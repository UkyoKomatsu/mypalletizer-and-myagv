#!/usr/bin/env python3
"""Use the native Linux host LAN for normal ROS 2 multicast discovery."""
import os
from pathlib import Path
from xml.sax.saxutils import escape

interface = os.environ.get('ROS_NETWORK_INTERFACE', '').strip() or 'auto'
if interface.startswith('127.'):
    raise RuntimeError('No LAN interface is available')
state = Path('/run/ros-network')
state.mkdir(parents=True, exist_ok=True)
xml = '''<?xml version="1.0"?>
<CycloneDDS xmlns="https://cdds.io/config"><Domain id="any">
  <General>
    <NetworkInterfaceAddress>%s</NetworkInterfaceAddress>
    <AllowMulticast>spdp</AllowMulticast>
  </General>
  <Discovery>
    <ParticipantIndex>auto</ParticipantIndex>
    <MaxAutoParticipantIndex>30</MaxAutoParticipantIndex>
  </Discovery>
</Domain></CycloneDDS>
''' % escape(interface)
(state / 'cyclonedds.xml').write_text(xml)
print('[ROS 2] LAN interface selection: %s; multicast discovery enabled; domain %s' %
      (interface, os.environ.get('ROS_DOMAIN_ID', '0')), flush=True)
