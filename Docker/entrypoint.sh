#!/bin/bash
set -e

source /opt/ros/galactic/setup.bash

# repoがなければclone
for DIR in eai_task_server sml_messages; do
  if [ ! -d /root/git/$DIR ]; then
    cd /root/git
    git clone https://github.com/robocup-sml/$DIR
  fi
done
if [ ! -d /root/git/rcll ]; then
  cd /root/git
  git clone https://github.com/babytigers-r/rcll
fi

# symlink作成
mkdir -p /root/colcon_ws/src
for DIR in eai_task_server sml_messages; do
  if [ ! -L /root/colcon_ws/src/$DIR ]; then
    ln -s /root/git/$DIR /root/colcon_ws/src/$DIR
  fi
done

# patch
cd /root/git/eai_task_server
PATCH_FILE=/root/git/rcll/robots/myAGV/eai_task_server.patch
if git apply --check "$PATCH_FILE" 2>/dev/null; then
  git apply "$PATCH_FILE"
elif git apply --reverse --check "$PATCH_FILE" 2>/dev/null; then
  echo "eai_task_server patch is already applied"
else
  echo "eai_task_server patch conflicts with the working tree; files were preserved" >&2
  exit 1
fi

# if [ ! -L /root/colcon_ws/src/myagv_navigation ]; then
#   ln -s /root/git/rcll/myAGV /root/colcon_ws/src/myagv_navigation
# fi

# build
cd /root/colcon_ws
colcon build

exec "$@"
