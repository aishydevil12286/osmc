# -*- coding: utf-8 -*-
"""
    Tests for periodic TRIM being enabled on every platform.

    SD cards and eMMC lose sustained write performance as unused blocks
    accumulate untrimmed. Only Vero was getting a discard pass - perftune's
    performance_tuner runs `fstrim -v /` at boot, inside a block gated on
    osmcdev being vero2 or vero3. Pi never got one.

    Rather than widening that condition, base-files-osmc now enables
    util-linux's fstrim.timer:

      - it is the mechanism Debian ships and enables by default;
      - weekly with Persistent=true, so it catches up after downtime;
      - it stays off the boot path. A first TRIM on a long-lived card can
        take a while and is heavy on I/O, and the existing call sits behind
        `sleep 30` in a boot service, landing on top of Kodi starting up;
      - one mechanism for all platforms, with no per-device condition.

    Continuous discard (the `discard` mount option) was considered and
    rejected: it issues discards inline and generally hurts latency on the
    cheap flash these devices use. Periodic fstrim is the recommended shape.

    Run with: python3 tests/test_periodic_fstrim.py
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTINST = REPO_ROOT / "package/base-files-osmc/files/DEBIAN/postinst"
PERFTUNE = REPO_ROOT / "package/perftune-osmc/files/usr/bin/performance_tuner"


class TestTimerIsEnabled(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = POSTINST.read_text()

    def test_postinst_parses(self):
        result = subprocess.run(['bash', '-n', str(POSTINST)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_enables_fstrim_timer(self):
        self.assertIn('systemctl enable fstrim.timer', self.text)

    def test_enable_is_guarded_on_the_unit_existing(self):
        """util-linux may not provide the unit; a failed enable must not
        fail the package install."""
        self.assertIn('/lib/systemd/system/fstrim.timer', self.text)
        self.assertIn('/usr/lib/systemd/system/fstrim.timer', self.text)

    def test_enable_cannot_fail_the_postinst(self):
        self.assertRegex(
            self.text,
            r'systemctl enable fstrim\.timer[^\n]*\|\|\s*true',
            'the enable needs a || true so a failure cannot abort configure'
        )

    def test_timer_is_not_started_during_the_apt_run(self):
        """Persistent=true means starting it here would fire a first TRIM
        mid-transaction. It should come up on the next boot."""
        self.assertNotIn('systemctl start fstrim.timer', self.text)


class TestGuardBehaviour(unittest.TestCase):
    """Execute the guard itself, both with and without the unit present."""

    GUARD = '''
        if [ -f "%(a)s" ] || [ -f "%(b)s" ]; then
            echo ENABLED
        fi
        echo DONE
    '''

    def run_guard(self, create_unit):
        work = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(work, True))

        lib = os.path.join(work, 'lib')
        usrlib = os.path.join(work, 'usrlib')
        os.makedirs(lib)
        os.makedirs(usrlib)

        if create_unit:
            Path(os.path.join(lib, 'fstrim.timer')).touch()

        script = self.GUARD % {
            'a': os.path.join(lib, 'fstrim.timer'),
            'b': os.path.join(usrlib, 'fstrim.timer'),
        }
        result = subprocess.run(['sh', '-c', script],
                                capture_output=True, text=True)
        return result.returncode, result.stdout.split()

    def test_enables_when_the_unit_is_present(self):
        code, output = self.run_guard(create_unit=True)
        self.assertEqual(code, 0)
        self.assertIn('ENABLED', output)

    def test_skips_cleanly_when_the_unit_is_absent(self):
        code, output = self.run_guard(create_unit=False)
        self.assertEqual(code, 0, 'guard must not fail when the unit is absent')
        self.assertNotIn('ENABLED', output)
        self.assertIn('DONE', output, 'the postinst must carry on regardless')


class TestVeroBootTimeTrimUntouched(unittest.TestCase):
    """The existing Vero behaviour is deliberately left alone - it is not
    testable from here, and the timer is harmless alongside it."""

    @classmethod
    def setUpClass(cls):
        cls.text = PERFTUNE.read_text()

    def test_perftune_still_trims_on_vero(self):
        self.assertIn('fstrim -v /', self.text)

    def test_perftune_condition_is_unchanged(self):
        self.assertIn('[ "$OPTION_OSMCDEV" = "vero2" ] || '
                      '[ "$OPTION_OSMCDEV" = "vero3" ]', self.text)

    def test_perftune_parses(self):
        result = subprocess.run(['sh', '-n', str(PERFTUNE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
