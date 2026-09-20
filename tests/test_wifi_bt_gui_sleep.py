# -*- coding: utf-8 -*-
"""
    Tests for the WiFi/Bluetooth settings-panel populate threads sleeping
    on a real interval rather than spinning every 10ms.

    WIFIPopulateBot and BluetoothBot used to:

        if runs % 200 == 0: refresh the list   # intended: every 2s
        if runs % 600 == 0: rescan wifi        # comment: "every minute"
        xbmc.sleep(10)
        runs += 1

    600 * 10ms is 6 seconds, not a minute, and the thread woke 100 times
    a second for the whole time the panel was open. The lists themselves
    only need updating every couple of seconds.

    The loops are now time-based (LIST_REFRESH_S / WIFI_RESCAN_S) and
    sleep THREAD_POLL_MS between wakeups. Bluetooth device details are
    fetched with one Properties.GetAll rather than four Gets.

    These tests inspect the source; the loops cannot be executed here
    because networking_gui.py imports xbmc at module level.

    Run with: python3 tests/test_wifi_bt_gui_sleep.py
"""

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GUI = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                   "/script.module.osmcsetting.networking/resources/lib"
                   "/osmcnetworking/networking_gui.py")
BLUETOOTH = (REPO_ROOT / "package/mediacenter-addon-osmc/src"
                         "/script.module.osmcsetting.networking/resources/lib"
                         "/osmcnetworking/bluetooth.py")


def module_constants(path):
    tree = ast.parse(path.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                values[target.id] = node.value.value
    return values


class TestSleepIntervals(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.source = GUI.read_text()
        cls.constants = module_constants(GUI)

    def test_poll_is_hundreds_of_milliseconds_not_ten(self):
        self.assertGreaterEqual(self.constants['THREAD_POLL_MS'], 100)
        self.assertLessEqual(self.constants['THREAD_POLL_MS'], 500)

    def test_wifi_rescan_is_about_a_minute(self):
        self.assertGreaterEqual(self.constants['WIFI_RESCAN_S'], 30)
        self.assertLessEqual(self.constants['WIFI_RESCAN_S'], 120)

    def test_list_refresh_is_a_couple_of_seconds(self):
        self.assertGreaterEqual(self.constants['LIST_REFRESH_S'], 1)
        self.assertLessEqual(self.constants['LIST_REFRESH_S'], 5)

    def test_ten_millisecond_busy_wait_is_gone(self):
        self.assertNotIn('xbmc.sleep(10)', self.source)

    def test_counter_modulo_loops_are_gone(self):
        tree = ast.parse(self.source)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
                if isinstance(node.left, ast.Name) and node.left.id == 'runs':
                    self.fail('populate loops still tick a runs counter')

    def test_loops_sleep_the_named_poll_interval(self):
        self.assertIn('xbmc.sleep(THREAD_POLL_MS)', self.source)
        self.assertIn('xbmc.sleep(THREAD_JOIN_POLL_MS)', self.source)

    def test_loops_are_time_based(self):
        self.assertIn('time.time()', self.source)
        self.assertIn('WIFI_RESCAN_S', self.source)
        self.assertIn('LIST_REFRESH_S', self.source)


class TestBluetoothGetAll(unittest.TestCase):

    def test_bluez_exposes_get_all_properties(self):
        source = BLUETOOTH.read_text()
        self.assertIn('def get_device_properties(', source)
        self.assertIn('GetAll(BLUEZ_DEVICE)', source)

    def test_panel_uses_the_bulk_fetch(self):
        source = GUI.read_text()
        self.assertIn('get_device_properties', source)
        self.assertNotIn("get_device_property(address, 'Alias')", source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
