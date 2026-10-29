#!/bin/bash
set -e

python3 /opt/ros-network/setup-dds.py
python3 /opt/ros-network/watch-daemon.py &
if [ "$#" -eq 0 ]; then set -- bash; fi
exec /bin/bash /entrypoint.sh "$@"
