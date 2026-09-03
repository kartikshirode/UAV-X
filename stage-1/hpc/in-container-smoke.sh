#!/usr/bin/env bash
# What runs inside the container. Called by smoke-sitl.slurm, never on its own.
#
# Three phases, in that order on purpose, so a partial result is still readable.
# If phase 1 works and phase 3 does not, the answer to "can this cluster run
# Gazebo headless" is yes and the answer to "can it fly four vehicles" is no,
# and those are different findings that would be lost in a single pass or fail.
#
#   1  gzserver alone, headless, from the same world file PX4 SITL uses
#   2  one vehicle through scripts/sitl_multi.sh
#   3  four vehicles through scripts/sitl_multi.sh
#
# scripts/sitl_multi.sh is the repo's launcher and it is mounted read only from
# the host. PX4's own Tools/simulation/gazebo-classic/sitl_multiple_run.sh is
# never used here: it ignores HEADLESS and ends in an unconditional gzclient.

set -uo pipefail

OUT=/work/out
REPO=/work/repo
VEHICLES="${VEHICLES:-4}"
HOLD="${HOLD:-30}"

# The repo copy is mounted read only, and sitl_multi.sh writes a spawn manifest
# to $UAVX_RUNS_DIR, which gate-env.sh otherwise defaults to $UAVX_REPO/runs.
# Point it at the artifact directory instead. The launcher keeps its own
# default; this only pre-sets the variable gate-env.sh already honours.
export UAVX_RUNS_DIR="${OUT}/runs"

mkdir -p "$OUT" "$UAVX_RUNS_DIR"

hr() { printf '\n===== %s\n' "$*"; }

# Stop gzserver and do not return until port 11345 is actually free.
#
# The first run of this script got phase 1 right and then failed both launcher
# phases on "gazebo port 11345 is still held", which is sitl_multi.sh refusing
# to start on top of a simulator it did not launch. That simulator was phase 1.
# gzserver logged three separate SIGTERM handler lines, one from here and two
# from the launcher's own cleanup, and was still holding the port after all
# three of them. A pkill followed by a sleep is a guess at how long shutdown
# takes. This waits for the condition the next phase depends on and escalates
# to SIGKILL when the polite signal has clearly not worked.
stop_gzserver() {
  local i=0
  pkill -x gzserver > /dev/null 2>&1
  pkill -x px4 > /dev/null 2>&1
  pkill -x MicroXRCEAgent > /dev/null 2>&1
  while [ "$i" -lt 20 ]; do
    if ! pgrep -x gzserver > /dev/null 2>&1 \
       && [ "$(ss -lnt 2>/dev/null | grep -c ':11345' || true)" -eq 0 ]; then
      printf '  port 11345    free after %ss\n' "$i"
      return 0
    fi
    sleep 1
    i=$((i + 1))
    [ "$i" -eq 8 ] && pkill -9 -x gzserver > /dev/null 2>&1
  done
  printf '  port 11345    STILL HELD after %ss\n' "$i"
  return 1
}

hr "container facts"
printf '  hostname      %s\n' "$(hostname)"
printf '  whoami        %s (uid %s)\n' "$(whoami)" "$(id -u)"
printf '  os            %s\n' "$(. /etc/os-release && echo "$PRETTY_NAME")"
printf '  nproc         %s\n' "$(nproc)"
printf '  DISPLAY       [%s]\n' "${DISPLAY:-unset}"
printf '  mem available %s MiB\n' "$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
printf '  swap used     %s MiB\n' "$(free -m | awk '/^Swap:/ {print $3}')"
printf '  shm size      %s\n' "$(df -h /dev/shm | tail -1 | awk '{print $2}')"
printf '  pid namespace %s pids visible\n' "$(ls /proc | grep -c '^[0-9]')"

hr "the stack in this image, against versions.lock"
uavx-report 2>&1 | tee "${OUT}/version-report.txt"
report_rc="${PIPESTATUS[0]}"
printf '  report exit   %s\n' "$report_rc"

