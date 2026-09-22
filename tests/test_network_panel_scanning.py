# -*- coding: utf-8 -*-
"""
    Tests for the WiFi and Bluetooth settings panels in
    osmcnetworking/networking_gui.py.

    Three behaviours are covered.

    1. WiFi active scanning. WIFIPopulateBot.run() used to fire an active
       radio scan on a timer:

           if not self.exit and runs % 600 == 0:
               self.wifi_scanner_bot = WIFIScannerBot()
               ...
               self.wifi_scanner_bot.start()

       with `xbmc.sleep(10)` at the bottom of the loop, so despite the
       comment above it saying "every minute re-scan wifi" this was a scan
       every *6 seconds*, for as long as the panel was open, connected or
       not. The displayed list comes from connman's cached service list via
       manager.GetServices() and does not need a scan to stay current, and
       connman background-scans on its own besides. Scanning is now on panel
       entry and on an explicit user request only.

    2. Loop tick rate. Both population threads woke every 10ms - 100 times a
       second each - purely so that the exit flag was noticed promptly.

    3. Bluetooth device properties. populate_bluetooth_dict() re-fetched
       Alias, Paired, Connected and Trusted one D-Bus round trip at a time,
       for every device in both lists, every refresh - even though the
       list_*_devices() call that produced the device list already returns
       every property from a single GetManagedObjects() call.

    4. Bluetooth discovery timeout. Discovery was already on-demand, but
       nothing stopped it if the user left the panel open and walked away.

    Run with: python3 tests/test_network_panel_scanning.py
"""

import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NETWORKING = (REPO_ROOT / 'package' / 'mediacenter-addon-osmc' / 'src' /
              'script.module.osmcsetting.networking')
LIB = NETWORKING / 'resources' / 'lib'
GUI_SOURCE = LIB / 'osmcnetworking' / 'networking_gui.py'
SKINS = [NETWORKING / 'resources' / 'skins' / 'Default' / '1080i' / 'network_gui.xml',
         NETWORKING / 'resources' / 'skins' / 'Default' / '1080i' / 'network_gui_720.xml']
STRINGS = (NETWORKING / 'resources' / 'language' / 'resource.language.en_gb' / 'strings.po')


def install_stubs():
    """ Stub out everything networking_gui imports at module scope, so the
        real module can be imported and its real classes exercised. """

    def module(name, **attrs):
        mod = types.ModuleType(name)
        mod.__path__ = []
        for key, value in attrs.items():
            setattr(mod, key, value)
        sys.modules[name] = mod
        return mod

    sleeps = []

    module('xbmc',
           sleep=lambda ms: sleeps.append(ms),
           log=lambda *a, **k: None,
           executebuiltin=lambda *a, **k: None,
           translatePath=lambda p: p,
           Monitor=type('Monitor', (), {'abortRequested': lambda self: False}),
           LOGDEBUG=0, LOGINFO=1, LOGWARNING=2, LOGERROR=3, LOGFATAL=4, LOGNONE=5)

    module('xbmcaddon', Addon=type('Addon', (), {
        '__init__': lambda self, *a, **k: None,
        'getAddonInfo': lambda self, key: '',
        'getSetting': lambda self, key: '',
        'setSetting': lambda self, key, value: None,
    }))

    module('xbmcgui',
           Dialog=type('Dialog', (), {
               '__init__': lambda self, *a, **k: None,
               'ok': lambda self, *a, **k: True,
               'select': lambda self, *a, **k: -1,
               'yesno': lambda self, *a, **k: False,
               'notification': lambda self, *a, **k: None,
           }),
           ListItem=type('ListItem', (), {'__init__': lambda self, *a, **k: None}),
           WindowXMLDialog=type('WindowXMLDialog', (), {'__init__': lambda self, *a, **k: None}),
           ControlLabel=type('ControlLabel', (), {}),
           ACTION_PREVIOUS_MENU=10, ACTION_NAV_BACK=92)

    module('xbmcvfs', exists=lambda p: False, translatePath=lambda p: p)

    module('osmccommon')
    module('osmccommon.osmc_language',
           LangRetriever=type('LangRetriever', (), {
               '__init__': lambda self, *a, **k: None,
               'lang': staticmethod(lambda value: str(value)),
           }))
    module('osmccommon.osmc_logging',
           StandardLogger=type('StandardLogger', (), {
               '__init__': lambda self, *a, **k: None,
               'log': staticmethod(lambda *a, **k: None),
           }),
           clog=lambda *a, **k: (lambda f: f))

    module('dbus')
    module('dbus.service')
    module('dbus.mainloop')
    module('dbus.mainloop.glib', DBusGMainLoop=lambda **k: None)

    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))

    # The bluetooth agent is built out of ravel/dbussy decorators evaluated at
    # import time; stub the whole module rather than the D-Bus stack under it.
    import osmcnetworking  # noqa: F401
    module('osmcnetworking.osmc_bluetooth_agent',
           pair_with_agent=lambda *a, **k: None)

    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))

    return sleeps


