#!/usr/bin/env python3
"""Maintain the ROS network while the Compose container is running."""
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from ros_network import CLIENT_IP, IFACE, configure_tun, pump, udp_rule, write_all

STATE = Path('/run/myagv-network')
READY = STATE / 'ready.json'


def mark_ready(robot):
    temp = READY.with_suffix('.tmp')
    temp.write_text(json.dumps({'robot': robot, 'last_received': time.time()}))
    temp.replace(READY)


def health():
    try:
        state = json.loads(READY.read_text())
        return Path('/sys/class/net/' + IFACE).exists() and time.time() - state['last_received'] < 15
    except (OSError, ValueError, KeyError):
        return False


def terminate(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'health':
        return 0 if health() else 1
    def stop(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    host = os.environ.get('MYAGV_HOST', '192.168.128.249')
    robot = str(ipaddress.IPv4Address(socket.gethostbyname(host)))
    user = os.environ.get('MYAGV_USER', 'er')
    if not user.replace('_', '').replace('-', '').isalnum():
        raise ValueError('Invalid SSH user')
    if Path('/sys/class/net/' + IFACE).exists():
        raise RuntimeError('Another myAGV tunnel already owns ' + IFACE)
    # Only this exact rule is owned by this service; recover it after SIGKILL.
    try:
        udp_rule(robot, False)
    except OSError as error:
        if error.errno != 2:
            raise
    tun = configure_tun(CLIENT_IP, robot)
    xml = '''<?xml version="1.0"?>
<CycloneDDS xmlns="https://cdds.io/config"><Domain id="any">
<General><NetworkInterfaceAddress>10.254.77.2</NetworkInterfaceAddress><AllowMulticast>false</AllowMulticast></General>
<Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>30</MaxAutoParticipantIndex>
<Peers><Peer Address="%s:7400"/><Peer Address="10.254.77.2"/></Peers></Discovery>
</Domain></CycloneDDS>
''' % robot
    (STATE / 'cyclonedds.xml').write_text(xml)
    command = ['ssh', '-T', '-i', str(STATE / 'id_ed25519'),
               '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=/opt/myagv-network/known_hosts',
               '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1',
               '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3',
               '-o', 'PreferredAuthentications=publickey', user + '@' + host,
               'myagv-network']
    child = None
    try:
        while True:
            READY.unlink(missing_ok=True)
            print('[myAGV] Connecting to ' + host, flush=True)
            try:
                child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
                pump(tun, child.stdout.fileno(), lambda data: write_all(child.stdin.fileno(), data),
                     False, robot, child, lambda: mark_ready(robot))
                print('[myAGV] Connection closed; reconnecting.', flush=True)
            except (OSError, ConnectionError, ValueError) as error:
                print('[myAGV] %s; retrying in 3 seconds.' % error, file=sys.stderr, flush=True)
            finally:
                READY.unlink(missing_ok=True)
                terminate(child)
                child = None
            time.sleep(3)
    finally:
        READY.unlink(missing_ok=True)
        terminate(child)
        try:
            udp_rule(robot, False)
        except OSError:
            pass
        os.close(tun)


if __name__ == '__main__':
    raise SystemExit(main())
