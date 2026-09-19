# -*- coding: utf-8 -*-
"""
    Regression tests for security-hardening fixes found during a review of
    the codebase for issues not already reported upstream:

    - services_gui.py: enable_service()/disable_service() built
      `os.system("sudo /bin/systemctl enable " + service)` from lines read
      out of /etc/osmc/apps.d/<service_name>. Any package (malicious,
      compromised, or just buggy) that drops shell metacharacters into
      that file gets them executed as root via sudo. Fixed to use
      subprocess.call() with a list of arguments (no shell).

    - osmc_network.py / osmc_wireless_agent.py: wifi_connect() wrote the
      WiFi passphrase in plaintext to a fixed, predictable path in the
      shared /tmp namespace (default permissions, readable by any local
      process) and only cleaned it up on the happy path, leaking the
      password to disk indefinitely on an unexpected exception. Fixed to
      use tempfile.mkstemp() (private, unpredictable, 0600) and a
      try/finally to guarantee cleanup.

    These modules pull in xbmc/xbmcgui/dbus, unavailable outside a
    Kodi/OSMC runtime, so these are static source checks (same approach as
    tests/test_critical_fixes.py and tests/test_major_fixes.py).

    Run with: python3 tests/test_security_hardening.py
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SERVICES_GUI_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.services"
      "/resources/lib/osmcservices/services_gui.py"
)
OSMC_NETWORK_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.networking"
      "/resources/lib/osmcnetworking/osmc_network.py"
)
WIRELESS_AGENT_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmcsetting.networking"
      "/resources/lib/osmcnetworking/osmc_wireless_agent.py"
)


class TestServiceGuiNoShellInjection(unittest.TestCase):
    """Service enable/disable must never build a shell string from
    attacker-influenceable data (lines from /etc/osmc/apps.d/*)."""

    @classmethod
    def setUpClass(cls):
        cls.text = SERVICES_GUI_PATH.read_text()

    def test_no_os_system_with_string_concatenation(self):
        self.assertNotIn(
            'os.system("sudo /bin/systemctl',
            self.text,
            'enable_service/disable_service went back to building a shell '
            'string with os.system() - reopens the command-injection bug',
        )

    def test_enable_service_uses_argument_list(self):
        match = re.search(
            r"def enable_service\(self, s_entry\):.*?(?=\n    def )",
            self.text, re.S,
        )
        self.assertIsNotNone(match, 'enable_service method not found')
        body = match.group(0)
        self.assertIn('subprocess.call(["sudo", "/bin/systemctl", "enable", service])',
                       body)

    def test_disable_service_uses_argument_list(self):
        match = re.search(
            r"def disable_service\(self, s_entry\):.*?(?=\n\n|\Z)",
            self.text, re.S,
        )
        self.assertIsNotNone(match, 'disable_service method not found')
        body = match.group(0)
        self.assertIn('subprocess.call(["sudo", "/bin/systemctl", "disable", service])',
                       body)


class TestWifiPassphraseNotLeaked(unittest.TestCase):
    """The WiFi passphrase must not be written to a fixed, predictable,
    world-readable path, and cleanup must be guaranteed."""

    @classmethod
    def setUpClass(cls):
        cls.text = OSMC_NETWORK_PATH.read_text()
        match = re.search(
            r"def wifi_connect\(path, password=None, ssid=None, script_base_path=None\):"
            r".*?\Z",
            cls.text, re.S,
        )
        assert match, "wifi_connect function not found"
        cls.func_body = match.group(0)

    def test_no_fixed_tmp_preseed_path(self):
        self.assertNotIn(
            "/tmp/preseed_data", self.func_body,
            'wifi_connect regressed to a fixed, predictable /tmp path for '
            'the passphrase file',
        )

    def test_uses_private_tempfile(self):
        self.assertIn('tempfile.mkstemp(', self.func_body)

    def test_cleanup_is_in_a_finally_block(self):
        try_idx = self.func_body.find('try:')
        finally_idx = self.func_body.find('finally:')
        cleanup_idx = self.func_body.find('os.remove(preseed_path)')
        self.assertNotEqual(try_idx, -1, 'wifi_connect is not wrapped in a try block')
        self.assertNotEqual(finally_idx, -1, 'No finally block for cleanup')
        self.assertNotEqual(cleanup_idx, -1, 'preseed file cleanup not found')
        self.assertLess(try_idx, finally_idx)
        self.assertLess(finally_idx, cleanup_idx,
                         'preseed file cleanup is not inside the finally block')

    def test_tempfile_import_present(self):
        self.assertIn('import tempfile', self.text)


class TestWirelessAgentReadsPassedPath(unittest.TestCase):
    """The agent script must read the passphrase from the path the caller
    gives it, not a hardcoded shared path."""

    @classmethod
    def setUpClass(cls):
        cls.text = WIRELESS_AGENT_PATH.read_text()

    def test_reads_path_from_argv(self):
        self.assertIn('argv()[2]', self.text,
                       'wireless agent no longer reads the preseed path from argv')

    def test_no_unconditional_hardcoded_path_read(self):
        # The old bug: `open("/tmp/preseed_data", ...)` was called
        # unconditionally whenever "fromfile" was passed, regardless of
        # which file the caller actually used.
        self.assertNotIn('open("/tmp/preseed_data"', self.text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
