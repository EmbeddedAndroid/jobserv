# Copyright (C) 2017 Linaro Limited
# Author: Andy Doan <andy.doan@linaro.org>

import os
import select
import subprocess
import time


def _cmd_output(cmd, cwd=None):
    '''Simple non-blocking way to stream the output of a command'''
    poller = select.poll()
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=cwd)

    fds = [p.stdout.fileno()]
    for fd in fds:
        poller.register(fd, select.POLLIN)

    while len(fds) > 0:
        events = poller.poll(0)
        if not events and p.poll() is not None:
            break
        for fd, event in events:
            if event & select.POLLIN:
                yield os.read(fd, 1024)
            elif event & select.POLLHUP:
                poller.unregister(fd)
                fds.remove(fd)
    p.wait()
    p.stdout.close()
    if p.returncode != 0:
        raise subprocess.CalledProcessError(p.returncode, cmd)


def stream_cmd(stream_cb, cmd, cwd=None):
    last_update = 0
    last_buff = b''
    try:
        for buff in _cmd_output(cmd, cwd):
            now = time.time()
            # stream data every 20s or if we have a 1M of data
            if now - last_update > 20 or len(buff) > 1048576:
                if not stream_cb(last_buff + buff):
                    last_buff += buff
                else:
                    last_buff = b''
            else:
                last_buff += buff
        if last_buff:
            if not stream_cb(last_buff):
                # Unable to stream part of command output
                raise subprocess.CalledProcessError(0, cmd)
    except subprocess.CalledProcessError as e:
        e.unstreamed = last_buff
        raise e
