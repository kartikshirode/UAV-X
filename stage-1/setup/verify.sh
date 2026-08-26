#!/usr/bin/env bash
# Checks the stack is actually there. Does not fly anything and does not
# start a simulator. Exits non-zero if any check fails or if it does not
# reach the end, so it can gate the run scripts.
#
# Two things this script must never do:
#   - run the gazebo binary. On WSL2 that kills the whole distribution.
#   - source a ROS setup file under `set -u`. Those files read unbound
#     variables by design and the shell dies mid-script, which used to make
#     this exit 0 having checked almost nothing.

. "$(dirname "$0")/00-common.sh"
set +e
set +u

FAILS=0
REACHED_END=0

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "$label"
  else
    printf '  FAIL  %s\n' "$label"
    FAILS=$((FAILS + 1))
  fi
}

# The trap has to re-exit with the captured status. A bash EXIT trap that
# returns normally clobbers the exit code, which made this script report 0
# with four checks failing, turning every gate that used it into decoration.
finish() {
  local rc=$?
  if [ "$REACHED_END" -ne 1 ]; then
    printf '\n=== verify.sh exited early, before finishing its checks.\n'
    printf '    Whatever it printed above is incomplete. Treat this as a failure.\n'
    exit 2
  fi
  exit "$rc"
}
trap finish EXIT

say "distro"
. /etc/os-release
printf '  %s %s\n' "${NAME:-?}" "${VERSION:-?}"
check "ubuntu 22.04 jammy" test "${VERSION_CODENAME:-}" = jammy

say "ros 2"
if [ -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" ]; then
  source_ros_file "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
fi
check "ros-${ROS_DISTRO_NAME} installed" test -f "/opt/ros/${ROS_DISTRO_NAME}/setup.bash"
check "ros2 cli"                          command -v ros2
check "colcon"                            command -v colcon

say "simulator"
# Assert on binaries, not on package metadata. dpkg-query -W reports a version
# for a package apt merely knows about, so a version check passed on a machine
# that had the Gazebo Classic libraries and none of its executables.
# Still never RUN these binaries: gazebo takes the WSL distro down. See header.
GZ_PKG_VER="$(dpkg-query -W -f='${Version}' gazebo 2>/dev/null)"
gz_is_11() { case "$GZ_PKG_VER" in 11.*) return 0 ;; *) return 1 ;; esac; }
no_garden() { ! dpkg -l 2>/dev/null | grep -q '^ii  gz-garden'; }

check "gzserver binary"       command -v gzserver
check "gzclient binary"       command -v gzclient
check "gazebo binary"         command -v gazebo
check "gazebo package is 11.x" gz_is_11
check "no Gazebo Garden"      no_garden
[ -n "$GZ_PKG_VER" ] && printf '  gazebo package: %s\n' "$GZ_PKG_VER"

say "px4"
check "px4 source tree"      test -d "$PX4_DIR/.git"
check "sitl binary built"    test -x "$PX4_DIR/build/px4_sitl_default/bin/px4"
check "multi-vehicle script" test -f "$PX4_DIR/Tools/simulation/gazebo-classic/sitl_multiple_run.sh"
if [ -d "$PX4_DIR/.git" ]; then
  printf '  px4 checkout: %s\n' "$(git -C "$PX4_DIR" describe --tags 2>/dev/null || echo unknown)"
fi

say "ros 2 bridge"
check "MicroXRCEAgent"   command -v MicroXRCEAgent
check "workspace built"  test -f "$WS_DIR/install/setup.bash"
if [ -f "$WS_DIR/install/setup.bash" ]; then
  source_ros_file "$WS_DIR/install/setup.bash"
  check "px4_msgs on the path" bash -c 'ros2 interface list 2>/dev/null | grep -q px4_msgs'
fi

say "display"
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
  printf '  DISPLAY=%s WAYLAND_DISPLAY=%s\n' "${DISPLAY:-unset}" "${WAYLAND_DISPLAY:-unset}"
else
  printf '  no DISPLAY and no WAYLAND_DISPLAY. Headless only, run with HEADLESS=1.\n'
fi

REACHED_END=1
printf '\n'
if [ "$FAILS" -eq 0 ]; then
  say "all checks passed"
  exit 0
fi
say "${FAILS} check(s) failed. Rerun the matching numbered script in stage-1/setup/."
exit 1
