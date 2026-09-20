# -*- coding: utf-8 -*-
"""
    Tests for coalescing apt progress IPC.

    apt_cache_action.call_parent opened a new Unix socket for every
    progress pulse, and DownloadProgress.pulse printed a full dump each
    time. The parent reads to EOF, so a persistent connection would
    stall the listener; duplicate pulses within a second are dropped
    instead.

    Run with: python3 tests/test_apt_progress_coalesce.py
"""

import json
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                      "/script.module.osmcsetting.updates/resources/lib"
                      "/osmcupdates/apt_cache_action.py")
COMMS = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                     "/script.module.osmccommon/resources/lib"
                     "/osmccommon/osmc_comms.py")


def load_apt():
    import importlib.util
    spec = importlib.util.spec_from_file_location('apt_cache_action', MODULE)
    module = importlib.util.module_from_spec(spec)
    # The file imports apt at module level; stub it.
    import sys
    import types
    sys.modules.setdefault('apt', types.ModuleType('apt'))
    sys.modules['apt'].progress = types.SimpleNamespace(
        base=types.SimpleNamespace(
            OpProgress=object,
            InstallProgress=object,
            AcquireProgress=object,
        )
    )
    spec.loader.exec_module(module)
    return module


class FakeSocket:
    def __init__(self):
        self.sent = []

    def connect(self, path):
        self.path = path

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TestCoalesce(unittest.TestCase):

    def setUp(self):
        self.mod = load_apt()
        self.mod._last_progress.update({
            'when': None, 'percent': None, 'heading': None, 'message': None,
        })
        self.sock = FakeSocket()

    def send(self, **data):
        with mock.patch.object(self.mod.socket, 'socket', return_value=self.sock):
            return self.mod.call_parent('progress_bar', data)

    def test_first_pulse_is_sent(self):
        result = self.send(percent=10, heading='h', message='m')
        self.assertEqual(result, 'response sent')
        self.assertEqual(len(self.sock.sent), 1)
        action, payload = json.loads(self.sock.sent[0].decode('utf-8'))
        self.assertEqual(action, 'progress_bar')
        self.assertEqual(payload['percent'], 10)

    def test_duplicate_pulse_is_dropped(self):
        self.send(percent=10, heading='h', message='m')
        result = self.send(percent=10, heading='h', message='m')
        self.assertEqual(result, 'coalesced')
        self.assertEqual(len(self.sock.sent), 1)

    def test_percent_change_is_sent(self):
        self.send(percent=10, heading='h', message='m')
        self.send(percent=11, heading='h', message='m')
        self.assertEqual(len(self.sock.sent), 2)

    def test_completion_messages_are_not_progress_coalesced(self):
        with mock.patch.object(self.mod.socket, 'socket', return_value=self.sock):
            self.mod.call_parent('apt_cache fetch complete', {})
            self.mod.call_parent('apt_cache fetch complete', {})
        self.assertEqual(len(self.sock.sent), 2)


class TestPulseSpamGone(unittest.TestCase):

    def test_pulse_does_not_print_a_dump(self):
        source = MODULE.read_text()
        self.assertNotIn('Pulse ===========================================', source)
        self.assertNotIn("print('%s %s sending response'", source)

    def test_listener_backlog_is_raised(self):
        source = COMMS.read_text()
        self.assertIn('self.sock.listen(8)', source)
        self.assertNotIn('self.sock.listen(1)', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
