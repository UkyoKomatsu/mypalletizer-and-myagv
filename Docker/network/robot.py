#!/usr/bin/env python3
"""Forced SSH command: start ROS if needed and serve the restricted UDP tunnel."""
import fcntl
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time

from ros_network import SERVER_IP, configure_tun, pump, write_all


def driver_running():
    for proc in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            executable = proc.read_bytes().split(b'\0', 1)[0]
        except (OSError, ProcessLookupError):
            continue
        if executable.endswith(b'/myagv_odometry_node'):
            return True
    return False


def ensure_ros():
    if driver_running():
        return
    log_dir = Path('/var/log/myagv-docker-network')
    log_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    lock = open('/run/myagv-docker-ros.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if driver_running():
            return
        # A reconnect must not create a second launch while an earlier one starts.
        pid_file = Path('/run/myagv-docker-ros.pid')
        if pid_file.exists():
            try:
                cmd = Path('/proc/%d/cmdline' % int(pid_file.read_text())).read_bytes()
                if b'myagv_active.launch.py' in cmd:
                    for _ in range(100):
                        if driver_running():
                            return
                        time.sleep(0.1)
                    raise RuntimeError('Existing ROS launch has no odometry node; inspect the ROS log')
            except (OSError, ValueError):
                pass
        print('[myAGV] Starting ROS nodes.', file=sys.stderr, flush=True)
        script = ('source /opt/ros/galactic/setup.bash; '
                  'source /home/er/myagv_ros2/install/setup.bash; '
                  'export ROS_DOMAIN_ID=0 ROS_LOCALHOST_ONLY=0 RMW_IMPLEMENTATION=rmw_cyclonedds_cpp; '
                  'unset CYCLONEDDS_URI; '
                  'exec ros2 launch myagv_odometry myagv_active.launch.py')
        with (log_dir / 'ros.log').open('ab', buffering=0) as log:
            child = subprocess.Popen(['/usr/sbin/runuser', '-u', 'er', '--', '/bin/bash', '-c', script],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
        pid_file.write_text(str(child.pid))
        for _ in range(150):
            if driver_running():
                return
            if child.poll() is not None:
                raise RuntimeError('ROS launch exited; inspect /var/log/myagv-docker-network/ros.log')
            time.sleep(0.1)
        raise RuntimeError('ROS did not start in time; inspect the ROS log')
    finally:
        lock.close()


def main():
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    ensure_ros()
    # Use the actual Wi-Fi address; no password or arbitrary SSH command is accepted.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        value = fcntl.ioctl(sock, 0x8915, struct.pack('256s', b'wlan0'))
        robot = socket.inet_ntoa(value[20:24])
    tun = configure_tun(SERVER_IP)
    try:
        pump(tun, 0, lambda data: write_all(1, data), True, robot)
    finally:
        os.close(tun)


if __name__ == '__main__':
    main()
