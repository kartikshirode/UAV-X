#!/usr/bin/env bash
# The environment every gate runs in. Sourced, never executed directly.
#
# Why this file exists. The loop config used to say "~/.bashrc already sources
# ROS and bash -lc picks it up". It does not. Ubuntu's .bashrc opens with
#
#     case $- in *i*) ;; *) return;; esac
#
# so a non-interactive shell returns before reaching any appended line. Checked
# on this machine: `wsl.exe -d Ubuntu-22.04 -- bash -lc 'command -v ros2'`
# prints nothing and AMENT_PREFIX_PATH is unset. Every gate would have failed on
# the first tick. Nothing may rely on an inherited ROS environment again.
#
# Build output deliberately does NOT live in the repo. Source stays on the
# Windows filesystem so it is under git; build and install trees go to ext4,
# because sustained work off the /mnt/c 9P mount drops its connection and takes
# the build with it.

# shellcheck disable=SC2155

# Resolve the repo from this file, not from the caller's cwd.
UAVX_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export UAVX_REPO="$(cd "$UAVX_SCRIPT_DIR/.." && pwd)"

export ROS_DISTRO_NAME="${ROS_DISTRO_NAME:-humble}"
export UAVX_WS_SRC="${UAVX_WS_SRC:-$UAVX_REPO/uavx_ws}"
export UAVX_BUILD_BASE="${UAVX_BUILD_BASE:-$HOME/uavx-build}"
export UAVX_INSTALL_BASE="${UAVX_INSTALL_BASE:-$HOME/uavx-install}"
export UAVX_PX4_DIR="${UAVX_PX4_DIR:-$HOME/PX4-Autopilot}"
export UAVX_DEPS_WS="${UAVX_DEPS_WS:-$HOME/ws_uavx}"
export UAVX_RUNS_DIR="${UAVX_RUNS_DIR:-$UAVX_REPO/runs}"

gsay()  { printf '\n--- %s\n' "$*"; }
gwarn() { printf '\n!!! %s\n' "$*" >&2; }
gdie()  { printf '\nGATE FAILED: %s\n' "$*" >&2; exit 1; }

# ROS setup files read unbound variables on purpose, so sourcing one under
# `set -u` kills the caller. Save the flag, source, put it back.
# Passes trailing arguments through to the sourced file. setup_gazebo.bash takes
# src_path and build_path positionally, and swallowing them leaves
# GAZEBO_PLUGIN_PATH unset, so gzserver starts but cannot load the PX4 plugins
# and the vehicles never connect.
uavx_source() {
  local f="$1"; shift
  [ -f "$f" ] || return 1
  local had_u=0
  case "$-" in *u*) had_u=1 ;; esac
  set +u
  # shellcheck disable=SC1090
  source "$f" "$@"
  [ "$had_u" -eq 1 ] && set -u
  return 0
}

uavx_load_env() {
  uavx_source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" \
    || gdie "no ROS at /opt/ros/${ROS_DISTRO_NAME}. Run stage-1/setup/setup-all.sh."
  uavx_source "${UAVX_DEPS_WS}/install/setup.bash" \
    || gdie "no dependency workspace at ${UAVX_DEPS_WS}. Run stage-1/setup/05-ros2-bridge.sh."
  # The repo overlay only exists once our packages have been built. Absent is
  # fine before W1; uavx_require_overlay is what insists on it.
  uavx_source "${UAVX_INSTALL_BASE}/setup.bash" || true
}

# Assertions. Each gate calls the ones it actually needs, so a W1 gate does not
# fail for want of a package W3 introduces.
uavx_require_ros() {
  command -v ros2 >/dev/null 2>&1 || gdie "ros2 not on PATH after sourcing. AMENT_PREFIX_PATH=[${AMENT_PREFIX_PATH:-unset}]"
  command -v colcon >/dev/null 2>&1 || gdie "colcon not on PATH"
  [ -n "${AMENT_PREFIX_PATH:-}" ] || gdie "AMENT_PREFIX_PATH is empty, the ROS environment did not load"
}

# grep -c, never grep -q. Under `set -o pipefail` a `grep -q` closes the pipe on
# its first match, the producer takes SIGPIPE and exits 141, and pipefail turns
# a passing check into a failing one. That bug already cost this repo a build
# cycle once through `gazebo --version | head -1`. grep -c reads to EOF.
uavx_require_px4_msgs() {
  local n
  n="$(ros2 interface list 2>/dev/null | grep -c px4_msgs || true)"
  [ "${n:-0}" -gt 0 ] \
    || gdie "px4_msgs not visible to ros2. The dependency workspace did not load."
  printf '  px4_msgs        %s interfaces\n' "$n"
}

uavx_require_sim() {
  command -v gzserver >/dev/null 2>&1 || gdie "gzserver missing"
  [ -x "${UAVX_PX4_DIR}/build/px4_sitl_default/bin/px4" ] || gdie "PX4 SITL binary not built"
  # Never assert by running the gazebo GUI binary. It takes the WSL distro down.
}

uavx_require_overlay() {
  [ -f "${UAVX_INSTALL_BASE}/setup.bash" ] \
    || gdie "repo overlay not built. Run: bash scripts/build-ws.sh"
}

uavx_require_module() {
  local m="$1"
  python3 -c "import ${m}" >/dev/null 2>&1 \
    || gdie "python module ${m} not importable. Overlay built but not sourced, or package missing."
}

# Every gate that runs a scenario goes through this so a stale metrics file can
# never satisfy a later check. See the run-record contract in stage-1/plan.md.
uavx_invalidate_latest() {
  # Round 6 finding 6: only latest.jsonl was deleted here. A runner that wrote
  # a new metrics record and missed the graph capture left the previous
  # scenario's latest-graph.json in place, and the seam pass certified the new
  # run against the old graph.
  rm -f "${UAVX_RUNS_DIR}/latest.jsonl" "${UAVX_RUNS_DIR}/latest-graph.json"
}
