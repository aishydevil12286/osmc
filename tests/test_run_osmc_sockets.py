# -*- coding: utf-8 -*-
"""
    Tests for phase 2 of the /var/tmp -> /run/osmc migration: the two
    settings sockets and the two update flag files.

    These four paths are touched only within mediacenter-addon-osmc, so they
    upgrade atomically and need no cross-package compatibility window. They
    do still need an *intra*-upgrade one: helpers such as apt_cache_action.py
    are launched as fresh `python3 <script>` processes reading new code off
    disk, while the Kodi process they talk to keeps running pre-upgrade code
    - and therefore keeps listening on the old socket - until Kodi restarts.
    So clients try /run/osmc first and fall back.

    Servers must also cope with base-files-osmc not having been upgraded
    yet: /run is root-owned, so an addon running as 'osmc' cannot create
    /run/osmc itself and has to bind the legacy path instead.

    Run with: python3 tests/test_run_osmc_sockets.py
"""

import ast
import importlib.util
import os
import socket
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "package/mediacenter-addon-osmc/src"

OSMC_PATHS = (SRC / "script.module.osmccommon/resources/lib/osmccommon/osmc_paths.py")

SETTINGS = SRC / "service.osmc.settings/resources/lib/osmcsettings"
UPDATES = SRC / "script.module.osmcsetting.updates/resources/lib/osmcupdates"
APFSTORE = SRC / "script.module.osmcsetting.apfstore/resources/lib/apfstore"

# Standalone scripts: launched as a bare `python3 <script>` with no Kodi
# addon sys.path, so they carry their own copy of the path list.
STANDALONE = {
    SETTINGS / "call_osmc_parent.py": 'SETTINGS_SOCKET_PATHS',
    UPDATES / "call_osmc_parent.py": 'SETTINGS_SOCKET_PATHS',
    UPDATES / "call_parent.py": 'UPDATE_SOCKET_PATHS',
    UPDATES / "apt_cache_action.py": 'UPDATE_SOCKET_PATHS',
    UPDATES / "tests/force_update.py": 'UPDATE_SOCKET_PATHS',
}

# Kodi-side modules, which import osmccommon instead.
KODI_SIDE = [
    SETTINGS / "script_entry.py",
    SETTINGS / "service_entry.py",
    UPDATES / "service_entry.py",
    APFSTORE / "apf_gui.py",
]

LEGACY_PRIMARIES = [
    '/var/tmp/osmc.settings.sockfile',
    '/var/tmp/osmc.settings.update.sockfile',
    '/var/tmp/.suppress_osmc_update_checks',
    '/var/tmp/.osmc_failed_update',
]


