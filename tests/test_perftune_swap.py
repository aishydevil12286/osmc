# -*- coding: utf-8 -*-
"""
    Tests for creating the low-RAM swap file without writing 128MB of zeros.

    Run with: python3 tests/test_perftune_swap.py
"""

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PERFTUNE = REPO_ROOT / "package/perftune-osmc/files/usr/bin/performance_tuner"


class TestSwapCreation(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = PERFTUNE.read_text()

    def test_prefers_fallocate(self):
        self.assertIn('fallocate -l 128M /swap', self.text)

    def test_dd_is_only_the_fallback(self):
        self.assertIn('dd if=/dev/zero of=/swap bs=1M count=128', self.text)
        self.assertLess(
            self.text.index('fallocate -l 128M /swap'),
            self.text.index('dd if=/dev/zero of=/swap bs=1M count=128')
        )

    def test_parses(self):
        result = subprocess.run(['sh', '-n', str(PERFTUNE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
