# -*- coding: utf-8 -*-
"""
    Regression tests for systemd service sandboxing.

    Background: 'osmc' holds passwordless sudo (see setup_osmc_user in
    filesystem/common/funcs.sh):

        osmc     ALL= NOPASSWD: ALL

    Any service running as that user therefore reaches root with no
    escalation step. For network-facing daemons that parse untrusted remote
    input - transmission (BitTorrent peers, DHT) and tvheadend (HTTP UI/API)
    - that turns a remote code execution bug directly into root.

    NoNewPrivileges=true sets the kernel no_new_privs bit, which stops
    setuid binaries (sudo among them) from elevating. It costs no memory or
    CPU, which matters because OSMC targets low-power hardware down to the
    Pi Zero.

    Run with: python3 tests/test_service_hardening.py
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "package"

TRANSMISSION_UNIT = (
    PACKAGE_ROOT / "transmission-app-osmc/files/lib/systemd/system/transmission.service"
)
TVHEADEND_UNIT = (
    PACKAGE_ROOT / "tvheadend-app-osmc/files/lib/systemd/system/tvheadend.service"
)

# Units that run as 'osmc' but deliberately do NOT set NoNewPrivileges.
# Each entry must carry a reason. Adding to this list is a security
# decision and should be argued in review, not done silently.
NNP_EXEMPT = {
    "udisks-glue.service":
        "ExecStartPre runs 'sudo rmdir /media/*/'; NoNewPrivileges would "
        "break it. Not network-facing - it is a local disk automount helper, "
        "so it does not process untrusted remote input.",
}

# Core sandboxing expected on any network-facing daemon we harden.
CORE_DIRECTIVES = [
    "NoNewPrivileges",
    "PrivateTmp",
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectControlGroups",
    "RestrictSUIDSGID",
    "RestrictNamespaces",
    "LockPersonality",
]


def parse_service_section(path):
    """Return the [Service] section of a unit file as a dict.

    OSMC unit files mix 'Key=Value' and 'Key = Value' styles, so both are
    normalised here.
    """
    settings = {}
    in_service = False

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()

        if line.startswith('#') or line.startswith(';') or not line:
            continue

        if line.startswith('['):
            in_service = line.lower() == '[service]'
            continue

        if not in_service or '=' not in line:
            continue

        key, _, value = line.partition('=')
        settings[key.strip()] = value.strip()

    return settings


def iter_unit_files():
    """Every systemd unit shipped by a package (avahi .service files are a
    different format and are excluded)."""
    for path in PACKAGE_ROOT.rglob("*.service"):
        if "/avahi/" in str(path):
            continue
        yield path


def runs_as_osmc(settings):
    """True if the unit runs as, or drops privileges to, the osmc user."""
    if settings.get('User', '').strip() == 'osmc':
        return True

    # tvheadend drops privileges itself via '-u osmc'
    exec_start = settings.get('ExecStart', '')
    return bool(re.search(r'(^|\s)-u\s+osmc(\s|$)', exec_start))


class TestNoNewPrivilegesOnNetworkDaemons(unittest.TestCase):
    """The escalation chain (RCE -> osmc -> sudo -> root) must stay severed."""

    def test_transmission_sets_no_new_privileges(self):
        settings = parse_service_section(TRANSMISSION_UNIT)
        self.assertEqual(
            settings.get('NoNewPrivileges'), 'true',
            'transmission-daemon parses untrusted peer traffic as the '
            'sudo-privileged osmc user; without NoNewPrivileges an RCE is root'
        )

    def test_tvheadend_sets_no_new_privileges(self):
        settings = parse_service_section(TVHEADEND_UNIT)
        self.assertEqual(
            settings.get('NoNewPrivileges'), 'true',
            'tvheadend serves HTTP and drops to the sudo-privileged osmc user'
        )

    def test_core_sandboxing_directives_present(self):
        for unit in (TRANSMISSION_UNIT, TVHEADEND_UNIT):
            settings = parse_service_section(unit)
            for directive in CORE_DIRECTIVES:
                with self.subTest(unit=unit.name, directive=directive):
                    self.assertEqual(
                        settings.get(directive), 'true',
                        '%s missing from %s' % (directive, unit.name)
                    )


class TestDirectivesThatWouldBreakService(unittest.TestCase):
    """Guards against 'helpful' hardening that breaks these services.

    These are the directives that look obviously correct but are wrong here,
    so they are asserted absent rather than merely left unset.
    """

    def test_transmission_does_not_protect_home(self):
        settings = parse_service_section(TRANSMISSION_UNIT)
        self.assertNotIn(
            'ProtectHome', settings,
            'transmission keeps its config and downloads under /home/osmc; '
            'ProtectHome would break it'
        )

    def test_transmission_protect_system_is_not_strict(self):
        settings = parse_service_section(TRANSMISSION_UNIT)
        self.assertNotEqual(
            settings.get('ProtectSystem'), 'strict',
            "ProtectSystem=strict makes the whole tree read-only, breaking "
            "download targets on removable media under /media"
        )

    def test_tvheadend_does_not_set_private_devices(self):
        settings = parse_service_section(TVHEADEND_UNIT)
        self.assertNotIn(
            'PrivateDevices', settings,
            'tvheadend needs DVB adapters under /dev/dvb; PrivateDevices '
            'would hide them'
        )


class TestOsmcUserUnitsPolicy(unittest.TestCase):
    """Forward-looking policy: any unit running as the sudo-privileged osmc
    user must set NoNewPrivileges, or be explicitly exempted with a reason."""

    def test_all_osmc_units_set_nnp_or_are_exempt(self):
        offenders = []

        for path in iter_unit_files():
            settings = parse_service_section(path)

            if not runs_as_osmc(settings):
                continue

            if settings.get('NoNewPrivileges') == 'true':
                continue

            if path.name in NNP_EXEMPT:
                continue

            offenders.append(path.name)

        self.assertEqual(
            offenders, [],
            'These units run as the sudo-privileged osmc user without '
            'NoNewPrivileges and are not in NNP_EXEMPT: %s. Either harden '
            'them or add a documented exemption.' % offenders
        )

    def test_exemptions_all_carry_a_reason(self):
        for unit, reason in NNP_EXEMPT.items():
            with self.subTest(unit=unit):
                self.assertTrue(
                    reason and len(reason) > 40,
                    'Exemption for %s needs a substantive reason' % unit
                )

    def test_exemptions_are_not_stale(self):
        """An exemption for a unit that no longer exists, or that now sets
        NoNewPrivileges, should be removed."""
        names = {p.name: p for p in iter_unit_files()}

        for unit in NNP_EXEMPT:
            with self.subTest(unit=unit):
                self.assertIn(unit, names,
                               'Exempted unit %s no longer exists' % unit)
                settings = parse_service_section(names[unit])
                self.assertNotEqual(
                    settings.get('NoNewPrivileges'), 'true',
                    '%s now sets NoNewPrivileges; drop the exemption' % unit
                )


@unittest.skipUnless(shutil.which('systemd-analyze'),
                     'systemd-analyze not available')
class TestExposureScores(unittest.TestCase):
    """systemd-analyze scores a unit's sandboxing 0 (safest) to 10 (unsafe).

    Both units scored UNSAFE before hardening: transmission 9.0, tvheadend
    9.4. Thresholds below leave headroom for scoring differences between
    systemd versions while still catching a regression.
    """

    MAX_EXPOSURE = {
        TRANSMISSION_UNIT: 7.0,   # measured 5.8 on systemd 255
        TVHEADEND_UNIT: 8.5,      # measured 7.3 on systemd 255
    }

    @staticmethod
    def exposure_of(unit_path):
        result = subprocess.run(
            ['systemd-analyze', 'security', '--offline=true', str(unit_path)],
            capture_output=True, text=True,
        )
        match = re.search(r'Overall exposure level for \S+:\s*([0-9.]+)',
                          result.stdout)
        return float(match.group(1)) if match else None

    def test_exposure_below_threshold(self):
        for unit, threshold in self.MAX_EXPOSURE.items():
            with self.subTest(unit=unit.name):
                score = self.exposure_of(unit)
                if score is None:
                    self.skipTest('could not parse exposure for %s '
                                  '(systemd too old for --offline?)' % unit.name)
                self.assertLess(
                    score, threshold,
                    '%s exposure %.1f regressed above %.1f'
                    % (unit.name, score, threshold)
                )


if __name__ == '__main__':
    unittest.main(verbosity=2)
