#!/usr/bin/env bash
# Checks the stack is actually there. Does not fly anything.
# Exits non-zero if any check fails, so it can gate the run scripts.

. "$(dirname "$0")/00-common.sh"
set +e

FAILS=0
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    FAILS=$((FAILS + 1))
  fi
}

say "distro"
. /etc/os-release
printf '  %s %s\n' "${NAME:-?}" "${VERSION:-?}"
check "ubuntu 22.04 jammy" test "${VERSION_CODENAME:-}" = jammy

say "ros 2"
if [ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]; then
  # shellcheck disable=SC1090
  source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
fi
check "ros-${ROS_DISTRO_NAME} installed" test -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
check "ros2 cli"                          command -v ros2
check "colcon"                            command -v colcon

say "simulator"
# Never invoke the gazebo binary from a check. On WSL2 it crashes the whole
# distribution through the dxg GPU shim, which kills this script and every
# other shell in the distro. dpkg answers the same question safely.
check "gazebo classic"      command -v gazebo
check "gazebo is 11.x"      bash -c 'dpkg-query -W -f="\${Version}" gazebo 2>/dev/null | grep -q "^11\."'
if command -v gzserver >/dev/null 2>&1; then
  printf '  gzserver present. Headless server is the one that has to run; the\n'
  printf '  gazebo GUI client is known to take this distro down under WSL2.\n'
fi

say "px4"
check "px4 source tree"     test -d "$PX4_DIR/.git"
check "sitl binary built"   test -x "$PX4_DIR/build/px4_sitl_default/bin/px4"
check "multi-vehicle script" test -f "$PX4_DIR/Tools/simulation/gazebo-classic/sitl_multiple_run.sh"
if [ -d "$PX4_DIR/.git" ]; then
  printf '  px4 checkout: %s\n' "$(git -C "$PX4_DIR" describe --tags 2>/dev/null || echo unknown)"
fi

say "ros 2 bridge"
check "MicroXRCEAgent"      command -v MicroXRCEAgent
check "workspace built"     test -f "$WS_DIR/install/setup.bash"
if [ -f "$WS_DIR/install/setup.bash" ]; then
  # shellcheck disable=SC1090
  source "$WS_DIR/install/setup.bash"
  check "px4_msgs on the path" bash -c 'ros2 interface list 2>/dev/null | grep -q px4_msgs'
fi

say "display"
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  printf '  DISPLAY=%s WAYLAND_DISPLAY=%s\n' "${DISPLAY:-unset}" "${WAYLAND_DISPLAY:-unset}"
else
  printf '  no DISPLAY and no WAYLAND_DISPLAY. Headless only, run with HEADLESS=1.\n'
fi

printf '\n'
if [ "$FAILS" -eq 0 ]; then
  say "all checks passed"
  exit 0
fi
say "${FAILS} check(s) failed. Rerun the matching numbered script in stage-1/setup/."
exit 1