def load_osmc_paths():
    """osmc_paths imports only os and socket, so it loads outside Kodi."""
    spec = importlib.util.spec_from_file_location("osmc_paths", OSMC_PATHS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def literal_list(path, name):
    """Read a module-level list-of-strings assignment without importing."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return ast.literal_eval(node.value)
    return None


class TestOsmcPathsModule(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.paths = load_osmc_paths()

    def test_run_osmc_is_preferred_when_usable(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.paths.RUN_DIR = tmp
            self.assertEqual(
                self.paths.preferred(self.paths.SETTINGS_SOCKET_PATHS),
                self.paths.SETTINGS_SOCKET_PATHS[0],
            )

    def test_falls_back_when_run_osmc_missing(self):
        """base-files-osmc may not have been upgraded yet, and /run is
        root-owned so an addon running as 'osmc' cannot create the dir."""
        self.paths.RUN_DIR = '/nonexistent/osmc'
        self.assertEqual(
            self.paths.preferred(self.paths.SETTINGS_SOCKET_PATHS),
            self.paths.SETTINGS_SOCKET_PATHS[-1],
        )

    def test_existing_reports_only_paths_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            present = os.path.join(tmp, 'present')
            absent = os.path.join(tmp, 'absent')
            Path(present).touch()
            self.assertEqual(self.paths.existing([present, absent]), [present])
            self.assertEqual(self.paths.existing([absent]), [])

    def test_new_paths_are_all_under_run_osmc(self):
        for name in ('SETTINGS_SOCKET', 'UPDATE_SOCKET',
                     'SUPPRESS_UPDATE_CHECKS', 'FAILED_UPDATE'):
            with self.subTest(path=name):
                self.assertTrue(getattr(self.paths, name).startswith('/run/osmc/'))

    def test_every_list_is_new_then_legacy(self):
        for name in ('SETTINGS_SOCKET_PATHS', 'UPDATE_SOCKET_PATHS',
                     'SUPPRESS_UPDATE_CHECKS_PATHS', 'FAILED_UPDATE_PATHS'):
            with self.subTest(list=name):
                entries = getattr(self.paths, name)
                self.assertEqual(len(entries), 2)
                self.assertTrue(entries[0].startswith('/run/osmc/'))
                self.assertTrue(entries[1].startswith('/var/tmp/'))


class TestConnectFallback(unittest.TestCase):
    """The upgrade window, exercised against real Unix sockets."""

    @classmethod
    def setUpClass(cls):
        cls.paths = load_osmc_paths()

    def listening_socket(self, path):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        self.addCleanup(server.close)
        return server

    def test_connects_to_preferred_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = os.path.join(tmp, 'new.sock')
            old = os.path.join(tmp, 'old.sock')
            self.listening_socket(new)
            self.listening_socket(old)

            client = self.paths.connect([new, old])
            self.addCleanup(client.close)
            self.assertEqual(client.getpeername(), new)

    def test_falls_back_to_legacy_when_preferred_absent(self):
        """A helper running freshly upgraded code reaching a Kodi addon that
        has not restarted and is still listening on the old path."""
        with tempfile.TemporaryDirectory() as tmp:
            new = os.path.join(tmp, 'new.sock')  # never bound
            old = os.path.join(tmp, 'old.sock')
            self.listening_socket(old)

            client = self.paths.connect([new, old])
            self.addCleanup(client.close)
            self.assertEqual(client.getpeername(), old)

    def test_raises_when_nothing_is_listening(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(OSError):
                self.paths.connect([os.path.join(tmp, 'a.sock'),
                                    os.path.join(tmp, 'b.sock')])


class TestStandaloneScriptsStayInStep(unittest.TestCase):
    """The duplicated lists are forced by these scripts having no addon
    sys.path. Duplication is acceptable; silent drift is not."""

    @classmethod
    def setUpClass(cls):
        cls.paths = load_osmc_paths()

    def test_inline_lists_match_the_canonical_ones(self):
        for script, name in STANDALONE.items():
            with self.subTest(script=script.name, list=name):
                inline = literal_list(script, name)
                self.assertIsNotNone(
                    inline, '%s does not define %s' % (script.name, name))
                self.assertEqual(
                    inline, getattr(self.paths, name),
                    '%s has drifted from osmccommon.osmc_paths' % script.name
                )

    def test_standalone_scripts_do_not_import_osmccommon(self):
        """If one gains an addon sys.path it should use the shared module
        instead of keeping a copy - this test is the reminder."""
        for script in STANDALONE:
            with self.subTest(script=script.name):
                self.assertNotIn('from osmccommon', script.read_text())


class TestCallSitesMigrated(unittest.TestCase):

    def test_kodi_side_modules_use_the_shared_module(self):
        for path in KODI_SIDE:
            with self.subTest(module=path.name):
                text = path.read_text()
                self.assertIn('osmc_paths', text,
                              '%s does not use osmccommon.osmc_paths' % path.name)

    def test_no_module_hardcodes_a_legacy_path_as_primary(self):
        """Legacy paths may appear only in a declared fallback list."""
        offenders = []
        allowed = set(STANDALONE) | {OSMC_PATHS}

        for path in list(KODI_SIDE) + list(STANDALONE) + [OSMC_PATHS]:
            text = path.read_text()
            for legacy in LEGACY_PRIMARIES:
                if legacy not in text:
                    continue
                if path in allowed:
                    continue
                offenders.append('%s -> %s' % (path.name, legacy))

        self.assertEqual(offenders, [], 'legacy paths used directly: %s' % offenders)

    def test_servers_bind_via_preferred(self):
        """Both socket servers must pick their bind path at runtime rather
        than hardcoding one."""
        for path, expected in ((SETTINGS / "service_entry.py",
                                'SETTINGS_SOCKET_PATHS'),
                               (UPDATES / "service_entry.py",
                                'UPDATE_SOCKET_PATHS')):
            with self.subTest(server=path.parent.name):
                text = path.read_text()
                self.assertIn('osmc_paths.preferred(osmc_paths.%s)' % expected,
                              text)

    def test_update_flags_are_cleared_in_both_locations(self):
        """The addon writes these as 'osmc' and apt_cache_action.py removes
        them as root; either side may still be on pre-upgrade code."""
        service = (UPDATES / "service_entry.py").read_text()
        self.assertIn('osmc_paths.existing(osmc_paths.SUPPRESS_UPDATE_CHECKS_PATHS)',
                      service)
        self.assertIn('osmc_paths.existing(osmc_paths.FAILED_UPDATE_PATHS)',
                      service)

        apt_action = (UPDATES / "apt_cache_action.py").read_text()
        self.assertIn('self.block_update_files', apt_action,
                      'apt_cache_action.py must clear every location')


if __name__ == '__main__':
    unittest.main(verbosity=2)
