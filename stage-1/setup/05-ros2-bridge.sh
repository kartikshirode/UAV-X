#!/usr/bin/env bash
# uXRCE-DDS agent plus the px4_msgs and px4_ros_com workspace.
# This is what puts PX4 uORB topics onto ROS 2.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did bridge; then say "bridge already done, skipping"; exit 0; fi

wait_for_net github.com

if ! command -v MicroXRCEAgent >/dev/null 2>&1; then
  say "building the Micro XRCE-DDS Agent at ${XRCE_TAG}"
  SRC="$HOME/src/Micro-XRCE-DDS-Agent"
  mkdir -p "$HOME/src"
  [ -d "$SRC/.git" ] || git clone -b "$XRCE_TAG" https://github.com/eProsima/Micro-XRCE-DDS-Agent.git "$SRC"
  mkdir -p "$SRC/build"
  cd "$SRC/build"
  cmake ..
  make -j"$(nproc)"
  sudo make install
  sudo ldconfig /usr/local/lib/
else
  say "MicroXRCEAgent already on PATH"
fi

command -v MicroXRCEAgent >/dev/null 2>&1 || die "MicroXRCEAgent still not on PATH after install"

say "ros2 workspace at ${WS_DIR}"
mkdir -p "$WS_DIR/src"
cd "$WS_DIR/src"

clone_px4_repo() {
  local repo="$1" dir="$2"
  [ -d "$dir/.git" ] && { say "$dir already cloned"; return 0; }
  # The release branch tracks the matching PX4 version. Fall back to main if it is absent.
  git clone -b release/1.15 "https://github.com/PX4/${repo}.git" "$dir" 2>/dev/null || {
    warn "release/1.15 not found for ${repo}, falling back to main. Message definitions may drift from PX4 v1.15."
    git clone "https://github.com/PX4/${repo}.git" "$dir"
  }
}

clone_px4_repo px4_msgs px4_msgs
clone_px4_repo px4_ros_com px4_ros_com

cd "$WS_DIR"
# shellcheck disable=SC1090
source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
say "colcon build"
colcon build --symlink-install || die "colcon build failed"

if ! grep -q "source ${WS_DIR}/install/setup.bash" "$HOME/.bashrc"; then
  echo "source ${WS_DIR}/install/setup.bash" >> "$HOME/.bashrc"
fi

done_with bridge
say "bridge done"
