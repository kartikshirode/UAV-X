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
# grep -c. With grep -q this returned "no garden" whenever garden WAS present,
# because the SIGPIPE exit inverted through the leading bang.
no_garden() { [ "$(dpkg -l 2>/dev/null | grep -c '^ii  gz-garden' || true)" -eq 0 ]; }

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
  px4_msgs_present() { [ "$(ros2 interface list 2>/dev/null | grep -c px4_msgs || true)" -gt 0 ]; }
  check "px4_msgs on the path" px4_msgs_present
fi

say "version lock"
# Round 2 finding 11: the scripts resolved "newest v1.15.*" and tracked moving
# release branches, so a fresh install in September could build a different
# stack from the one every result in this repo came off. Compare, do not assume.
LOCK="$(dirname "$0")/versions.lock"
if [ ! -f "$LOCK" ]; then
  printf '  FAIL  versions.lock missing\n'
  FAILS=$((FAILS + 1))
else
  lock_get() { grep -E "^$1=" "$LOCK" | head -1 | cut -d= -f2-; }
  cmp_ver() {
    local label="$1" want="$2" got="$3"
    if [ "$want" = "$got" ]; then
      printf '  ok    %-22s %s\n' "$label" "$got"
    else
      printf '  FAIL  %-22s locked %s, found %s\n' "$label" "$want" "$got"
      FAILS=$((FAILS + 1))
    fi
  }
  cmp_ver "px4 sha"      "$(lock_get px4_sha)"      "$(git -C "$PX4_DIR" rev-parse HEAD 2>/dev/null)"
  cmp_ver "xrce agent sha" "$(lock_get xrce_agent_sha)" "$(git -C "$HOME/src/Micro-XRCE-DDS-Agent" rev-parse HEAD 2>/dev/null)"
  cmp_ver "px4_msgs sha"  "$(lock_get px4_msgs_sha)"  "$(git -C "$WS_DIR/src/px4_msgs" rev-parse HEAD 2>/dev/null)"
  cmp_ver "px4_ros_com sha" "$(lock_get px4_ros_com_sha)" "$(git -C "$WS_DIR/src/px4_ros_com" rev-parse HEAD 2>/dev/null)"
  cmp_ver "gazebo"        "$(lock_get gazebo_version)" "$GZ_PKG_VER"
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
