# -*- coding: utf-8 -*-
"""
    Regression tests for the major-severity fixes bundled together:

    - #684  networking_gui.py could dereference/assign into
            current_network_config[protocol] while it was None or missing,
            raising "'NoneType' object is not subscriptable" when enabling
            the wired adapter.
    - #586  osmc_network.py sent stale static Address/Netmask alongside
            Method=dhcp/auto to connman, preventing WiFi from actually
            reacquiring a DHCP lease after a manual configuration.
    - #491  transmission.service had no shutdown timeout, so systemd could
            SIGKILL transmission-daemon mid-write on restart/reboot,
            corrupting downloaded data and the resume cache.
    - #465  apt_cache_action.py's DownloadProgress.fetching must stay
            initialized in __init__ so pulse() never reads an unset
            attribute.

    These modules pull in xbmc/xbmcgui/dbus, which aren't available outside
    a Kodi/OSMC runtime, so these are static source checks (consistent with
    tests/test_critical_fixes.py's approach for the uncompiled C++ fix)
    rather than executing the modules directly.

    Run with: python3 tests/test_major_fixes.py
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

NETWORKING_GUI_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.networking"
      "/resources/lib/osmcnetworking/networking_gui.py"
)
OSMC_NETWORK_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.networking"
      "/resources/lib/osmcnetworking/osmc_network.py"
)
TRANSMISSION_SERVICE_PATH = (
    REPO_ROOT / "package/transmission-app-osmc/files/lib/systemd/system/transmission.service"
)
APT_CACHE_ACTION_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.updates"
      "/resources/lib/osmcupdates/apt_cache_action.py"
)


class TestNetworkingGuiDeviceConfigGuard(unittest.TestCase):
    """Issue #684: update_current_ip_settings must not assign into a
    None/missing protocol or Nameservers entry."""

    @classmethod
    def setUpClass(cls):
        cls.text = NETWORKING_GUI_PATH.read_text()
        match = re.search(
            r"def update_current_ip_settings\(self, controls\):.*?(?=\n    def )",
            cls.text, re.S,
        )
        assert match, "update_current_ip_settings method not found"
        cls.method_body = match.group(0)

    def test_current_network_config_is_guarded(self):
        self.assertIn('if not self.current_network_config:', self.method_body)

    def test_protocol_entry_is_guarded_before_assignment(self):
        guard_idx = self.method_body.find(
            "if not self.current_network_config.get(self.internet_protocol):"
        )
        assign_idx = self.method_body.find(
            "self.current_network_config[self.internet_protocol]['Address'] = ip_address"
        )
        self.assertNotEqual(guard_idx, -1, 'Missing protocol-entry guard')
        self.assertNotEqual(assign_idx, -1, 'Missing Address assignment')
        self.assertLess(guard_idx, assign_idx,
                         'protocol entry is assigned into before being guarded')

    def test_nameservers_entry_is_guarded_before_assignment(self):
        guard_idx = self.method_body.find(
            "if not self.current_network_config.get('Nameservers'):"
        )
        assign_idx = self.method_body.find(
            "self.current_network_config['Nameservers']['DNS_1'] = primary_dns"
        )
        self.assertNotEqual(guard_idx, -1, "Missing Nameservers-entry guard")
        self.assertNotEqual(assign_idx, -1, "Missing DNS_1 assignment")
        self.assertLess(guard_idx, assign_idx,
                         'Nameservers entry is assigned into before being guarded')


class TestOsmcNetworkDhcpConfiguration(unittest.TestCase):
    """Issue #586: switching to DHCP/auto must not send stale static
    Address/Netmask/Gateway values to connman."""

    @classmethod
    def setUpClass(cls):
        cls.text = OSMC_NETWORK_PATH.read_text()
        match = re.search(
            r"def apply_network_changes\(settings_dict, internet_protocol\):.*?"
            r"(?=\ndef make_variant)",
            cls.text, re.S,
        )
        assert match, "apply_network_changes function not found"
        cls.func_body = match.group(0)

    def test_address_netmask_only_sent_for_manual_method(self):
        manual_guard_idx = self.func_body.find("if method == 'manual':")
        address_idx = self.func_body.find(
            "ipv4_configuration['Address'] = \\\n                make_variant"
        )
        self.assertNotEqual(manual_guard_idx, -1,
                             "No method == 'manual' guard found")
        self.assertNotEqual(address_idx, -1,
                             "Address assignment to ipv4_configuration not found")
        self.assertLess(manual_guard_idx, address_idx,
                         "Address is set outside the manual-only guard")

    def test_unconditional_stale_address_send_is_gone(self):
        # Regression guard: the old code built the whole dict (Method,
        # Address, Netmask) unconditionally before the method was checked.
        self.assertNotIn(
            "'Address': make_variant(settings_dict[internet_protocol]['Address'])",
            self.func_body,
            "Address is being sent unconditionally again, reopening #586",
        )


class TestTransmissionShutdownTimeout(unittest.TestCase):
    """Issue #491: transmission.service must give the daemon time to flush
    data before systemd kills it."""

    @classmethod
    def setUpClass(cls):
        cls.text = TRANSMISSION_SERVICE_PATH.read_text()

    def test_timeout_stop_sec_is_set(self):
        match = re.search(r'^TimeoutStopSec=(\d+)$', self.text, re.M)
        self.assertIsNotNone(match, 'TimeoutStopSec not set in transmission.service')
        self.assertGreaterEqual(int(match.group(1)), 30,
                                 'TimeoutStopSec too short to flush torrent data safely')

    def test_kill_signal_is_graceful(self):
        self.assertIn('KillSignal=SIGTERM', self.text)


class TestAptCacheActionFetchingInitialized(unittest.TestCase):
    """Issue #465: DownloadProgress.fetching must be initialized in
    __init__ before pulse() can read it."""

    @classmethod
    def setUpClass(cls):
        cls.text = APT_CACHE_ACTION_PATH.read_text()
        match = re.search(
            r"class DownloadProgress\(apt\.progress\.base\.AcquireProgress\):.*?"
            r"def __init__\(self, partial_heading='Downloading'\):(.*?)\n    def ",
            cls.text, re.S,
        )
        assert match, "DownloadProgress.__init__ not found"
        cls.init_body = match.group(1)

    def test_fetching_is_initialized_in_constructor(self):
        self.assertIn("self.fetching = ", self.init_body,
                       'self.fetching is no longer initialized in __init__, '
                       'reopening #465')


if __name__ == '__main__':
    unittest.main(verbosity=2)
