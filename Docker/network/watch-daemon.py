#!/usr/bin/env python3
"""Discard stale CLI discovery daemons without touching application nodes."""
import fcntl
import logging
import os
from pathlib import Path
import signal
import socket
import struct
import time
from xmlrpc.client import ServerProxy, Transport


class BoundedTransport(Transport):
    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = 3
        return connection


def addresses():
    result = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for _, name in socket.if_nameindex():
            if name == 'lo' or name.startswith(('veth', 'docker', 'br-')):
                continue
            try:
                info = fcntl.ioctl(sock, 0x8915, struct.pack('256s', name.encode()[:15]))
                result.append((name, socket.inet_ntoa(info[20:24])))
            except OSError:
                pass
    return tuple(sorted(result))


def daemons(domain):
    executable = '/opt/ros/%s/bin/_ros2_daemon' % os.environ['ROS_DISTRO']
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc / 'cmdline').read_bytes().decode().split('\0')
            if executable in args and args[args.index('--ros-domain-id') + 1] == str(domain):
                yield int(proc.name)
        except (OSError, ValueError, IndexError):
            continue


def main():
    state = Path('/run/ros-network')
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'watch-daemon.pid').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        logging.basicConfig(level=logging.INFO, format='[ROS 2] %(message)s', handlers=[
            logging.StreamHandler(), logging.FileHandler(state / 'watch-daemon.log')])
        domain = int(os.environ.get('ROS_DOMAIN_ID', '0'))
        previous = addresses()
        failures = 0
        logging.info('CLI daemon recovery enabled')
        while True:
            current = addresses()
            pids = list(daemons(domain))
            changed = current != previous
            previous = current
            if pids and not changed:
                try:
                    with ServerProxy('http://127.0.0.1:%d/ros2cli/' % (11511 + domain),
                                     transport=BoundedTransport(), allow_none=True) as proxy:
                        proxy.get_topic_names_and_types()
                    failures = 0
                except Exception as error:
                    # Two failed probes allow a newly starting daemon time to initialize.
                    failures += 1
                    logging.warning('CLI daemon probe failed: %s', error)
            else:
                failures = 0
            if pids and (changed or failures >= 2):
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        continue
                    logging.info('Discarded CLI daemon %s (%s); next CLI call recreates it',
                                 pid, 'LAN address changed' if changed else 'unresponsive')
                failures = 0
            time.sleep(3)


if __name__ == '__main__':
    main()