SLEEPS = install_stubs()
from osmcnetworking import networking_gui  # noqa: E402


class FakeBluetooth(object):
    """ Counts D-Bus round trips the way the real wrapper would make them. """

    def __init__(self, devices):
        self._devices = devices
        self.property_calls = []
        self.managed_object_calls = 0
        self.discovery_stops = 0
        self.discovery_starts = 0

    def list_trusted_devices(self):
        self.managed_object_calls += 1
        return {a: p for a, p in self._devices.items() if p.get('Trusted')}

    def list_discovered_devices(self):
        self.managed_object_calls += 1
        return {a: p for a, p in self._devices.items() if not p.get('Trusted')}

    def get_device_property(self, address, key):
        self.property_calls.append((address, key))
        return self._devices[address].get(key)

    def start_discovery(self):
        self.discovery_starts += 1

    def stop_discovery(self):
        self.discovery_stops += 1


DEVICES = {
    'AA:BB:CC:DD:EE:01': {'Alias': 'OSMC Remote', 'Paired': True,
                          'Connected': True, 'Trusted': True},
    'AA:BB:CC:DD:EE:02': {'Alias': 'Keyboard', 'Paired': True,
                          'Connected': False, 'Trusted': True},
    'AA:BB:CC:DD:EE:03': {'Alias': "Someone's Phone", 'Paired': False,
                          'Connected': False, 'Trusted': False},
}


class TestWifiScanIsOnDemand(unittest.TestCase):

    def setUp(self):
        self.source = GUI_SOURCE.read_text(encoding='utf-8')

    def test_no_timer_starts_a_wifi_scan(self):
        """ The population loop must not start a scanner thread on a timer. """
        run = _method_source(self.source, 'class WIFIPopulateBot', 'def run')

        self.assertNotIn('WIFIScannerBot', run,
                         'WIFIPopulateBot.run() still starts an active scan on '
                         'its own schedule; scanning must be on demand only.')

    def test_scan_is_reachable_on_request(self):
        """ ...but an explicit request must still be able to scan. """
        self.assertIn('def request_scan(self)', self.source)
        request = _method_source(self.source, 'class WIFIPopulateBot', 'def request_scan')
        self.assertIn('WIFIScannerBot', request)

    def test_request_scan_starts_a_scanner_thread(self):
        bot = networking_gui.WIFIPopulateBot.__new__(networking_gui.WIFIPopulateBot)
        bot.exit = False
        bot.wifi_scanner_bot = None

        scanned = []
        original = networking_gui.osmc_network.scan_wifi
        networking_gui.osmc_network.scan_wifi = lambda: scanned.append(1)
        try:
            bot.request_scan()
            bot.wifi_scanner_bot.join(5)
        finally:
            networking_gui.osmc_network.scan_wifi = original

        self.assertIsInstance(bot.wifi_scanner_bot, networking_gui.WIFIScannerBot)
        self.assertEqual(scanned, [1],
                         'request_scan() did not reach osmc_network.scan_wifi()')

    def test_request_scan_does_not_stack_scans(self):
        """ A held-down button must not spawn a scanner thread per press. """
        bot = networking_gui.WIFIPopulateBot.__new__(networking_gui.WIFIPopulateBot)
        bot.exit = False

        class Running(object):
            @staticmethod
            def is_alive():
                return True

        bot.wifi_scanner_bot = Running()
        bot.request_scan()
        self.assertIs(bot.wifi_scanner_bot.__class__, Running,
                      'request_scan() replaced a still-running scanner thread')

    def test_request_scan_is_a_noop_after_exit(self):
        bot = networking_gui.WIFIPopulateBot.__new__(networking_gui.WIFIPopulateBot)
        bot.exit = True
        bot.wifi_scanner_bot = None
        bot.request_scan()
        self.assertIsNone(bot.wifi_scanner_bot)

    def test_panel_entry_still_scans_once(self):
        """ Entering the panel scans, so first-run setup isn't shown an
            empty list while connman's background scan catches up. """
        self.assertIn('self.populate_wifi_panel(True)', self.source)

    def test_rescan_control_is_wired_up(self):
        self.assertIn('WIRELESS_SCAN_BUTTON = 10222', self.source)
        self.assertIn('control_id == WIRELESS_SCAN_BUTTON', self.source)
        self.assertIn('def rescan_wifi(self)', self.source)
        self.assertIn('10222', _constant(self.source, 'ALL_WIRELESS_CONTROLS'),
                      'the rescan button must be in ALL_WIRELESS_CONTROLS so '
                      'onClick() routes it and it is hidden with the panel')

    def test_rescan_control_exists_in_both_skins(self):
        for skin in SKINS:
            text = skin.read_text(encoding='utf-8')
            self.assertIn('<control id="10222" type="button">', text,
                          '%s has no rescan button' % skin.name)
            self.assertIn('32107', text, '%s rescan button has no label' % skin.name)

    def test_rescan_label_string_exists(self):
        self.assertIn('msgctxt "#32107"', STRINGS.read_text(encoding='utf-8'))


