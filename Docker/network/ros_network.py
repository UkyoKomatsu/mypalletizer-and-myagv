#!/usr/bin/env python3
"""A narrow IPv4 tunnel for ROS 2 between an existing Docker container and myAGV.

Uses the existing SSH authentication and sudo; changes no SSH/firewall settings.
Only UDP to the robot's LAN address is routed through the temporary interface.
The robot's running ROS nodes and LAN interface remain unchanged.
"""
import argparse
import fcntl
import ipaddress
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import time

IFACE = 'rosagv0'
CLIENT_IP = '10.254.77.2'
SERVER_IP = '10.254.77.1'
RULE_PRIORITY = 10203
TABLE = 203


def attr(kind, value):
    size = len(value) + 4
    return struct.pack('HH', size, kind) + value + bytes((-size) % 4)


def netlink(kind, payload, create=True):
    flags = 1 | 4 | (0x400 | 0x200 if create else 0)
    message = struct.pack('IHHII', 16 + len(payload), kind, flags, 1, 0) + payload
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0) as sock:
        sock.bind((0, 0))
        sock.send(message)
        response = sock.recv(65536)
    if struct.unpack_from('H', response, 4)[0] == 2:
        status = struct.unpack_from('i', response, 16)[0]
        if status:
            raise OSError(-status, os.strerror(-status))


def udp_rule(robot, add=True):
    header = struct.pack('BBBBBBBBI', 2, 32, 0, 0, TABLE, 0, 0, 1, 0)
    payload = header + attr(1, socket.inet_aton(robot))
    payload += attr(6, struct.pack('I', RULE_PRIORITY)) + attr(22, bytes([17]))
    netlink(32 if add else 33, payload, add)


def configure_tun(address, robot=None):
    fd = os.open('/dev/net/tun', os.O_RDWR)
    try:
        fcntl.ioctl(fd, 0x400454ca, struct.pack('16sH', IFACE.encode(), 1 | 0x1000))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as ctl:
            for operation, value in [(0x8916, address), (0x891c, '255.255.255.252')]:
                req = struct.pack('16sH2s4s8s', IFACE.encode(), 2, bytes(2), socket.inet_aton(value), bytes(8))
                fcntl.ioctl(ctl, operation, req)
            fcntl.ioctl(ctl, 0x8922, struct.pack('16si', IFACE.encode(), 1400))
            flags = fcntl.ioctl(ctl, 0x8913, struct.pack('16sH', IFACE.encode(), 0))
            old = struct.unpack_from('H', flags, 16)[0]
            fcntl.ioctl(ctl, 0x8914, struct.pack('16sH', IFACE.encode(), old | 1))
        if robot:
            route = struct.pack('BBBBBBBBI', 2, 32, 0, 0, TABLE, 4, 253, 1, 0)
            route += attr(1, socket.inet_aton(robot))
            route += attr(4, struct.pack('I', socket.if_nametoindex(IFACE)))
            route += attr(7, socket.inet_aton(address))
            netlink(24, route)
            udp_rule(robot)
        return fd
    except BaseException:
        os.close(fd)
        raise


def valid_packet(packet, server, robot):
    if len(packet) < 20 or packet[0] >> 4 != 4 or packet[9] not in (1, 17):
        return False
    src = socket.inet_ntoa(packet[12:16])
    dst = socket.inet_ntoa(packet[16:20])
    if server:
        return src == CLIENT_IP and dst in (SERVER_IP, robot)
    return dst == CLIENT_IP and src in (SERVER_IP, robot)


def pump(tun, reader, writer, server, robot, child=None, on_receive=None):
    pending = bytearray()
    last_send = last_receive = time.monotonic()
    tx = rx = 0
    discovery = None
    if server:
        discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        discovery.bind((robot, 0))
        discovery.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(robot))
        discovery.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        discovery.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    print('TUNNEL_READY ' + ('server' if server else 'client'), file=sys.stderr, flush=True)
    try:
        if server:
            writer(bytes(4))
        while True:
            ready, _, _ = select.select([tun, reader], [], [], 1)
            now = time.monotonic()
            if child is not None and child.poll() is not None:
                raise ConnectionError('SSH process exited')
            if now - last_receive > 20:
                raise ConnectionError('SSH tunnel heartbeat timed out')
            if reader in ready:
                data = os.read(reader, 65536)
                if not data:
                    return
                last_receive = now
                if on_receive is not None:
                    on_receive()
                pending.extend(data)
                while len(pending) >= 4:
                    size = struct.unpack_from('!I', pending)[0]
                    if size > 65535:
                        raise ValueError('Invalid tunnel frame')
                    if len(pending) < 4 + size:
                        break
                    packet = bytes(pending[4:4 + size])
                    del pending[:4 + size]
                    if size and valid_packet(packet, server, robot):
                        ihl = (packet[0] & 15) * 4
                        # UDP unicast to a shared 7400 socket reaches just one
                        # ROS process. Fan SPDP out through the robot's normal
                        # multicast group so all already-running nodes see it.
                        if (server and packet[9] == 17 and len(packet) >= ihl + 8
                                and struct.unpack_from('!H', packet, ihl + 2)[0] == 7400
                                and packet[ihl + 8:ihl + 12] == b'RTPS'):
                            discovery.sendto(packet[ihl + 8:], ('239.255.0.1', 7400))
                        else:
                            os.write(tun, packet)
                        rx += 1
            if tun in ready:
                packet = os.read(tun, 65535)
                # The opposite side validates the packet before injecting it.
                writer(struct.pack('!I', len(packet)) + packet)
                last_send = now
                tx += 1
            if now - last_send >= 5:
                writer(bytes(4))
                last_send = now
    finally:
        if discovery is not None:
            discovery.close()
        print('TUNNEL_COUNTS tx=%d rx=%d' % (tx, rx), file=sys.stderr, flush=True)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        view = view[count:]

