# -*- coding: utf-8 -*-
"""
    Tests for the daily free-space check actually running.

    automatic_freespace_checker was gated on freespace_supressor > 172800,
    with the counter starting at 172200 and never being incremented, so
    the check was dead. It is now wall-clock based: first run five
    minutes after start, then daily.

    Run with: python3 tests/test_freespace_checker.py
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                      "/script.module.osmcsetting.updates/resources/lib"
                      "/osmcupdates/service_entry.py")


class TestFreespaceChecker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_tick_counter_is_gone(self):
        self.assertNotIn('freespace_supressor', self.source)

    def test_uses_wall_clock(self):
        self.assertIn('freespace_first_check_after', self.source)
        self.assertIn('freespace_interval', self.source)
        self.assertIn('last_freespace_check', self.source)
        self.assertIn('time.time()', self.source)

    def test_first_check_is_five_minutes(self):
        self.assertIn('time.time() + 300', self.source)

    def test_interval_is_a_day(self):
        self.assertIn('self.freespace_interval = 86400', self.source)

    def test_apt_clean_is_a_real_shell_command(self):
        # The old argv treated && as a literal apt-get argument.
        self.assertNotIn("['sudo', 'apt-get', 'autoremove', '&&'", self.source)
        self.assertIn("apt-get autoremove && apt-get clean", self.source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
