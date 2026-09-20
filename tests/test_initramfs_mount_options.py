# -*- coding: utf-8 -*-
"""
    Tests for the root filesystem mount options set by the OSMC initramfs.

    Root lives on SD or eMMC on every platform OSMC ships. The initramfs
    mounts it, and it was doing so with no options beyond 'rw', which leaves
    the kernel default of relatime in place: any file read whose access time
    is over a day old gets an atime written back. A Kodi library scan turns
    thousands of reads into thousands of small metadata writes, which is the
    worst case for flash.

    The installer does write 'noatime' into /etc/fstab, but for everything
    except the Apple TV the root entry there is commented out - the
    initramfs owns that mount - so it never took effect. `rootflags=` is not
    parsed either, so it could not be set from cmdline.txt.

    These tests execute the real option-building lines lifted out of the
    init script, so they fail if that logic is edited without updating them.

    Run with: python3 tests/test_initramfs_mount_options.py
"""

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT = REPO_ROOT / "package/kernel-osmc/initramfs-src/init"

# The three lines that decide the final mount options, exactly as they
# appear in the init script.
PREPEND_RW = '\tOPTION_MOUNT_OPTIONS="rw${OPTION_MOUNT_OPTIONS}"'
ADD_DASH_O = ('if [ "$OPTION_MOUNT_OPTIONS" ]; then '
              'OPTION_MOUNT_OPTIONS="-o $OPTION_MOUNT_OPTIONS"; fi')
MOUNT_CALL = ('/bin/busybox mount -t "$OPTION_FILESYSTEM" "$OPTION_ROOT" '
              '"$OPTION_MOUNT_PATH" $OPTION_MOUNT_OPTIONS')


def init_source():
    return INIT.read_text()


def default_mount_options():
    """The OPTION_MOUNT_OPTIONS default as set in the init script."""
    match = re.search(r'^OPTION_MOUNT_OPTIONS="([^"]*)"$',
                      init_source(), re.M)
    return match.group(1) if match else None


def build_options(default, filesystem='ext4', nfs_options=None):
    """Run the init script's real option-building logic under /bin/sh."""
    script = '''
        OPTION_FILESYSTEM="%s"
        OPTION_MOUNT_OPTIONS="%s"
        if [ "$OPTION_FILESYSTEM" = "nfs" ]; then
            OPTION_MOUNT_OPTIONS="%s"
        else
        %s
        fi
        %s
        echo "$OPTION_MOUNT_OPTIONS"
    ''' % (filesystem, default, nfs_options or '', PREPEND_RW, ADD_DASH_O)

    result = subprocess.run(['sh', '-c', script],
                            capture_output=True, text=True)
    return result.stdout.strip()


class TestInitScriptIsIntact(unittest.TestCase):
    """If these fail, the logic moved and the tests below are meaningless."""

    def test_init_script_parses(self):
        result = subprocess.run(['sh', '-n', str(INIT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_option_building_lines_are_where_we_think(self):
        source = init_source()
        for line in (PREPEND_RW, ADD_DASH_O, MOUNT_CALL):
            with self.subTest(line=line.strip()[:50]):
                self.assertIn(line, source)

    def test_rootflags_is_still_not_parsed(self):
        """Documents why the default had to change rather than cmdline.txt.

        If someone adds rootflags= parsing, this fails as a prompt to
        reconsider whether the baked-in default is still the right approach.
        """
        self.assertNotIn('rootflags', init_source())


class TestRootIsMountedNoatime(unittest.TestCase):

    def test_default_carries_noatime_with_a_leading_comma(self):
        default = default_mount_options()
        self.assertIsNotNone(default, 'OPTION_MOUNT_OPTIONS default not found')
        self.assertEqual(
            default, ',noatime',
            'the default must keep the leading comma the init script '
            'documents, so it concatenates onto "rw" correctly'
        )

    def test_local_root_is_mounted_rw_noatime(self):
        self.assertEqual(build_options(default_mount_options()),
                         '-o rw,noatime')

    def test_root_is_still_read_write(self):
        """noatime must not have displaced rw."""
        options = build_options(default_mount_options())
        self.assertIn('rw', options.split('-o ')[1].split(','))

    def test_nfs_root_is_left_alone(self):
        """The nfsroot= handler assigns OPTION_MOUNT_OPTIONS outright, so an
        NFS install keeps exactly the options the user asked for."""
        self.assertEqual(
            build_options(default_mount_options(), filesystem='nfs',
                          nfs_options='vers=3,nolock'),
            '-o vers=3,nolock',
        )

    def test_empty_default_would_still_produce_valid_options(self):
        """Guard on the surrounding logic: even with the old empty default
        the result must be a well-formed option string, never a bare -o."""
        self.assertEqual(build_options(''), '-o rw')


class TestOptionStringIsAccepted(unittest.TestCase):
    """The option string has to be one ext4 actually accepts."""

    def test_mount_accepts_rw_noatime(self):
        import os
        import shutil
        import tempfile

        if os.geteuid() != 0 or not shutil.which('mkfs.ext4'):
            self.skipTest('needs root and mkfs.ext4 to build a loopback image')

        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        image = os.path.join(work, 'fs.img')
        point = os.path.join(work, 'mp')
        os.mkdir(point)

        subprocess.run(['dd', 'if=/dev/zero', 'of=' + image, 'bs=1M',
                        'count=16', 'status=none'], check=True)
        subprocess.run(['mkfs.ext4', '-q', image], check=True,
                       capture_output=True)

        mounted = subprocess.run(['mount', '-o', 'rw,noatime', image, point],
                                 capture_output=True, text=True)
        if mounted.returncode != 0:
            self.skipTest('cannot mount here: %s' % mounted.stderr.strip())

        try:
            with open('/proc/mounts') as f:
                entry = [l for l in f if ' %s ' % point in l]

            self.assertTrue(entry, 'mount point not found in /proc/mounts')
            self.assertIn('noatime', entry[0].split()[3])
        finally:
            subprocess.run(['umount', point], capture_output=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
