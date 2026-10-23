#!/usr/bin/env python3
"""Verify real odometry and two-way String messages; never sends motion commands."""
import argparse
import json
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String

parser = argparse.ArgumentParser()
parser.add_argument('--side', choices=['docker', 'robot'], required=True)
parser.add_argument('--run-id', required=True)
args = parser.parse_args()
peer = 'robot' if args.side == 'docker' else 'docker'
rclpy.init()
node = rclpy.create_node('network_check_' + args.side)
publisher = node.create_publisher(String, '/myagv_network_check/from_' + args.side, 10)
stats = {'side': args.side, 'received_from_peer': 0, 'odom_samples': 0, 'odom_first': None, 'odom_last': None}


def receive(message):
    if message.data == args.run_id:
        stats['received_from_peer'] += 1


def odometry(message):
    stats['odom_samples'] += 1
    stamp = message.header.stamp.sec + message.header.stamp.nanosec / 1e9
    if stats['odom_first'] is None:
        stats['odom_first'] = stamp
    stats['odom_last'] = stamp
    stats['odom_frame'] = message.header.frame_id


subscription = node.create_subscription(String, '/myagv_network_check/from_' + peer, receive, 10)
odom_subscription = None
if args.side == 'docker':
    odom_subscription = node.create_subscription(Odometry, '/odom', odometry, qos_profile_sensor_data)


def publish():
    message = String()
    message.data = args.run_id
    publisher.publish(message)


timer = node.create_timer(0.5, publish)
deadline = time.monotonic() + 25
try:
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)
    stats['success'] = stats['received_from_peer'] >= 3 and (args.side == 'robot' or stats['odom_samples'] >= 20)
    if stats['odom_samples'] > 1 and stats['odom_last'] > stats['odom_first']:
        stats['odom_hz'] = round((stats['odom_samples'] - 1) / (stats['odom_last'] - stats['odom_first']), 2)
    print(json.dumps(stats), flush=True)
finally:
    node.destroy_node()
    rclpy.shutdown()
raise SystemExit(0 if stats['success'] else 1)
