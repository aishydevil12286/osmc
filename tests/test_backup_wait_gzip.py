# -*- coding: utf-8 -*-
"""
    Tests for backup helpers waiting on subprocesses and using faster gzip.

    mkdir/cp/mv were fired with Popen and never waited on, so the next
    step could race a missing directory. tarfile used gzip's default
    level 9 on ARM, and every member paid a 150ms sleep.

    Run with: python3 tests/test_backup_wait_gzip.py
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                      "/script.module.osmcsetting.updates/resources/lib"
                      "/osmcupdates/osmc_backups.py")


class TestBackupWaits(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_no_fire_and_forget_popen(self):
        self.assertNotIn('subprocess.Popen', self.source)

    def test_uses_check_call(self):
        self.assertIn('subprocess.check_call', self.source)

    def test_gzip_is_not_maximum_effort(self):
        self.assertIn('compresslevel=3', self.source)

    def test_per_member_sleep_is_conditional(self):
        self.assertNotIn('xbmc.sleep(150)  # sleep for 150ms to resolve invalid FileNotFound',
                         self.source)
        self.assertIn('if not os.path.exists(name):', self.source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
