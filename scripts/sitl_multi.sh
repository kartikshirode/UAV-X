#!/usr/bin/env bash
# Bring up N PX4 SITL vehicles on Gazebo Classic, headless, and prove they are
# all actually there. Repo-owned on purpose.
#
# Why not PX4's Tools/simulation/gazebo-classic/sitl_multiple_run.sh:
#
#   1. It ignores HEADLESS. Checked on the installed v1.15.4: `grep -c HEADLESS`
#      is 0, and its last line is an unconditional `gzclient`. On this machine
#      the Gazebo GUI binary takes the entire WSL distribution down, so the
#      documented launcher would kill its own gate.
#   2. It never sets PX4_UXRCE_DDS_NS, so every instance registers in the same
#      ROS namespace and the vehicles are indistinguishable to ROS 2. The rcS
#      does honour that variable (rcS:291), it just is not set per instance.
#
# Usage:
#   scripts/sitl_multi.sh --vehicles 4 [--model iris] [--world empty]
#                         [--hold 60] [--spacing 5]
#
# Holds the stack up for --hold seconds, then tears everything down. Exits
# non-zero if any required process is missing at any point.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

VEHICLES=4
MODEL=iris
WORLD=empty
HOLD=60
SPACING=5

while [ $# -gt 0 ]; do
  case "$1" in
    --vehicles) VEHICLES="$2"; shift 2 ;;
    --model)    MODEL="$2";    shift 2 ;;
    --world)    WORLD="$2";    shift 2 ;;
    --hold)     HOLD="$2";     shift 2 ;;
    --spacing)  SPACING="$2";  shift 2 ;;
    *) gdie "unknown argument: $1" ;;
  esac
done

uavx_load_env
uavx_require_ros
uavx_require_sim

SRC="${UAVX_PX4_DIR}"
BUILD="${SRC}/build/px4_sitl_default"
GZ_DIR="${SRC}/Tools/simulation/gazebo-classic"

cleanup() {
  local rc=$?
  gsay "cleanup"
  pkill -x px4 >/dev/null 2>&1 || true
  pkill -x MicroXRCEAgent >/dev/null 2>&1 || true
  pkill -x gzserver >/dev/null 2>&1 || true
  pkill -x gzclient >/dev/null 2>&1 || true
  sleep 2
  exit "$rc"
}
trap cleanup INT TERM EXIT

gsay "clearing anything left from a previous run"
pkill -x px4 >/dev/null 2>&1 || true
pkill -x gzserver >/dev/null 2>&1 || true
pkill -x gzclient >/dev/null 2>&1 || true
pkill -x MicroXRCEAgent >/dev/null 2>&1 || true
pkill -x gz >/dev/null 2>&1 || true
sleep 3

# Check the port rather than trusting the pkill. A gzserver held open by another
# shell survives this cleanup, and the only symptom downstream is "gzserver died
# on startup", which sends you looking at the wrong thing entirely. The real
# error is buried in its log as "bind: Address already in use".
if [ "$(ss -lnt 2>/dev/null | grep -c ':11345' || true)" -gt 0 ]; then
  gdie "gazebo port 11345 is still held. Another gzserver is running, probably from a launcher in another shell. Find it with: ss -lntp | grep 11345"
fi

# Sets GAZEBO_PLUGIN_PATH, GAZEBO_MODEL_PATH and LD_LIBRARY_PATH for the PX4
# plugins. Reads unbound variables, hence uavx_source.
uavx_source "${GZ_DIR}/setup_gazebo.bash" "${SRC}" "${BUILD}" >/dev/null \
  || gdie "could not source setup_gazebo.bash"

# Assert the plugin path actually landed. gzserver starts happily without it and
# then fails to load libgazebo_mavlink_interface.so, so the vehicles spawn and
# never connect. That looks like a PX4 problem and is not one.
[ -n "${GAZEBO_PLUGIN_PATH:-}" ] || gdie "GAZEBO_PLUGIN_PATH is unset after setup_gazebo.bash"
ls "${BUILD}"/build_gazebo-classic/libgazebo_mavlink_interface.so >/dev/null 2>&1 \
  || gdie "libgazebo_mavlink_interface.so not built. Run: make px4_sitl_default sitl_gazebo-classic"

gsay "starting the uXRCE-DDS agent"
MicroXRCEAgent udp4 -p 8888 > /tmp/uavx-agent.log 2>&1 &
AGENT_PID=$!
sleep 3
kill -0 "$AGENT_PID" 2>/dev/null || gdie "the agent died on startup, see /tmp/uavx-agent.log"

gsay "starting gzserver headless, no client"
WORLD_FILE="${GZ_DIR}/sitl_gazebo-classic/worlds/${WORLD}.world"
[ -f "$WORLD_FILE" ] || gdie "no world file at ${WORLD_FILE}"
gzserver "$WORLD_FILE" --verbose \
  -s libgazebo_ros_init.so -s libgazebo_ros_factory.so \
  > /tmp/uavx-gzserver.log 2>&1 &
