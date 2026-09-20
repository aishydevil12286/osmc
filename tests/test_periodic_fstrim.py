# -*- coding: utf-8 -*-
"""
    Tests for periodic TRIM being enabled on every platform.

    SD cards and eMMC lose sustained write performance as unused blocks
    accumulate untrimmed. Vero used to get a discard pass at boot via
    perftune; that is now left to fstrim.timer on every platform.

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

    Whether a card supports discard cannot be told from its model name -
    families like SanDisk Ultra and Samsung EVO span many years and
    controller revisions - so /usr/bin/osmc-supports-discard asks the device,
    and an ExecCondition= drop-in runs it at each firing. Deciding this at
    run time rather than install time matters because the card can be
    swapped, or root moved to USB, long afterwards.

    Run with: python3 tests/test_periodic_fstrim.py
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTINST = REPO_ROOT / "package/base-files-osmc/files/DEBIAN/postinst"
PERFTUNE = REPO_ROOT / "package/perftune-osmc/files/usr/bin/performance_tuner"
HELPER = (REPO_ROOT /
          "package/base-files-osmc/files/usr/bin/osmc-supports-discard")
DROPIN = (REPO_ROOT / "package/base-files-osmc/files/etc/systemd/system"
                      "/fstrim.service.d/osmc-discard-check.conf")


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


class TestVeroBootTimeTrimRemoved(unittest.TestCase):
    """Weekly fstrim.timer now covers every platform, including Vero.
    The boot-time TRIM sat behind sleep 30 and ran on top of Kodi
    starting up; drop it so boot I/O is not doubled."""

    @classmethod
    def setUpClass(cls):
        cls.text = PERFTUNE.read_text()

    def test_perftune_no_longer_trims_at_boot(self):
        self.assertNotIn('fstrim', self.text)

    def test_vero_gpu_freq_tweak_is_kept(self):
        self.assertIn('[ "$OPTION_OSMCDEV" = "vero2" ] || '
                      '[ "$OPTION_OSMCDEV" = "vero3" ]', self.text)
        self.assertIn('mpgpu/cur_freq', self.text)

    def test_perftune_parses(self):
        result = subprocess.run(['sh', '-n', str(PERFTUNE)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class TestDiscardHelper(unittest.TestCase):
    """Executes the real helper against a controlled sysfs tree, so both
    answers are covered without needing hardware that gives each one."""

    @classmethod
    def setUpClass(cls):
        cls.loop_device = None
        cls.mount_point = None

        if os.geteuid() != 0 or not shutil.which('mkfs.ext4'):
            return

        work = tempfile.mkdtemp()
        cls.work = work
        image = os.path.join(work, 'fs.img')
        point = os.path.join(work, 'mp')
        os.mkdir(point)

        subprocess.run(['dd', 'if=/dev/zero', 'of=' + image, 'bs=1M',
                        'count=16', 'status=none'], check=True)
        subprocess.run(['mkfs.ext4', '-q', image], check=True,
                       capture_output=True)

        attached = subprocess.run(['losetup', '--find', '--show', image],
                                  capture_output=True, text=True)
        if attached.returncode != 0:
            return

        device = attached.stdout.strip()
        if subprocess.run(['mount', device, point],
                          capture_output=True).returncode != 0:
            subprocess.run(['losetup', '-d', device], capture_output=True)
            return

        cls.loop_device = device
        cls.mount_point = point

    @classmethod
    def tearDownClass(cls):
        if cls.mount_point:
            subprocess.run(['umount', cls.mount_point], capture_output=True)
        if cls.loop_device:
            subprocess.run(['losetup', '-d', cls.loop_device],
                           capture_output=True)
        if getattr(cls, 'work', None):
            shutil.rmtree(cls.work, ignore_errors=True)

    def run_helper(self, discard_value):
        """Run the helper with a fake sysfs reporting discard_value.

        discard_value of None means the queue entry is absent entirely.
        """
        if not self.mount_point:
            self.skipTest('needs root, losetup and mkfs.ext4')

        sysfs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, sysfs, True)

        name = os.path.basename(self.loop_device)
        queue = os.path.join(sysfs, name, 'queue')

        if discard_value is not None:
            os.makedirs(queue)
            with open(os.path.join(queue, 'discard_max_bytes'), 'w') as f:
                f.write('%s\n' % discard_value)
        else:
            os.makedirs(os.path.join(sysfs, name))

        env = dict(os.environ, OSMC_SYSFS_BLOCK=sysfs)
        return subprocess.run(['sh', str(HELPER), self.mount_point],
                              capture_output=True, text=True, env=env)

    def test_helper_is_executable(self):
        self.assertTrue(os.access(HELPER, os.X_OK),
                        'the helper must ship with the exec bit set')

    def test_helper_parses(self):
        result = subprocess.run(['sh', '-n', str(HELPER)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_supported_device_exits_zero(self):
        result = self.run_helper(4294966784)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('supports discard', result.stdout)

    def test_unsupported_device_exits_nonzero(self):
        result = self.run_helper(0)
        self.assertEqual(result.returncode, 1)
        self.assertIn('does not support discard', result.stdout)

    def test_missing_queue_entry_is_not_fatal(self):
        result = self.run_helper(None)
        self.assertEqual(result.returncode, 1)
        self.assertIn('no discard information', result.stdout)

    def test_malformed_value_is_not_fatal(self):
        result = self.run_helper('not-a-number')
        self.assertEqual(result.returncode, 1)

    def test_non_block_filesystem_is_skipped(self):
        result = subprocess.run(['sh', str(HELPER), '/proc'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('not a block device', result.stdout)

    def test_unknown_target_is_not_fatal(self):
        result = subprocess.run(['sh', str(HELPER), '/no/such/path'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)

    def test_every_failure_exits_inside_the_skip_range(self):
        """ExecCondition= treats 1-254 as 'skip this unit'. 255 or a signal
        marks it failed, which is exactly what this drop-in avoids."""
        for target in ('/proc', '/no/such/path'):
            with self.subTest(target=target):
                result = subprocess.run(['sh', str(HELPER), target],
                                        capture_output=True, text=True)
                self.assertGreaterEqual(result.returncode, 1)
                self.assertLessEqual(result.returncode, 254)


class TestDropIn(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = DROPIN.read_text()

    def test_dropin_targets_fstrim_service(self):
        self.assertEqual(DROPIN.parent.name, 'fstrim.service.d')

    def test_dropin_uses_exec_condition(self):
        """ExecCondition, not ExecStartPre: the former skips the unit, the
        latter fails it."""
        self.assertIn('ExecCondition=/usr/bin/osmc-supports-discard',
                      self.text)
        self.assertNotIn('ExecStartPre', self.text)

    def test_dropin_declares_a_service_section(self):
        self.assertIn('[Service]', self.text)

    def test_helper_path_in_dropin_matches_where_it_ships(self):
        shipped = '/' + str(HELPER.relative_to(
            REPO_ROOT / 'package/base-files-osmc/files'))
        self.assertIn('ExecCondition=' + shipped, self.text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
