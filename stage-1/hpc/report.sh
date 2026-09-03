#!/usr/bin/env bash
# What actually landed in the image, next to what versions.lock asked for.
#
# Runs once at the end of the build and again at the start of every smoke run,
# because the two answer different questions. The build-time copy proves what
# was assembled. The run-time copy proves the image that ran is the image that
# was assembled, which matters on a cluster where the image lives on one node's
# local disk and nobody watches it being built.
#
# git SHAs are compared and a mismatch is fatal: those refs are permanent, so a
# difference means the build did something other than what it was told. apt
# versions are compared and a mismatch is reported and survives, because the
# archive drops old builds and that is not the build script's fault. Which of
# the two happened is the whole point of printing both columns.

set -uo pipefail

# ROS setup files read unbound variables by design, so sourcing one under set -u
# kills the caller on the spot. Same trap that bit 05-ros2-bridge.sh and
# verify.sh, recorded in stage-1/setup/00-common.sh. Sourcing is needed here at
# all because a Containerfile RUN never goes through the base image entrypoint,
# so ros2 is not on PATH during the build.
set +u
# shellcheck disable=SC1091
[ -f /opt/ros/humble/setup.bash ] && source /opt/ros/humble/setup.bash
[ -f /root/ws_uavx/install/setup.bash ] && source /root/ws_uavx/install/setup.bash
set -u

lock() { /usr/local/bin/uavx-lock "$1"; }

status=0
sha_mismatch=0

printf '%s\n' "uavx stack, locked against installed"
printf '%s\n' "generated $(date -Is) on $(uname -n)"
printf '\n'

# ------------------------------------------------------------- apt versions
printf 'apt packages (a mismatch is recorded, not fatal)\n'
apt_row() {
  local pkg="$1" key="$2" want got mark
  want="$(lock "$key")"
  got="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || echo MISSING)"
  if [ "$want" = "$got" ]; then mark=ok; else mark=DIFFERS; fi
  printf '  %-7s %-34s locked %-42s installed %s\n' "$mark" "$pkg" "$want" "$got"
}
apt_row gazebo                       gazebo_version
apt_row ros-humble-desktop           ros_desktop_version
apt_row ros-humble-ros-core          ros_core_version
apt_row ros-humble-rclpy             ros_rclpy_version
apt_row ros-humble-rclcpp            ros_rclcpp_version
apt_row ros-humble-rmw-fastrtps-cpp  ros_rmw_fastrtps_cpp_version

printf '\n'

# --------------------------------------------------------------- git checkouts
printf 'git checkouts (a mismatch is fatal)\n'
sha_row() {
  local dir="$1" key="$2" want got mark
  want="$(lock "$key")"
  got="$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo MISSING)"
  if [ "$want" = "$got" ]; then mark=ok; else mark=FAIL; sha_mismatch=1; fi
  printf '  %-7s %-34s locked %s\n' "$mark" "$(basename "$dir")" "$want"
  printf '  %-7s %-34s   HEAD %s\n' '' '' "$got"
}
sha_row /root/PX4-Autopilot              px4_sha
sha_row /root/src/Micro-XRCE-DDS-Agent   xrce_agent_sha
sha_row /root/ws_uavx/src/px4_msgs       px4_msgs_sha
sha_row /root/ws_uavx/src/px4_ros_com    px4_ros_com_sha

printf '\n'

# ----------------------------------------------------------------- binaries
#
# Assert on files, never on package metadata. dpkg-query returns a version for a
# package apt merely knows about, so a version check passes on a machine where
# nothing was installed. This is the lesson from stage-1/setup/03-gazebo-classic.sh.
printf 'binaries that have to exist\n'
bin_row() {
  local what="$1" path
  path="$(command -v "$what" 2>/dev/null || true)"
  if [ -n "$path" ]; then
    printf '  %-7s %-34s %s\n' ok "$what" "$path"
  else
    printf '  %-7s %-34s %s\n' FAIL "$what" ABSENT
    status=1
  fi
}
bin_row gzserver
bin_row gz
bin_row MicroXRCEAgent
bin_row ros2
bin_row colcon
bin_row python3

file_row() {
  local what="$1"
  if [ -e "$what" ]; then
    printf '  %-7s %s\n' ok "$what"
  else
    printf '  %-7s %s\n' FAIL "$what"
    status=1
  fi
}
file_row /root/PX4-Autopilot/build/px4_sitl_default/bin/px4
file_row /root/PX4-Autopilot/build/px4_sitl_default/build_gazebo-classic/libgazebo_mavlink_interface.so
file_row /root/ws_uavx/install/setup.bash
file_row /opt/ros/humble/setup.bash

# gzclient is deliberately NOT asserted absent. It is installed, because it
# comes in the same package as gzserver. sitl_multi.sh is what makes sure it
# never runs.

# The same two checks stage-1/setup/verify.sh makes, for the same reason. PX4's
# own dependency script installs gz-garden on Ubuntu 22.04 and gz-tools2
# conflicts with the gazebo package, so apt resolves it by leaving the Classic
# libraries and taking the Classic binaries away. An image can look correct and
# have no gzserver.
if [ "$(dpkg -l 2>/dev/null | grep -c '^ii  gz-garden')" -eq 0 ]; then
  printf '  %-7s %s\n' ok "no gz-garden installed"
else
  printf '  %-7s %s\n' FAIL "gz-garden is installed, Gazebo Classic is compromised"
  status=1
fi
if [ ! -f /etc/apt/sources.list.d/gazebo-stable.list ]; then
  printf '  %-7s %s\n' ok "no osrfoundation apt source"
else
  printf '  %-7s %s\n' FAIL "the osrfoundation apt source is present, it serves Garden on jammy"
  status=1
fi

printf '\n'
if [ "$sha_mismatch" -ne 0 ]; then
  printf 'RESULT: a git checkout does not match versions.lock. This image is not the pinned stack.\n'
  status=1
elif [ "$status" -ne 0 ]; then
  printf 'RESULT: something the stack needs is missing. See the FAIL lines above.\n'
else
  printf 'RESULT: every SHA matches and every binary is present. Read the apt rows for archive drift.\n'
fi

exit "$status"
