# -*- coding: utf-8 -*-
"""
    Regression tests for the critical-severity fixes bundled together:

    - #422  grab-logs leaked WebDAV/basic-auth credentials because the
            masking regex only matched a single-character URI scheme.
    - #319  osmc-no-secure-path disabled sudo's secure_path entirely,
            weakening privilege-escalation protection.
    - #787  Installer language selection could proceed with an
            uninitialized/unmatched device pointer, crashing when a
            selected translation didn't resolve to a device.

    Run with: python3 tests/test_critical_fixes.py
"""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

GRABLOGS_PATH = (
    REPO_ROOT
    / "package/mediacenter-addon-osmc/src/script.module.osmccommon"
      "/resources/lib/osmccommon/grablogs.py"
)
FUNCS_SH_PATH = REPO_ROOT / "filesystem/common/funcs.sh"
LANGSELECTION_CPP_PATH = (
    REPO_ROOT / "installer/host/qt_host_installer/langselection.cpp"
)


def _load_grablogs():
    """Import grablogs.py standalone (it degrades gracefully without Kodi)."""
    spec = importlib.util.spec_from_file_location("grablogs", GRABLOGS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestGrabLogsCredentialMasking(unittest.TestCase):
    """Issue #422: grab-logs must actually redact credentials in sources.xml."""

    @classmethod
    def setUpClass(cls):
        cls.grablogs = _load_grablogs()

    def test_webdav_credentials_are_masked(self):
        line = '<path>davs://myuser:supersecret@example.com:443/Videos</path>\n'
        masked = self.grablogs.Main._mask_sensitive(line)

        self.assertNotIn('supersecret', masked,
                          'WebDAV password leaked past masking')
        self.assertIn('**masked*by*grab-logs**', masked)

    def test_common_multichar_schemes_are_masked(self):
        for scheme in ('http', 'https', 'ftp', 'smb', 'nfs', 'davs'):
            line = '%s://user:hunter2@host.example/path\n' % scheme
            masked = self.grablogs.Main._mask_sensitive(line)
            self.assertNotIn('hunter2', masked,
                              'Password leaked for scheme %r' % scheme)

    def test_regression_single_char_scheme_regex_is_gone(self):
        # The original bug used r'(\w://...' which can only ever match a
        # single-character scheme. Guard against that regressing.
        pattern = self.grablogs.RE_MASKS['07'].pattern
        self.assertNotRegex(
            pattern, r'^\(\\w://',
            'Basic-auth mask regex regressed to single-character scheme match'
        )


class TestSudoSecurePath(unittest.TestCase):
    """Issue #319: sudo secure_path must stay enabled with sbin dirs present."""

    @classmethod
    def setUpClass(cls):
        cls.text = FUNCS_SH_PATH.read_text()

    def test_secure_path_is_not_disabled(self):
        self.assertNotIn('!secure_path', self.text,
                          'secure_path is disabled again, reopening #319')

    def test_secure_path_is_defined_with_sbin_dirs(self):
        match = re.search(r'secure_path=\\"([^\\"]+)\\"', self.text)
        self.assertIsNotNone(match, 'No secure_path definition found')
        paths = match.group(1).split(':')
        self.assertIn('/sbin', paths)
        self.assertIn('/usr/sbin', paths)


class TestLangSelectionDeviceGuard(unittest.TestCase):
    """Issue #787: language selection must not use an unmatched/uninitialized
    SupportedDevice pointer."""

    @classmethod
    def setUpClass(cls):
        cls.text = LANGSELECTION_CPP_PATH.read_text()

    def test_device_pointer_is_initialized(self):
        self.assertIn('SupportedDevice *device = nullptr;', self.text,
                       'device pointer is no longer defensively initialized')

    def test_null_device_is_guarded_before_dereference(self):
        # The null-check must appear before any '*device' dereference
        # (dereferences occur as the `*device)` argument to emit calls;
        # excluded here are declarations like `*device = nullptr`).
        null_check_idx = self.text.find('if (device == nullptr)')
        deref_idx = self.text.find('*device)')
        self.assertNotEqual(null_check_idx, -1, 'Missing device null-check')
        self.assertNotEqual(deref_idx, -1, 'No *device dereference found')
        self.assertLess(null_check_idx, deref_idx,
                         'device is dereferenced before being null-checked')


if __name__ == '__main__':
    unittest.main(verbosity=2)
