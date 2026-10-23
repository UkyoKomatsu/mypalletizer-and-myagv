#!/bin/bash
set -e

mkdir -p /run/myagv-network
chmod 700 /run/myagv-network
install -m 600 /run/secrets/myagv_network_key /run/myagv-network/id_ed25519
rm -f /run/myagv-network/cyclonedds.xml /run/myagv-network/ready.json
python3 -u /opt/myagv-network/supervisor.py &
network_pid=$!
project_pid=''
cleanup() {
    trap - EXIT INT TERM
    if [ -n "$project_pid" ]; then kill -TERM "$project_pid" 2>/dev/null || true; fi
    kill -TERM "$network_pid" 2>/dev/null || true
    wait "$network_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# The interface and DDS profile exist before any ROS application starts.
for attempt in {1..50}; do
    [ -f /run/myagv-network/cyclonedds.xml ] && break
    if ! kill -0 "$network_pid" 2>/dev/null; then
        echo '[myAGV] Network startup failed.' >&2
        exit 1
    fi
    sleep 0.1
done
test -f /run/myagv-network/cyclonedds.xml

if [ "$#" -eq 0 ]; then set -- bash; fi
/bin/bash /entrypoint.sh "$@" <&0 &
project_pid=$!
wait "$project_pid"