# ---------------------------------------------------------------- phase 1
#
# Nothing about Gazebo needs a display when it runs as gzserver, but that is an
# assertion the local machine has never had to make on a box with no GPU, no X
# and no DRM device at all. This is the phase that answers it.
hr "phase 1: gzserver headless, no display, no client"
set +u
source /opt/ros/humble/setup.bash
source "$UAVX_PX4_DIR/Tools/simulation/gazebo-classic/setup_gazebo.bash" \
  "$UAVX_PX4_DIR" "$UAVX_PX4_DIR/build/px4_sitl_default" > /dev/null
set -u
WORLD="$UAVX_PX4_DIR/Tools/simulation/gazebo-classic/sitl_gazebo-classic/worlds/empty.world"
printf '  world         %s\n' "$WORLD"
printf '  plugin path   %s\n' "${GAZEBO_PLUGIN_PATH:-UNSET}"

gzserver "$WORLD" --verbose \
  -s libgazebo_ros_init.so -s libgazebo_ros_factory.so \
  > "${OUT}/phase1-gzserver.log" 2>&1 &
GZ_PID=$!
sleep 12
if kill -0 "$GZ_PID" 2>/dev/null; then
  printf '  gzserver      up after 12s, pid %s\n' "$GZ_PID"
  printf '  port 11345    %s listener(s)\n' "$(ss -lnt 2>/dev/null | grep -c ':11345' || true)"
  echo "  gz topics     $(timeout 15 gz topic -l 2>/dev/null | wc -l) on the transport"
  phase1=0
else
  printf '  gzserver      DIED, see %s\n' "${OUT}/phase1-gzserver.log"
  tail -25 "${OUT}/phase1-gzserver.log" | sed 's/^/    /'
  phase1=1
fi
stop_gzserver
printf '  phase 1 rc    %s\n' "$phase1"

# ---------------------------------------------------------------- phase 2 & 3
run_launcher() {
  local n="$1" hold="$2" tag="$3" rc
  hr "phase ${tag}: scripts/sitl_multi.sh --vehicles ${n} --hold ${hold}"
  stop_gzserver
  bash "${REPO}/scripts/sitl_multi.sh" --vehicles "$n" --hold "$hold" \
    2>&1 | tee "${OUT}/phase${tag}-sitl_multi.log"
  rc="${PIPESTATUS[0]}"
  printf '  phase %s rc    %s\n' "$tag" "$rc"
  # PX4 writes one out.log and one err.log per instance into its rootfs. When a
  # vehicle fails to boot, that is the only place the reason exists.
  local d="${OUT}/phase${tag}-px4-rootfs"
  mkdir -p "$d"
  local i=0
  while [ "$i" -lt "$n" ]; do
    for f in out.log err.log; do
      src="${UAVX_PX4_DIR}/build/px4_sitl_default/rootfs/${i}/${f}"
      [ -f "$src" ] && cp "$src" "${d}/${i}-${f}"
    done
    i=$((i + 1))
  done
  cp /tmp/uavx-gzserver.log "${d}/gzserver.log" 2>/dev/null
  cp /tmp/uavx-agent.log    "${d}/agent.log"    2>/dev/null
  return "$rc"
}

run_launcher 1 10 2
phase2=$?

run_launcher "$VEHICLES" "$HOLD" 3
phase3=$?

hr "summary"
printf '  version report   %s\n' "$([ "$report_rc" -eq 0 ] && echo ok || echo MISMATCH)"
printf '  phase 1 gzserver %s\n' "$([ "$phase1" -eq 0 ] && echo ok || echo FAILED)"
printf '  phase 2 1 vehicle  %s\n' "$([ "$phase2" -eq 0 ] && echo ok || echo FAILED)"
printf '  phase 3 %s vehicles %s\n' "$VEHICLES" "$([ "$phase3" -eq 0 ] && echo ok || echo FAILED)"

[ "$phase1" -eq 0 ] && [ "$phase2" -eq 0 ] && [ "$phase3" -eq 0 ]
