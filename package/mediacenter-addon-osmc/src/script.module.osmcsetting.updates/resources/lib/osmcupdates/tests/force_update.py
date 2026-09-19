# -*- coding: utf-8 -*-
"""
    Copyright (C) 2014-2020 OSMC (KodeKarnage)

    This file is part of script.module.osmcsetting.updates

    SPDX-License-Identifier: GPL-2.0-or-later
    See LICENSES/GPL-2.0-or-later for more information.

    This script is run as root by the osmc update module.
"""

import json
import socket
import sys
from contextlib import closing
from datetime import datetime


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


def call_parent(raw_message, data=None):
    print('%s %s sending response' % (datetime.now(), 'apt_cache_action.py'))
    if data is None:
        data = {}
    message = (raw_message, data)
    message = json.dumps(message)

    try:
        with closing(connect_socket(UPDATE_SOCKET_PATHS)) as open_socket:
            if not isinstance(message, (bytes, bytearray)):
                message = message.encode('utf-8', 'ignore')
            open_socket.sendall(message)

    except Exception as e:
        print('%s %s failed to connect to parent - %s' % (datetime.now(), 'apt_cache_action.py', e))

    print('%s %s response sent' % (datetime.now(), 'apt_cache_action.py'))


if __name__ == "__main__":
    if len(argv()) > 1:
        call_parent(str(argv()[1]))
