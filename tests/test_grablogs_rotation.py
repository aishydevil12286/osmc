# -*- coding: utf-8 -*-
"""
    Tests for grab-logs capping output and waiting on piped commands.

    Run with: python3 tests/test_grablogs_rotation.py
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from test_grablogs_streaming import load_grablogs

REPO_ROOT = Path(__file__).resolve().parent.parent
GRABLOGS = (REPO_ROOT / "package/mediacenter-addon-osmc/src/script.module.osmccommon"
                        "/resources/lib/osmccommon/grablogs.py")


class TestCap(unittest.TestCase):

    def setUp(self):
        self.grablogs = load_grablogs()
        self.work = tempfile.mkdtemp()
        self.output = os.path.join(self.work, 'uploadlog.txt')
        self.grablogs.TEMP_LOG_FILE = self.output
        self.grablogs.MAX_LOG_BYTES = 64 * 1024
        self.grablogs.TRUNCATION_NOTICE = (
            '\n[grab-logs truncated: output exceeded %d bytes]\n'
            % self.grablogs.MAX_LOG_BYTES
        )

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_output_is_capped(self):
        source = os.path.join(self.work, 'big.log')
        with open(source, 'w', encoding='utf-8') as f:
            f.write('x' * (200 * 1024))

        self.grablogs.SETS = {
            'set': {
                'order': 1, 'active': True, 'help': '', 'dest': 'set',
                'action': 'store_true', 'flags': ['-x'],
                'logs': [{'name': 'big.log', 'key': 'KEY00009',
                          'ltyp': 'file_log', 'actn': source}],
            },
        }
        self.grablogs.right_now = lambda raw=False: 'FIXED-TIMESTAMP'
        main = self.grablogs.Main(False, False)
        self.assertTrue(main.open_temp_file())
        try:
            main.write_content_index()
            main.process_logs()
        finally:
            main.close_temp_file()

        size = os.path.getsize(self.output)
        self.assertLessEqual(size, self.grablogs.MAX_LOG_BYTES)
        with open(self.output, 'rb') as f:
            data = f.read()
        self.assertIn(b'truncated', data)

    def test_piped_command_waits_and_returns_output(self):
        cli = self.grablogs.CommandLine(['/usr/bin/printf', 'hello', '|', '/bin/cat'])
        self.assertEqual(cli.readlines(), 'hello')

    def test_no_os_popen_and_no_dummy_popen(self):
        source = GRABLOGS.read_text()
        self.assertNotIn('os.popen', source)
        self.assertNotIn("Popen((''), shell=True)", source)
        self.assertIn('MAX_LOG_BYTES', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
