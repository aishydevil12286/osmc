# -*- coding: utf-8 -*-
"""
    Copyright (C) 2014-2020 OSMC (KodeKarnage)

    This file is part of script.module.osmcsetting.updates

    SPDX-License-Identifier: GPL-2.0-or-later
    See LICENSES/GPL-2.0-or-later for more information.
"""

import json
import socket
import sys
from contextlib import closing


# Kept in step with osmccommon.osmc_paths. This script is launched as a bare
# `python3 <script>` with no Kodi addon sys.path, so it cannot import that
# module. tests/test_run_osmc_sockets.py asserts the two stay identical.
UPDATE_SOCKET_PATHS = [
    '/run/osmc/settings-update.sock',
    '/var/tmp/osmc.settings.update.sockfile',
]


def connect_socket(paths):
    """Connect to the first reachable path, newest location first."""
    last_error = None

    for path in paths:
        open_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            open_socket.connect(path)
            return open_socket
        except (OSError, socket.error) as error:
            last_error = error
            open_socket.close()

    raise last_error


def argv():
    return sys.argv


if len(argv()) > 1:
    msg = argv()[1]
    print('OSMC settings sending response, %s' % msg)

    message = ('settings_command', {
        'action': msg
    })
    message = json.dumps(message)

    with closing(connect_socket(UPDATE_SOCKET_PATHS)) as open_socket:
        if not isinstance(message, (bytes, bytearray)):
            message = message.encode('utf-8', 'ignore')
        open_socket.sendall(message)

    print('OSMC settings sent response, %s' % msg)
