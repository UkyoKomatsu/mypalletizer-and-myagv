# myAGV automatic ROS 2 network

On this PC, run `docker compose up` from `C:\Users\siron\mypalletizer-and-myagv\Docker`.
The robot must be powered on and connected to the same network.

Compose supplies DDS environment variables to every container process. Its
entrypoint starts a supervised SSH tunnel using a dedicated key. No password
prompt or manual source is required. A stopped robot ROS launch is started
automatically; already-running robot nodes are reused. Reconnection keeps the
local interface stable for running subscribers.

## Files and lifecycle

- Compose mounts this directory read-only. The private key is a Compose secret
  from `../.local-network/id_ed25519`, excluded from Git. Back it up privately
  when moving the project.
- `container-entrypoint.sh` starts the supervisor and the project setup.
- `supervisor.py` generates `/run/myagv-network/cyclonedds.xml`, maintains
  SSH, and reports health from recent tunnel traffic.
- `ros_network.py` carries UDP to the robot over the authenticated SSH channel.
- Robot files are root-owned in `/usr/local/lib/myagv-docker-network`. The
  dedicated authorized key is restricted to `robot.py`; it cannot open an
  interactive shell or SSH port forwards. Host key verification stays on.
- SSH public-key authentication was enabled only for user er. The previous
  config is `/etc/ssh/sshd_config.before-myagv-compose-20261023`.
- Robot ROS launch logs are `/var/log/myagv-docker-network/ros.log`.

Compose down closes the tunnel and removes its interface and UDP rule. Robot
ROS nodes continue running, so a later Compose startup reuses them. Networking
and verification never publish velocity commands.

## Status

```powershell
docker compose ps
docker compose logs --tail 40
docker exec -it ros2_galactic bash
```

Inside bash, `ros2 topic echo /odom` works directly. Health covers the tunnel;
the node and odometry paths were separately verified with real messages.

The configured robot IP is 192.168.128.249, ROS domain 0. If it changes, update
MYAGV_HOST in the Compose environment or .env and update known_hosts after
verifying the robot's SSH host key. For a different PC/robot, provision its
dedicated key and root-owned helper once. Private keys are not in the repository.

## Validation (2026-10-23)

- Removed and recreated the container without manual networking.
- Stopped the original robot ROS launch; Compose started a new one.
- A new bash inherited DDS settings and saw /odom and /cmd_vel.
- Interrupted SSH while subscribers remained active: reconnection succeeded
  with the same local interface index.
- During that 25-second test, Docker received 259 real odometry messages;
  dedicated String topics arrived both ways (12 and 13 messages).
- The existing task publisher retained its SHA-256 across startup.

The project entrypoint applies its existing patch idempotently and preserves
working-tree edits instead of resetting/cleaning Git on every startup.
The mandatory joystick mapping was removed because /dev is already mounted in
privileged mode and an absent /dev/input/js0 otherwise prevents recreation.
