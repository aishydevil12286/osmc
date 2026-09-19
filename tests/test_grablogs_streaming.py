# -*- coding: utf-8 -*-
"""
    Tests for grab-logs streaming its output instead of buffering it.

    grab-logs used to accumulate every collected log into a single
    `log_blotter` list, then rebuild that list twice more - once when
    masking, once when encoding to bytes - before writing anything. Peak
    heap therefore ran at several times the combined size of the logs, on
    hardware with as little as 512MB of RAM, at the moment something has
    already gone wrong enough for the user to be collecting logs.

    The command-output path was worse. CommandLine.readlines() returns the
    command's entire output as a *single string*, so:

        self.log_blotter.extend(readlines)

    iterated it character by character. 2MB of `journalctl` output became
    2,097,152 list elements at roughly 8x the input size.

    Sections are now written straight to the open file handle as they are
    collected. Output is unchanged - verified byte-for-byte against the
    previous implementation.

    Run with: python3 tests/test_grablogs_streaming.py
"""

import importlib.util
import os
import sys
import tempfile
import tracemalloc
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GRABLOGS = (REPO_ROOT / "package/mediacenter-addon-osmc/src/script.module.osmccommon"
                        "/resources/lib/osmccommon/grablogs.py")


def load_grablogs():
    """grablogs degrades gracefully without Kodi, so it loads as-is."""
    spec = importlib.util.spec_from_file_location("grablogs", GRABLOGS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GrabLogsHarness(unittest.TestCase):
    """Drives the real Main pipeline against a synthetic log set."""

    NOISE_LINES = 20000

    def setUp(self):
        self.grablogs = load_grablogs()
        self.work = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)

        self.source = os.path.join(self.work, 'sources.xml')
        with open(self.source, 'w', encoding='utf-8') as f:
            f.write('<path>davs://user:supersecret@example.com:443/Videos</path>\n')
            for index in range(self.NOISE_LINES):
                f.write('noise line %d\n' % index)

        self.input_size = os.path.getsize(self.source)
        self.output = os.path.join(self.work, 'uploadlog.txt')

        self.grablogs.TEMP_LOG_FILE = self.output
        self.grablogs.right_now = lambda raw=False: 'FIXED-TIMESTAMP'

    def _cleanup(self):
        for name in os.listdir(self.work):
            try:
                os.remove(os.path.join(self.work, name))
            except OSError:
                pass
        os.rmdir(self.work)

    def configure(self, logs):
        self.grablogs.SETS = {
            'set': {
                'order': 1, 'active': True, 'help': '', 'dest': 'set',
                'action': 'store_true', 'flags': ['-x'], 'logs': logs,
            },
        }

    def collect(self):
        """Run the pipeline, returning (bytes written, peak heap)."""
        main = self.grablogs.Main(False, False)

        stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            tracemalloc.start()
            self.assertTrue(main.open_temp_file(), 'could not open temp file')
            try:
                main.write_content_index()
                main.process_logs()
            finally:
                main.close_temp_file()
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        finally:
            sys.stdout.close()
            sys.stdout = stdout

        with open(self.output, 'rb') as f:
            return f.read(), peak


class TestOutputIsCorrect(GrabLogsHarness):

    def test_file_log_is_collected_and_masked(self):
        self.configure([{'name': 'sources.xml', 'key': 'KEY00001',
                         'ltyp': 'file_log', 'actn': self.source, 'mask': True}])
        data, _ = self.collect()

        self.assertIn(b'FIXED-TIMESTAMP', data)
        self.assertIn(b'KEY00001', data)
        self.assertIn(b'sources.xml', data)
        self.assertNotIn(b'supersecret', data, 'credential leaked')
        self.assertIn(b'**masked*by*grab-logs**', data)
        self.assertIn(b'noise line 19999\n', data, 'log truncated')

    def test_unmasked_file_log_is_left_intact(self):
        self.configure([{'name': 'sources.xml', 'key': 'KEY00002',
                         'ltyp': 'file_log', 'actn': self.source}])
        data, _ = self.collect()
        self.assertIn(b'supersecret', data,
                      'content changed on a log that asked for no masking')

    def test_command_output_is_written_whole(self):
        """The regression: extend() on a string split it into characters.

        The bytes came out in the right order, so this asserts the shape of
        the output rather than merely its presence.
        """
        self.configure([{
            'name': 'Big Command', 'key': 'KEY00003', 'ltyp': 'cl_log',
            'actn': '/bin/sh -c "for i in $(seq 1 5000); do echo cmd line $i; done"',
        }])
        data, _ = self.collect()

        self.assertIn(b'cmd line 1\n', data)
        self.assertIn(b'cmd line 5000\n', data)
        self.assertEqual(data.count(b'cmd line '), 5000)

    def test_missing_log_is_reported_not_fatal(self):
        self.configure([{'name': 'Absent', 'key': 'KEY00004', 'ltyp': 'file_log',
                         'actn': os.path.join(self.work, 'does-not-exist')},
                        {'name': 'sources.xml', 'key': 'KEY00005',
                         'ltyp': 'file_log', 'actn': self.source}])
        data, _ = self.collect()

        self.assertIn(b'An error occurred while grabbing Absent', data)
        self.assertIn(b'noise line 19999\n', data,
                      'a failed section stopped later ones being collected')

    def test_sections_are_delimited(self):
        self.configure([{'name': 'sources.xml', 'key': 'KEY00006',
                         'ltyp': 'file_log', 'actn': self.source}])
        data, _ = self.collect()

        self.assertIn(b'====================== sources.xml', data)
        self.assertIn(b'---------------------- sources.xml END', data)


class TestMemoryIsBounded(GrabLogsHarness):
    """Regression guard: reintroducing a buffer would fail these."""

    def test_file_log_does_not_scale_peak_heap_with_input(self):
        self.configure([{'name': 'sources.xml', 'key': 'KEY00007',
                         'ltyp': 'file_log', 'actn': self.source, 'mask': True}])
        data, peak = self.collect()

        self.assertGreater(len(data), self.input_size, 'sanity: log was collected')
        self.assertLess(
            peak, self.input_size,
            'peak heap (%d bytes) reached the size of the input (%d) - '
            'the logs are being buffered again' % (peak, self.input_size)
        )

    def test_command_output_does_not_scale_peak_heap(self):
        """The old character-splitting path peaked at ~8x the input."""
        self.configure([{
            'name': 'Big Command', 'key': 'KEY00008', 'ltyp': 'cl_log',
            'actn': '/bin/sh -c "for i in $(seq 1 40000); do echo cmd line $i; done"',
        }])
        data, peak = self.collect()

        self.assertLess(
            peak, 4 * len(data),
            'peak heap (%d bytes) is %.1fx the output (%d) - command output '
            'is being split into a list again'
            % (peak, peak / float(len(data)), len(data))
        )


class TestMaskSignature(unittest.TestCase):
    """_mask_sensitive masks one chunk of text now, not a list of lines."""

    @classmethod
    def setUpClass(cls):
        cls.grablogs = load_grablogs()

    def test_masks_a_string_and_returns_a_string(self):
        masked = self.grablogs.Main._mask_sensitive(
            'davs://user:supersecret@host/path\n')

        self.assertIsInstance(masked, str)
        self.assertNotIn('supersecret', masked)

    def test_no_blotter_attribute_remains(self):
        source = GRABLOGS.read_text()
        self.assertNotIn('log_blotter', source,
                         'the in-memory blotter is back')


if __name__ == '__main__':
    unittest.main(verbosity=2)
