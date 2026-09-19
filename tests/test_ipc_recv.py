# -*- coding: utf-8 -*-
"""
    Tests for the Communicator receive loop in osmccommon/osmc_comms.py.

    The loop previously set the connection non-blocking and busy-polled
    recv() in 5ms increments, taking the *first* chunk as the whole message:

        conn.setblocking(False)
        while not passed and total_wait < 0.5:
            try:
                data = conn.recv(8192)     # assigns, never accumulates
            except:
                total_wait += wait         # wait = 0.005
                ...
            passed = True                  # exits on the first chunk

    Two problems. Up to 100 spin iterations per message instead of one
    blocking read, and - despite the comment above it claiming the loop
    "will allow the loop to collect all parts of the message" - any message
    arriving in more than one chunk was silently truncated, so the JSON
    failed to parse and the action was dropped.

    Every sender calls sendall() then closes, so reading to EOF with a
    socket timeout fixes both at once.

    These tests drive the real Communicator over real Unix sockets. Only
    `xbmc` is stubbed, since osmccommon itself has no import side effects.

    Run with: python3 tests/test_ipc_recv.py
"""

import json
import os
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path

try:
    import queue as Queue
except ImportError:
    import Queue

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = (REPO_ROOT / "package/mediacenter-addon-osmc/src/script.module.osmccommon"
                   "/resources/lib")


class StubMonitor(object):
    """Counts waitForAbort calls so the absence of a busy-wait is testable."""

    def __init__(self):
        self.wait_calls = 0

    def abortRequested(self):
        return False

    def waitForAbort(self, timeout=0):
        self.wait_calls += 1
        return False


def install_xbmc_stub():
    xbmc = types.ModuleType('xbmc')
    xbmc.Monitor = StubMonitor
    # osmc_logging imports xbmc opportunistically and calls xbmc.log with
    # these levels, so the stub has to carry the whole set.
    xbmc.LOGDEBUG = 0
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    xbmc.LOGFATAL = 4
    xbmc.LOGNONE = 5
    xbmc.log = lambda *args, **kwargs: None
    sys.modules['xbmc'] = xbmc


install_xbmc_stub()
sys.path.insert(0, str(LIB))

from osmccommon import osmc_comms  # noqa: E402


class CommunicatorHarness(unittest.TestCase):

    def setUp(self):
        # delete_sockfile shells out to `sudo rm`; keep the tests hermetic.
        self._real_call = osmc_comms.subprocess.call
        osmc_comms.subprocess.call = lambda *a, **kw: 0
        self.addCleanup(self._restore_subprocess)

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup_tmp)

        self.sock_path = os.path.join(self.tmp, 'test.sock')
        self.queue = Queue.Queue()

        self.comms = osmc_comms.Communicator(self.queue, socket_file=self.sock_path)
        self.monitor = self.comms.monitor
        self.comms.start()
        self.addCleanup(self._stop_comms)

    def _restore_subprocess(self):
        osmc_comms.subprocess.call = self._real_call

    def _stop_comms(self):
        try:
            self.comms.stop()
            self.comms.join(timeout=5)
        except Exception:
            pass

    def _cleanup_tmp(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        os.rmdir(self.tmp)

    def send(self, payload, chunked=False):
        """Send bytes to the listener and close, as every real sender does."""
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(self.sock_path)
            if chunked:
                # Force multiple reads on the server side.
                for start in range(0, len(payload), 1024):
                    client.sendall(payload[start:start + 1024])
            else:
                client.sendall(payload)

    def received(self, timeout=5):
        return self.queue.get(timeout=timeout)


class TestMessagesAreNotTruncated(CommunicatorHarness):

    def test_small_message_round_trips(self):
        self.send(b'hello')
        self.assertEqual(self.received(), 'hello')

    def test_message_larger_than_one_recv_is_complete(self):
        """The regression: a payload bigger than the 8192-byte read buffer.

        The old loop returned only the first chunk.
        """
        payload = ('x' * 50000).encode('utf-8')
        self.send(payload)

        data = self.received()
        self.assertEqual(len(data), 50000,
                         'message truncated at %d bytes' % len(data))

    def test_message_sent_in_several_writes_is_reassembled(self):
        payload = ('y' * 20000).encode('utf-8')
        self.send(payload, chunked=True)

        data = self.received()
        self.assertEqual(len(data), 20000)

    def test_large_json_action_survives_round_trip(self):
        """Shaped like the real traffic - an action plus a payload dict."""
        message = json.dumps(('action_list', {
            'action_list': ['package-%04d-osmc' % n for n in range(2000)],
        }))
        self.send(message.encode('utf-8'))

        action, data = json.loads(self.received())
        self.assertEqual(action, 'action_list')
        self.assertEqual(len(data['action_list']), 2000)
        self.assertEqual(data['action_list'][-1], 'package-1999-osmc')


class TestNoBusyWait(CommunicatorHarness):

    def test_receiving_does_not_spin_on_wait_for_abort(self):
        """The old loop called waitForAbort every 5ms while reading.

        The kernel now wakes the thread when data arrives, so reading a
        message should cost no waitForAbort calls at all.
        """
        before = self.monitor.wait_calls
        self.send(b'z' * 30000)
        self.received()

        self.assertEqual(
            self.monitor.wait_calls, before,
            'receive path still polls waitForAbort (%d calls)'
            % (self.monitor.wait_calls - before)
        )


class TestExitStillWorks(CommunicatorHarness):

    def test_exit_message_stops_the_listener(self):
        self.send(b'exit')
        self.comms.join(timeout=5)
        self.assertFalse(self.comms.is_alive(), 'listener did not shut down')
        self.assertTrue(self.comms.stopped)


class TestImplementation(unittest.TestCase):
    """Guards against the busy-wait pattern coming back."""

    SOURCE = (LIB / "osmccommon/osmc_comms.py").read_text()

    def test_connection_is_not_set_non_blocking(self):
        self.assertNotIn('conn.setblocking(False)', self.SOURCE)

    def test_uses_a_socket_timeout(self):
        self.assertIn('conn.settimeout(self.RECV_TIMEOUT)', self.SOURCE)

    def test_accumulates_chunks(self):
        self.assertIn("b''.join(chunks)", self.SOURCE)


if __name__ == '__main__':
    unittest.main(verbosity=2)
