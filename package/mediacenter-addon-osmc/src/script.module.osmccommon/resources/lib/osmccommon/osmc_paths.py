# -*- coding: utf-8 -*-
"""
    Copyright (C) 2014-2020 OSMC (KodeKarnage)

    This file is part of script.module.osmccommon

    SPDX-License-Identifier: GPL-2.0-or-later
    See LICENSES/GPL-2.0-or-later for more information.

    Locations of the runtime files OSMC uses to coordinate between the Kodi
    addons and the privileged helpers they invoke.

    These moved out of /var/tmp. That directory is world-writable, and - the
    reason the move matters - a unit setting PrivateTmp= is given a private
    /tmp *and* /var/tmp, so anything coordinating through either cannot be
    sandboxed. /run/osmc is created by base-files-osmc via tmpfiles.d, mode
    0770 root:osmc, so both root and the addons can create and remove
    entries.

    Nothing here assumes that directory exists. base-files-osmc and
    mediacenter-addon-osmc are separate packages that upgrade independently,
    and /run is root-owned - an addon running as 'osmc' cannot create
    /run/osmc itself. So servers bind the legacy path when /run/osmc is not
    usable, and clients try both.

    NOTE: apt_cache_action.py, call_parent.py and call_osmc_parent.py are
    launched as bare `python3 <script>` (via sudo, for the first) with no
    Kodi addon sys.path, so they cannot import this module and carry their
    own copies of the relevant lists. tests/test_run_osmc_sockets.py asserts
    those copies stay in step with the definitions here.
"""

import os
import socket

RUN_DIR = '/run/osmc'

SETTINGS_SOCKET = RUN_DIR + '/settings.sock'
UPDATE_SOCKET = RUN_DIR + '/settings-update.sock'
SUPPRESS_UPDATE_CHECKS = RUN_DIR + '/suppress-update-checks'
FAILED_UPDATE = RUN_DIR + '/failed-update'

# Pre-/run/osmc locations. Still honoured so that a helper started from
# freshly upgraded code can reach a Kodi addon that has not restarted yet
# and is therefore still listening on the old path, and so that a flag
# written before the upgrade is not missed.
#
# Remove these, and collapse the *_PATHS lists, one release after every
# reader and writer uses /run/osmc.
LEGACY_SETTINGS_SOCKET = '/var/tmp/osmc.settings.sockfile'
LEGACY_UPDATE_SOCKET = '/var/tmp/osmc.settings.update.sockfile'
LEGACY_SUPPRESS_UPDATE_CHECKS = '/var/tmp/.suppress_osmc_update_checks'
LEGACY_FAILED_UPDATE = '/var/tmp/.osmc_failed_update'

# Ordered most-preferred first. Clients try every entry; servers and
# writers take the first usable one.
SETTINGS_SOCKET_PATHS = [SETTINGS_SOCKET, LEGACY_SETTINGS_SOCKET]
UPDATE_SOCKET_PATHS = [UPDATE_SOCKET, LEGACY_UPDATE_SOCKET]
SUPPRESS_UPDATE_CHECKS_PATHS = [SUPPRESS_UPDATE_CHECKS,
                                LEGACY_SUPPRESS_UPDATE_CHECKS]
FAILED_UPDATE_PATHS = [FAILED_UPDATE, LEGACY_FAILED_UPDATE]


def run_dir_usable():
    """Whether /run/osmc exists and is writable by this process.

    False on a system where base-files-osmc has not been upgraded yet.
    """
    return os.path.isdir(RUN_DIR) and os.access(RUN_DIR, os.W_OK)


def preferred(paths):
    """The path a server should bind, or a writer should write to."""
    return paths[0] if run_dir_usable() else paths[-1]


def existing(paths):
    """Every path in the list that is present on disk.

    Readers use this so a flag is seen wherever it was written, and
    removers so no stale copy is left behind in the other location.
    """
    return [path for path in paths if os.path.exists(path)]


def connect(paths):
    """Open an AF_UNIX stream connection, trying each path in turn.

    Returns the connected socket. Raises the last error if none succeed.
    """
    last_error = None

    for path in paths:
        open_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            open_socket.connect(path)
            return open_socket
        except (OSError, socket.error) as error:
            last_error = error
            open_socket.close()

    raise last_error if last_error else \
        RuntimeError('no socket paths supplied')
