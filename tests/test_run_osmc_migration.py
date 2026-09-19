# -*- coding: utf-8 -*-
"""
    Tests for the /tmp -> /run/osmc migration of cross-process coordination
    flags.

    Background: OSMC coordinates between privileged code (package maintainer
    scripts, the first-time-run scripts) and the Kodi settings addons
    (running as 'osmc') through flag files. Those lived in /tmp, which is
    world-writable and - the reason this migration exists - is given a
    private mount by any unit setting PrivateTmp=. As long as coordination
    runs through /tmp, mediacenter.service cannot be sandboxed that way.

    Writers now go through /usr/bin/osmc-runtime-flag, which writes to
    /run/osmc and mirrors to the legacy /tmp path so that addon code from
    before the upgrade - still loaded in the running Kodi process - keeps
    working until Kodi restarts.

    Unlike the other suites here, the helper tests execute the real script.

    Run with: python3 tests/test_run_osmc_migration.py
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "package"

BASE_FILES = PACKAGE_ROOT / "base-files-osmc/files"
HELPER = BASE_FILES / "usr/bin/osmc-runtime-flag"
TMPFILES_CONF = BASE_FILES / "usr/lib/tmpfiles.d/osmc.conf"
BASE_POSTINST = BASE_FILES / "DEBIAN/postinst"

FTR_DIR = PACKAGE_ROOT / "ftr-osmc/src"
UPDATES_SERVICE_ENTRY = (
    PACKAGE_ROOT
    / "mediacenter-addon-osmc/src/script.module.osmcsetting.updates"
      "/resources/lib/osmcupdates/service_entry.py"
)
WALKTHRU = (
    PACKAGE_ROOT
    / "mediacenter-addon-osmc/src/service.osmc.settings"
      "/resources/lib/osmcsettings/osmc_walkthru.py"
)


def maintainer_scripts():
    for name in ("postinst", "preinst", "prerm", "postrm"):
        yield from PACKAGE_ROOT.glob("*/files/DEBIAN/%s" % name)


class TestRuntimeFlagHelper(unittest.TestCase):
    """Functional tests - these run the real script."""

    def run_helper(self, *args, rundir=None):
        env = dict(os.environ)
        if rundir is not None:
            env['OSMC_RUN_DIR'] = str(rundir)
        return subprocess.run(
            ['sh', str(HELPER), *args],
            capture_output=True, text=True, env=env,
        )

    def test_helper_is_executable(self):
        self.assertTrue(os.access(HELPER, os.X_OK),
                        'osmc-runtime-flag must ship with the exec bit set')

    def test_helper_shell_syntax_is_valid(self):
        result = subprocess.run(['sh', '-n', str(HELPER)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_set_creates_flag_and_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            rundir = Path(tmp) / 'osmc'
            result = self.run_helper('set', 'reboot-needed', rundir=rundir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((rundir / 'reboot-needed').is_file(),
                            'flag not created in the run directory')

    def test_clear_removes_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            rundir = Path(tmp) / 'osmc'
            self.run_helper('set', 'reboot-needed', rundir=rundir)
            result = self.run_helper('clear', 'reboot-needed', rundir=rundir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((rundir / 'reboot-needed').exists())

    def test_clear_on_missing_flag_succeeds(self):
        """Maintainer scripts run under 'set -e' in places; clearing an
        already-absent flag must not fail the caller."""
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_helper('clear', 'reboot-needed',
                                     rundir=Path(tmp) / 'osmc')
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_path_reports_run_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            rundir = Path(tmp) / 'osmc'
            result = self.run_helper('path', 'reboot-needed', rundir=rundir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             str(rundir / 'reboot-needed'))

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ('../escape', 'a/b', '..', ''):
                with self.subTest(flag=bad):
                    result = self.run_helper('set', bad,
                                             rundir=Path(tmp) / 'osmc')
                    self.assertNotEqual(
                        result.returncode, 0,
                        'helper accepted unsafe flag name %r' % bad
                    )

    def test_rejects_unknown_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_helper('frobnicate', 'reboot-needed',
                                     rundir=Path(tmp) / 'osmc')
            self.assertNotEqual(result.returncode, 0)

    def test_known_flags_have_legacy_mirror(self):
        """Both migrated flags must keep a legacy /tmp mapping until the
        compatibility window closes."""
        text = HELPER.read_text()
        self.assertIn('/tmp/reboot-needed', text)
        self.assertIn('/tmp/NO_UPDATE', text)


class TestTmpfilesConfig(unittest.TestCase):

    def test_tmpfiles_conf_exists(self):
        self.assertTrue(TMPFILES_CONF.is_file())

    def test_creates_run_osmc_group_writable_by_osmc(self):
        line = None
        for raw in TMPFILES_CONF.read_text().splitlines():
            if raw.strip().startswith('d '):
                line = raw.split()
                break

        self.assertIsNotNone(line, 'no directory entry in osmc.conf')
        self.assertEqual(line[1], '/run/osmc')
        # Both root (maintainer scripts) and osmc (the addons) create and
        # remove entries, so group write is required, not optional.
        self.assertEqual(line[2], '0770')
        self.assertEqual(line[3], 'root')
        self.assertEqual(line[4], 'osmc')

    def test_postinst_creates_directory_without_waiting_for_reboot(self):
        self.assertIn('systemd-tmpfiles --create', BASE_POSTINST.read_text())


class TestWritersMigrated(unittest.TestCase):
    """No writer may set these flags by touching /tmp directly."""

    RAW_REBOOT = re.compile(r'touch\s+(\S+\s+)*/tmp/reboot-needed')
    RAW_NO_UPDATE = re.compile(r'touch\s+(\S+\s+)*/tmp/NO_UPDATE')

    def test_no_maintainer_script_touches_tmp_reboot_needed_directly(self):
        offenders = []
        for path in maintainer_scripts():
            for num, line in enumerate(path.read_text().splitlines(), 1):
                if not self.RAW_REBOOT.search(line):
                    continue
                # The documented fallback for when base-files-osmc has not
                # been configured yet is allowed.
                if 'osmc-runtime-flag set reboot-needed' in line:
                    continue
                offenders.append('%s:%d' % (path, num))

        self.assertEqual(
            offenders, [],
            'These writers set reboot-needed in /tmp without going through '
            'osmc-runtime-flag: %s' % offenders
        )

    def test_all_ftr_variants_use_the_helper(self):
        variants = sorted(FTR_DIR.glob('*-ftr'))
        self.assertTrue(variants, 'no ftr variants found')

        for path in variants:
            with self.subTest(variant=path.name):
                text = path.read_text()
                self.assertIn('osmc-runtime-flag set no-update', text,
                              '%s still sets NO_UPDATE directly' % path.name)

    def test_migrated_shell_scripts_still_parse(self):
        scripts = sorted(FTR_DIR.glob('*-ftr'))
        scripts += [p for p in maintainer_scripts()
                    if 'osmc-runtime-flag' in p.read_text()]
        self.assertTrue(scripts)

        for path in scripts:
            with self.subTest(script=str(path)):
                shell = 'bash' if path.read_text().startswith('#!/bin/bash') else 'sh'
                result = subprocess.run([shell, '-n', str(path)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class TestReadersMigrated(unittest.TestCase):

    def test_reboot_flag_list_covers_run_osmc_and_legacy(self):
        text = UPDATES_SERVICE_ENTRY.read_text()
        match = re.search(r'REBOOT_REQUIRED_FILES = \[(.*?)\]', text, re.S)
        self.assertIsNotNone(match, 'REBOOT_REQUIRED_FILES not defined')

        entries = match.group(1)
        self.assertIn('/run/osmc/reboot-needed', entries)
        self.assertIn('/var/run/reboot-required', entries,
                      "Debian's own reboot flag must still be honoured")
        self.assertIn('/tmp/reboot-needed', entries,
                      'legacy location must stay until the compat window closes')

    def test_reboot_checks_use_the_shared_list(self):
        text = UPDATES_SERVICE_ENTRY.read_text()
        # Two call sites previously inlined their own path lists.
        self.assertEqual(
            text.count('os.path.isfile(x) for x in REBOOT_REQUIRED_FILES'), 2,
            'both reboot checks should read the shared list'
        )

    def test_dead_reboot_check_typo_is_gone(self):
        """One call site checked a relative path, 'fname/var/run/...', so
        that half of the condition could never be true."""
        self.assertNotIn('fname/var/run/reboot-required',
                         UPDATES_SERVICE_ENTRY.read_text())

    def test_walkthru_clears_both_no_update_locations(self):
        text = WALKTHRU.read_text()
        self.assertIn('/run/osmc/no-update', text)
        self.assertIn('/tmp/NO_UPDATE', text)
        self.assertIn("'-f'", text,
                      "rm needs -f now that one of the two paths is "
                      "expected to be absent")


if __name__ == '__main__':
    unittest.main(verbosity=2)
