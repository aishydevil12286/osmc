# -*- coding: utf-8 -*-
"""
    Tests that the Vero 5 watchdog does not walk all of ~/.kodi for
    guisettings.xml at every start.

    Run with: python3 tests/test_vero5_guisettings_find.py
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WATCHDOG = REPO_ROOT / "package/mediacenter-osmc/patches/vero5-watchdog"


class TestVero5GuiSettingsFind(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = WATCHDOG.read_text()

    def test_does_not_recursively_find(self):
        self.assertNotIn('find /home/osmc/.kodi', self.text)

    def test_looks_in_userdata_and_profiles(self):
        self.assertIn('/home/osmc/.kodi/userdata/guisettings.xml', self.text)
        self.assertIn('/home/osmc/.kodi/userdata/profiles/*/guisettings.xml', self.text)

    def test_parses(self):
        result = subprocess.run(['bash', '-n', str(WATCHDOG)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
