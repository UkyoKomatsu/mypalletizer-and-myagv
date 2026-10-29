# ROS 2 LAN networking

Run from PowerShell in C:\Users\siron\mypalletizer-and-myagv\Docker:

```powershell
docker compose up
docker compose exec ros2_galactic bash
```

The selected Docker context is ros2-lan: native Docker Engine in Ubuntu WSL,
using networkingMode=mirrored. The container uses Linux host networking.
No per-robot IP, SSH session, or manual DDS source command is needed.
Peers must run ROS 2 on the same LAN, ROS_DOMAIN_ID=0, ROS_LOCALHOST_ONLY=0.

## Startup

container-entrypoint.sh generates /run/ros-network/cyclonedds.xml with CycloneDDS
automatic interface selection, then invokes the project entrypoint. It does not
pin an IP address from container startup.
CycloneDDS SPDP multicast handles discovery; normal data uses unicast.
ROS_NETWORK_INTERFACE may override the selected local interface if required.

watch-daemon.py runs inside the container and discards only this domain's CLI
discovery daemon when the LAN IPv4 addresses change or two bounded probes fail.
The next ros2 command starts a fresh daemon. This handles the Galactic daemon
hanging after a Wi-Fi change; it does not stop or restart application nodes.
An in-flight CLI request can fail during recovery; rerun it after a few seconds.
Application nodes that were running during a network switch may need restarting.

The existing workspace build is reused. After source edits, run colcon build;
ROS_BUILD_ON_START=1 restores the previous build-on-each-start behavior.

## Host setup already performed

- Native Docker CE installed in WSL Ubuntu. The previous Docker Desktop engine
  and its images/containers remain available through context desktop-linux.
- Docker API listens only on 127.0.0.1:2376, with client/server certificate
  verification. Certificates for the Windows CLI are stored in its ros2-lan
  context; the local backup .local-engine is excluded from Git.
- A Windows sign-in task, ROS2-Docker-WSL, starts the engine and keeps WSL alive.
  It runs hidden and also works on battery power.
- Windows and Hyper-V firewall rules permit local-subnet UDP 7400-7500,
  matching domain 0 with the configured participant indexes.
- Windows bind mounts use /mnt/c paths as seen by the native Linux daemon.

Docker Desktop is not required for this project. Its UI does not show the
native engine's containers. Use docker compose ps/logs instead.
If another program changes the selected context, select ros2-lan again once.

## Verification and limits

- Container and Windows share the current LAN IPv4 address.
- Independent ROS talker/listener containers automatically discovered each
  other, and the main container saw both endpoints after its own recreation.
- Compose creation/start was measured at 0.9 seconds with an existing build.
- Windows multicast arrived in the container, and outgoing container
  multicast was captured on the physical Wi-Fi interface.
- Same-PC Windows multicast loopback reception did not work in the probe;
  see https://github.com/microsoft/WSL/issues/12344 for the WSL limitation.
  No route workaround was applied that would interfere with LAN multicast.
- After the Wi-Fi address changed, topic discovery, the myAGV node names and
  live /odom reception were confirmed on 2026-10-26.
- A deliberately suspended CLI daemon was removed automatically in 9.1 seconds;
  normal topic discovery was then checked again.
- No velocity commands were published.

Legacy SSH helpers were archived to this task's work folder. The old robot-side
helper and restricted authorized key remain on the powered-off robot but are
not used by this configuration. They can be removed when the robot is available.