class TestLoopTickRate(unittest.TestCase):

    def test_wifi_loop_does_not_spin_at_10ms(self):
        self.assertGreaterEqual(networking_gui.WIFIPopulateBot.TICK, 100,
                                'WiFi panel thread still wakes ~100 times a second')

    def test_bluetooth_loop_does_not_spin_at_10ms(self):
        self.assertGreaterEqual(networking_gui.BluetoothPopulationThread.TICK, 100,
                                'Bluetooth panel thread still wakes ~100 times a second')

    def test_refresh_interval_is_a_whole_number_of_ticks(self):
        """ The loops gate work on `elapsed % REFRESH_INTERVAL`, so a tick
            that doesn't divide the interval would skip refreshes entirely. """
        for cls in (networking_gui.WIFIPopulateBot,
                    networking_gui.BluetoothPopulationThread):
            self.assertEqual(cls.REFRESH_INTERVAL % cls.TICK, 0, cls.__name__)

    def test_refresh_interval_unchanged_at_2s(self):
        """ The list refresh rate the user sees must not regress. """
        for cls in (networking_gui.WIFIPopulateBot,
                    networking_gui.BluetoothPopulationThread):
            self.assertEqual(cls.REFRESH_INTERVAL, 2000, cls.__name__)

    def test_bluetooth_debug_dump_is_gone(self):
        source = GUI_SOURCE.read_text(encoding='utf-8')
        run = _method_source(source, 'class BluetoothPopulationThread', 'def run')
        self.assertNotIn('-- DISCOVERED ---', run,
                         'the every-4-seconds debug dump of both device dicts '
                         'is still writing to the Kodi log')


class TestBluetoothPropertyBatching(unittest.TestCase):

    def _thread(self):
        thread = networking_gui.BluetoothPopulationThread.__new__(
            networking_gui.BluetoothPopulationThread)
        thread._osmc_bluetooth = FakeBluetooth(DEVICES)
        thread._addon = None
        return thread

    def test_no_per_property_round_trips(self):
        thread = self._thread()

        thread.populate_bluetooth_dict(True)
        thread.populate_bluetooth_dict(False)

        self.assertEqual(thread.osmc_bluetooth.property_calls, [],
                         'populate_bluetooth_dict is still making a D-Bus '
                         'round trip per property per device: %r'
                         % (thread.osmc_bluetooth.property_calls,))

    def test_values_are_unchanged(self):
        thread = self._thread()

        trusted = thread.populate_bluetooth_dict(True)
        discovered = thread.populate_bluetooth_dict(False)

        self.assertEqual(sorted(trusted), ['AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02'])
        self.assertEqual(sorted(discovered), ['AA:BB:CC:DD:EE:03'])

        self.assertEqual(trusted['AA:BB:CC:DD:EE:01'],
                         {'alias': 'OSMC Remote', 'paired': True,
                          'connected': True, 'trusted': True})
        self.assertEqual(trusted['AA:BB:CC:DD:EE:02']['connected'], False)
        self.assertEqual(discovered['AA:BB:CC:DD:EE:03']['alias'], "Someone's Phone")

    def test_values_are_native_types(self):
        """ The managed-object dict carries dbus.String/dbus.Boolean, whose
            str() differs from the native bool's ('1' vs 'True'). The list
            item sets its 'connected' property with str(), and the comparison
            in update_list_control reads it back, so leaking a dbus type here
            would silently break connected-state refresh. """
        thread = self._thread()
        info = thread.populate_bluetooth_dict(True)['AA:BB:CC:DD:EE:01']

        self.assertIs(type(info['alias']), str)
        for key in ('paired', 'connected', 'trusted'):
            self.assertIs(type(info[key]), bool, key)
        self.assertEqual(str(info['connected']), 'True')

    def test_missing_properties_do_not_raise(self):
        """ A device that BlueZ has only partially resolved must not take
            down the whole refresh. """
        thread = networking_gui.BluetoothPopulationThread.__new__(
            networking_gui.BluetoothPopulationThread)
        thread._osmc_bluetooth = FakeBluetooth({'AA:BB:CC:DD:EE:04': {'Trusted': True}})
        thread._addon = None

        info = thread.populate_bluetooth_dict(True)['AA:BB:CC:DD:EE:04']
        self.assertEqual(info, {'alias': '', 'paired': False,
                                'connected': False, 'trusted': True})