GZ_PID=$!
sleep 6
kill -0 "$GZ_PID" 2>/dev/null || gdie "gzserver died on startup, see /tmp/uavx-gzserver.log"

gsay "spawning ${VEHICLES} x ${MODEL}"
i=0
while [ "$i" -lt "$VEHICLES" ]; do
  ns="uav_$((i + 1))"
  workdir="${BUILD}/rootfs/${i}"
  mkdir -p "$workdir"

  # One namespace per vehicle. Without this every instance lands in the same
  # one and ROS 2 cannot tell them apart.
  sdf="/tmp/uavx_${MODEL}_${i}.sdf"
  python3 "${GZ_DIR}/sitl_gazebo-classic/scripts/jinja_gen.py" \
    "${GZ_DIR}/sitl_gazebo-classic/models/${MODEL}/${MODEL}.sdf.jinja" \
    "${GZ_DIR}/sitl_gazebo-classic" \
    --mavlink_tcp_port $((4560 + i)) \
    --mavlink_udp_port $((14560 + i)) \
    --mavlink_id $((1 + i)) \
    --gst_udp_port $((5600 + i)) \
    --video_uri $((5600 + i)) \
    --mavlink_cam_udp_port $((14530 + i)) \
    --output-file "$sdf" > /dev/null || gdie "jinja_gen failed for instance ${i}"

  (
    cd "$workdir"
    PX4_SIM_MODEL="gazebo-classic_${MODEL}" \
    PX4_UXRCE_DDS_NS="$ns" \
    PX4_GZ_MODEL_POSE="0,$((i * SPACING))" \
      "${BUILD}/bin/px4" -i "$i" -d "${BUILD}/etc" > out.log 2> err.log &
  )

  sleep 2
  gz model --spawn-file="$sdf" --model-name="${MODEL}_${i}" \
    -x 0 -y $((i * SPACING)) -z 0.83 > /dev/null 2>&1 \
    || gwarn "gz model spawn reported a problem for instance ${i}"
  printf '  instance %d  ns=%s  sys_id=%d\n' "$i" "$ns" $((1 + i))
  i=$((i + 1))
done

gsay "letting the stack settle"
sleep 15

# ---------------------------------------------------------------- assertions
gsay "checking what is actually running"

px4_count="$(pgrep -xc px4 || true)"
[ "${px4_count:-0}" -eq "$VEHICLES" ] \
  || gdie "expected ${VEHICLES} px4 processes, found ${px4_count:-0}. See ${BUILD}/rootfs/*/err.log"
printf '  px4 processes      %s\n' "$px4_count"

pgrep -x gzserver >/dev/null 2>&1 || gdie "gzserver is not running"
printf '  gzserver           up\n'

gzclient_count="$(pgrep -xc gzclient || true)"
[ "${gzclient_count:-0}" -eq 0 ] \
  || gdie "gzclient is running. This launcher must stay headless; the GUI binary crashes the WSL distro."
printf '  gzclient           absent, as required\n'

pgrep -x MicroXRCEAgent >/dev/null 2>&1 || gdie "the agent is not running"
printf '  agent              up\n'

# Every vehicle must be individually visible to ROS 2, not just present in sum.
missing=""
i=1
while [ "$i" -le "$VEHICLES" ]; do
  n="$(ros2 topic list 2>/dev/null | grep -c "^/uav_${i}/" || true)"
  printf '  uav_%d topics       %s\n' "$i" "${n:-0}"
  [ "${n:-0}" -gt 0 ] || missing="${missing} uav_${i}"
  i=$((i + 1))
done
[ -z "$missing" ] || gdie "no ROS 2 topics under namespace(s):${missing}. Check PX4_UXRCE_DDS_NS and the agent log."

# Codex round 2, finding 11: nothing measured peak memory, and this box has
# about 11.5 GiB with Docker Desktop competing for it.
mem_avail_mb="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
swap_used_mb="$(free -m | awk '/^Swap:/ {print $3}')"
printf '  mem available      %s MiB\n' "$mem_avail_mb"
printf '  swap used          %s MiB\n' "$swap_used_mb"
[ "${mem_avail_mb:-0}" -ge 2000 ] \
  || gdie "only ${mem_avail_mb} MiB available with ${VEHICLES} vehicles up. Too close to the edge to run a mission."
[ "${swap_used_mb:-0}" -le 512 ] \
  || gdie "swap in use at ${swap_used_mb} MiB. The box is thrashing; results from here are not trustworthy."

gsay "${VEHICLES} vehicles up and healthy, holding ${HOLD}s"
sleep "$HOLD"

gsay "still healthy after the hold"
px4_count="$(pgrep -xc px4 || true)"
[ "${px4_count:-0}" -eq "$VEHICLES" ] \
  || gdie "vehicles died during the hold: ${px4_count:-0} of ${VEHICLES} left"
printf '  px4 processes      %s\n' "$px4_count"
