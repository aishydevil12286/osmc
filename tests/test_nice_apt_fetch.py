# -*- coding: utf-8 -*-
"""
    Tests for launching the apt helper at idle priority.

    Run with: python3 tests/test_nice_apt_fetch.py
"""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                      "/script.module.osmcsetting.updates/resources/lib"
                      "/osmcupdates/service_entry.py")


class TestNiceApt(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()

    def test_helper_is_niced(self):
        self.assertIn("'nice', '-n', '19'", self.source)
        self.assertIn("'ionice', '-c3'", self.source)

    def test_both_launch_sites_use_the_helper(self):
        self.assertIn('self._apt_helper_cmd(action)', self.source)
        self.assertIn("self._apt_helper_cmd('action_list', action)", self.source)
        self.assertNotIn(
            "['sudo', 'python3', os.path.join(self.lib_path, 'apt_cache_action.py')",
            self.source
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)
