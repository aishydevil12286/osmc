# -*- coding: utf-8 -*-
"""
    Tests for the update daemon sleeping longer when idle.

    service_entry.Main._daemon used to waitForAbort(0.5) on every pass
    for the life of Kodi, two wake-ups a second, for a scheduler that
    only needs minute granularity. It also processed a single queue
    item per tick.

    Idle waits are now two seconds; the holding pattern, icon
    positioning, and a non-empty IPC queue keep the half-second cadence.
    check_action_queue drains the queue in one pass.

    The module imports xbmc at import time, so these tests inspect the
    source and execute a copy of the wait-interval decision.

    Run with: python3 tests/test_update_daemon_idle_poll.py
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                      "/script.module.osmcsetting.updates/resources/lib"
                      "/osmcupdates/service_entry.py")


def module_constants(path):
    tree = ast.parse(path.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                values[target.id] = node.value.value
    return values


class TestDaemonWaitConstants(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.constants = module_constants(SOURCE)

    def test_idle_wait_is_seconds_not_half_a_second(self):
        self.assertGreaterEqual(self.constants['DAEMON_IDLE_WAIT_S'], 2.0)
        self.assertLessEqual(self.constants['DAEMON_IDLE_WAIT_S'], 5.0)

    def test_busy_wait_stays_responsive(self):
        self.assertLessEqual(self.constants['DAEMON_BUSY_WAIT_S'], 0.5)

    def test_hardcoded_half_second_wait_is_gone(self):
        self.assertNotIn('waitForAbort(0.5)', self.source)
        self.assertIn('waitForAbort(self._daemon_wait_seconds())', self.source)

    def test_queue_is_drained_in_a_loop(self):
        self.assertIn('def check_action_queue(self):', self.source)
        # The old body was a single try/get/except Empty; draining needs while.
        tree = ast.parse(self.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'check_action_queue':
                self.assertTrue(
                    any(isinstance(child, ast.While) for child in ast.walk(node)),
                    'check_action_queue no longer loops over the queue'
                )
                return
        self.fail('check_action_queue missing')


class TestWaitDecision(unittest.TestCase):
    """Mirrors _daemon_wait_seconds without importing xbmc."""

    IDLE = 2.0
    BUSY = 0.5

    def choose(self, holding, positioning, queue_pending):
        if holding or positioning or queue_pending:
            return self.BUSY
        return self.IDLE

    def test_idle_when_nothing_is_happening(self):
        self.assertEqual(self.choose(False, False, False), self.IDLE)

    def test_busy_during_holding_pattern(self):
        self.assertEqual(self.choose(True, False, False), self.BUSY)

    def test_busy_while_positioning_the_icon(self):
        self.assertEqual(self.choose(False, True, False), self.BUSY)

    def test_busy_when_the_queue_has_work(self):
        self.assertEqual(self.choose(False, False, True), self.BUSY)


if __name__ == '__main__':
    unittest.main(verbosity=2)