class TestBluetoothDiscoveryTimeout(unittest.TestCase):

    def _gui(self, started_at, discovering=True):
        gui = networking_gui.NetworkingGui.__new__(networking_gui.NetworkingGui)
        gui.bluetooth_discovering = discovering
        gui.bluetooth_discovery_started = started_at
        gui._osmc_bluetooth = FakeBluetooth(DEVICES)
        gui._addon = None
        gui.getControl = lambda control_id: types.SimpleNamespace(
            setSelected=lambda value: self.selected.append(value))
        self.selected = []
        return gui

    def setUp(self):
        self.selected = []
        self.timeout = networking_gui.BLUETOOTH_DISCOVERY_TIMEOUT

    def test_timeout_is_bounded(self):
        self.assertTrue(30 <= self.timeout <= 300,
                        'discovery timeout of %ss is not a sane window for '
                        'pairing a device' % self.timeout)

    def test_discovery_stops_once_the_window_passes(self):
        import time
        gui = self._gui(time.time() - self.timeout - 1)

        self.assertTrue(gui.expire_bluetooth_discovery())
        self.assertEqual(gui.osmc_bluetooth.discovery_stops, 1)
        self.assertFalse(gui.bluetooth_discovering)
        self.assertEqual(gui.bluetooth_discovery_started, 0)

    def test_radio_button_reflects_the_timeout(self):
        import time
        gui = self._gui(time.time() - self.timeout - 1)
        gui.expire_bluetooth_discovery()
        self.assertEqual(self.selected, [False],
                         'the discovery radio button must be cleared, or the '
                         'GUI will claim discovery is running when it is not')

    def test_discovery_is_left_alone_inside_the_window(self):
        import time
        gui = self._gui(time.time() - 1)

        self.assertFalse(gui.expire_bluetooth_discovery())
        self.assertEqual(gui.osmc_bluetooth.discovery_stops, 0)
        self.assertTrue(gui.bluetooth_discovering)

    def test_no_op_when_discovery_is_not_running(self):
        gui = self._gui(0, discovering=False)
        self.assertFalse(gui.expire_bluetooth_discovery())
        self.assertEqual(gui.osmc_bluetooth.discovery_stops, 0)

    def test_population_thread_drives_the_timeout(self):
        """ The timeout has to be driven by the only thing still ticking when
            the user has walked away - not by a GUI action handler. """
        source = GUI_SOURCE.read_text(encoding='utf-8')
        run = _method_source(source, 'class BluetoothPopulationThread', 'def run')
        self.assertIn('discovery_expiry_check', run)
        self.assertIn('discovery_expiry_check=self.expire_bluetooth_discovery', source)

    def test_start_discovery_records_the_clock(self):
        gui = self._gui(0, discovering=False)
        gui.start_bluetooth_discovery()

        self.assertTrue(gui.bluetooth_discovering)
        self.assertGreater(gui.bluetooth_discovery_started, 0)
        self.assertEqual(gui.osmc_bluetooth.discovery_starts, 1)
        self.assertEqual(self.selected, [True])

    def test_radio_button_is_resynced_after_a_toggle(self):
        """ Kodi flips a radiobutton's visual state on click before the
            handler runs, so a toggle that could not take effect - adapter
            off, or start_discovery() raising - used to leave the button
            claiming discovery was running. """
        source = GUI_SOURCE.read_text(encoding='utf-8')
        handler = source[source.index('elif control_id == BLUETOOTH_DISCOVERY'):
                         source.index('elif control_id == 6000')]
        self.assertIn('_set_discovery_radio_button(self.bluetooth_discovering)', handler)

    def test_thread_survives_a_failing_expiry_check(self):
        """ A raising callback must not kill the population thread. """
        source = GUI_SOURCE.read_text(encoding='utf-8')
        run = _method_source(source, 'class BluetoothPopulationThread', 'def run')
        self.assertIn('try:', run)
        self.assertIn('except:', run)


def _method_source(source, class_marker, method_marker):
    """ Return the text of `method_marker` within `class_marker`. """
    class_start = source.index(class_marker)
    next_class = source.find('\nclass ', class_start + 1)
    class_body = source[class_start:next_class if next_class != -1 else len(source)]

    method_start = class_body.index(method_marker)
    rest = class_body[method_start:]
    match = re.search(r'\n    (?:@|def )', rest[1:])
    return rest[:match.start() + 1] if match else rest


def _constant(source, name):
    match = re.search(r'^%s = (\[.*?\])' % re.escape(name), source,
                      re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
