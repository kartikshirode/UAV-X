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
# Vehicles stand in a line along y, spaced --spacing apart and centred on the
# origin, and the launcher checks each one is actually level before it reports
# the stack healthy. See the spawn loop for why both of those matter.
#
# Holds the stack up for --hold seconds, then tears everything down. Exits
# non-zero if any required process is missing at any point.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

VEHICLES=4
MODEL=iris
# The world this repository carries, resolved below. PX4's own empty.world
# is still reachable by name for anyone who wants to compare against it.
WORLD=uavx_empty
HOLD=60
SPACING=5
# How far from level a vehicle may rest and still be asked to fly. Measured on
# this machine: a vehicle standing on flat ground settles at 0.09 deg, and one
# standing on the lip described in the spawn loop settles at 9 deg. Anything
# between the two is a vehicle nobody meant to put there.
MAX_TILT_DEG=2.0

# Where the launcher writes down what it did. Week 1 audit finding 11: this
# script computed a spawn position per vehicle, printed it and threw it away.
# PX4 reports position in each vehicle's own local frame, whose origin is that
# vehicle's spawn point, while architecture.md fixes all geometry in one frame
# with the ground station at the origin. Converting between the two needs the
# offsets, and the only place they existed was the arithmetic below. Then defect
# 13 moved every one of them by centring the line, and no artifact recorded
# either the old positions or the new.
SPAWN_MANIFEST="${UAVX_RUNS_DIR}/.launcher-spawn.json"

# The two pieces of arithmetic this launcher gets wrong quietly, pulled out so
# scripts/test_launcher_geometry.py can run them without a simulator. Week 1
# audit finding 9: the tilt check was the one control this week added against
# the defect that cost seven runs, and nothing exercised it. check_shell.sh runs
# `bash -n`, which sees no further into an awk program than into a comment.
#
# >>> spacing-awk, extracted verbatim by scripts/test_launcher_geometry.py
UAVX_SPACING_AWK='BEGIN { printf "%.3f", (i - (n - 1) / 2) * s }'
# <<< spacing-awk
#
# Six numbers, x y z roll pitch yaw, on one line. The first version took $4 and
# $5 off whatever arrived: awk reads a non-numeric field as 0, so a changed
# output format, an error line or an empty answer all produced a confident
# 0.00 degrees and the launcher called a vehicle level that it had never
# measured. Nothing about a guard that fails open is better than no guard.
#
# >>> tilt-awk, extracted verbatim by scripts/test_launcher_geometry.py
UAVX_TILT_AWK='
NF == 6 &&
$4 ~ /^-?([0-9]+[.]?[0-9]*|[.][0-9]+)([eE][-+]?[0-9]+)?$/ &&
$5 ~ /^-?([0-9]+[.]?[0-9]*|[.][0-9]+)([eE][-+]?[0-9]+)?$/ {
  r = ($4 < 0) ? -$4 : $4
  p = ($5 < 0) ? -$5 : $5
  printf "%.2f", ((r > p) ? r : p) * 57.29578
  seen = 1
  exit
}
END { if (!seen) exit 1 }'
# <<< tilt-awk

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

# /clock runs at 10 Hz and that is the ceiling on everything the runner
# measures in simulated time. libgazebo_ros_init defaults to 10 Hz. Every `_s`
# field in a run record is measured against this clock, and the runner samples
# poses on it, so a sample can land only once per tick. Across eight archived
# runs the ratio of pose samples to clock messages is 0.402 at a 5 Hz sampling
# target and exactly 1.000 at 20 Hz, so the sampler is against the wall.
#
# Three ways of raising it have been tried and none works:
#
#   -p publish_rate:=100     rclcpp refuses it, publish_rate is a double, and
#                            the failure arrives as gzserver aborting with a
#                            core dump rather than as a message about a type.
#   --ros-args -p ...:=100.0 gzserver's own parser is boost::program_options
#                            and knows nothing about --ros-args, so it takes
#                            the leftover token as a positional and dies with
#                            "Invalid logfile [publish_rate:=100.0]".
#   a block in the world     libgazebo_ros_init is a SystemPlugin. Only
#                            WorldPlugins may be declared in a world file, so
#                            there is nowhere in worlds/uavx_empty.world to put
#                            it. That world exists for a different plugin.
#
# What it costs is resolution and not coverage. architecture.md freezes 20 Hz
# for the coverage source and the separation monitor; at 10 Hz the along-track
# sample spacing at survey speed is about 0.61 m against a 12 m sensor
# footprint, and every record carries the rate it achieved beside the rate it
# asked for so no number claims otherwise.

# Ground truth. /gazebo/model_states is published by libgazebo_ros_state.so,
# which is a WorldPlugin, and -s loads SystemPlugins. Handing it to -s produced
# one line in the gzserver log,
#
#   [Err] System is attempting to load a plugin, but detected an incorrect
#   plugin type. Plugin filename[libgazebo_ros_state.so]
#
# and then carried on with no ground truth topic. The first survey run flew the
# whole 420 s and reported 0 of 400 cells, because the collector had nothing to
# collect. A WorldPlugin is declared inside the world, so the world is
# worlds/uavx_empty.world here: PX4's empty.world byte for byte with that one
# plugin block added. A world named on the command line is still looked for in
# PX4's directory first, so --world empty runs PX4's own copy unchanged.
gsay "starting gzserver headless, no client"
WORLD_FILE="${GZ_DIR}/sitl_gazebo-classic/worlds/${WORLD}.world"
if [ ! -f "$WORLD_FILE" ] && [ -f "${UAVX_REPO}/worlds/${WORLD}.world" ]; then
  WORLD_FILE="${UAVX_REPO}/worlds/${WORLD}.world"
fi
[ -f "$WORLD_FILE" ] || gdie "no world file for ${WORLD}: neither ${GZ_DIR}/sitl_gazebo-classic/worlds/${WORLD}.world nor ${UAVX_REPO}/worlds/${WORLD}.world"
printf '  world              %s\n' "$WORLD_FILE"
gzserver "$WORLD_FILE" --verbose \
  -s libgazebo_ros_init.so -s libgazebo_ros_factory.so \
  > /tmp/uavx-gzserver.log 2>&1 &
GZ_PID=$!
sleep 6
kill -0 "$GZ_PID" 2>/dev/null || gdie "gzserver died on startup, see /tmp/uavx-gzserver.log"

gsay "spawning ${VEHICLES} x ${MODEL}"
i=0
spawn_rows=""
spawn_sep=""
while [ "$i" -lt "$VEHICLES" ]; do
  ns="uav_$((i + 1))"
  workdir="${BUILD}/rootfs/${i}"
  mkdir -p "$workdir"

  # PX4 SITL imports parameters.bson from its working directory at boot and
  # writes it back at shutdown, so instance N silently inherits whatever
  # instance N left on disk the last time it ran. After two smoke runs the four
  # files had already diverged. The airframe was never the part that drifted:
  # SYS_AUTOSTART read 10015 on all four, and rcS recomputes it from
  # PX4_SIM_MODEL on every boot anyway. The calibration drifted. Instance 2
  # alone had persisted CAL_GYRO0_PRIO=50 and a gyro bias of
  # CAL_GYRO0_XOFF=0.0374, YOFF=-0.0080, ZOFF=-0.0013 that the other three did
  # not carry, and instance 2 was the one vehicle that took 36s to arm and then
  # never finished its landing. Standing rule 4 asks that a run replay exactly,
  # and a run whose sensor calibration depends on an earlier run cannot. W4
  # injects faults, so it would be diagnosing yesterday's leftovers.
  #
  # Both parameter files have to go, not just the primary: rcS falls back to
  # the backup when the primary is missing. With neither present it imports
  # nothing, prints nothing, and boots from defaults. dataman goes for the same
  # reason, it is mission and geofence storage and a mission left there by an
  # earlier experiment is the same bug.
  rm -f "${workdir}/parameters.bson" "${workdir}/parameters_backup.bson"
  rm -f "${workdir}/dataman"

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

  # Where this vehicle stands, and the whole reason the line is centred on the
  # origin rather than running out from it.
  #
  # empty.world lays an asphalt_plane over the ground plane. The model Gazebo
  # actually resolves for it on this machine is ~/.gazebo/models/asphalt_plane,
  # which is 20 x 20 x 0.1 m, not the 200 x 200 copy PX4 ships under
  # Tools/simulation/gazebo-classic. The user model path wins, so the paved
  # square ends at y = +/-10 m with a 5 cm lip down to the ground plane.
  #
  # The old layout was y = i * spacing, which put instance 2 at y = 10 exactly,
  # astride that lip. Measured: instances 0 and 1 rest at z = 0.1044 on the
  # asphalt and instance 3 at z = 0.0542 on the ground plane, all three level to
  # 0.09 deg, while instance 2 rests at z = 0.0910 and 9.0 deg of roll. It then
  # takes off tilted, its EKF fails the post-takeoff navigation test, failsafe
  # fires, and it flies away instead of climbing. That was uav_3 never reaching
  # its 50 m layer. Spawning the same four instances in reverse order moved the
  # tilt to whichever one was handed y = 10, so it was the ground underneath and
  # never the instance number.
  #
  # Centred, four vehicles at 5 m span y = -7.5 to +7.5 and every one of them
  # stands on flat asphalt. Integer arithmetic cannot hold the half spacing an
  # even vehicle count needs, hence awk.
  y="$(awk -v i="$i" -v n="$VEHICLES" -v s="$SPACING" "$UAVX_SPACING_AWK")"
  spawn_rows="${spawn_rows}${spawn_sep}{\"instance\": ${i}"
  spawn_rows="${spawn_rows}, \"vehicle_id\": \"${ns}\""
  spawn_rows="${spawn_rows}, \"x_m\": 0.0, \"y_m\": ${y}, \"z_m\": 0.83}"
  spawn_sep=", "

  (
    cd "$workdir"
    PX4_SIM_MODEL="gazebo-classic_${MODEL}" \
    PX4_UXRCE_DDS_NS="$ns" \
    PX4_GZ_MODEL_POSE="0,${y}" \
      "${BUILD}/bin/px4" -i "$i" -d "${BUILD}/etc" > out.log 2> err.log &
  )

  sleep 2
  gz model --spawn-file="$sdf" --model-name="${MODEL}_${i}" \
    -x 0 -y "$y" -z 0.83 > /dev/null 2>&1 \
    || gwarn "gz model spawn reported a problem for instance ${i}"
  printf '  instance %d  ns=%s  sys_id=%d  y=%s\n' "$i" "$ns" $((1 + i)) "$y"
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

# Every vehicle has to be standing level before it is asked to fly, and the
# only way to know is to ask Gazebo where each model came to rest rather than
# trusting the pose it was spawned with. A vehicle settled on a slope lifts off
# with its thrust vector tilted, and what reaches whoever is reading the log is
# not "instance 2 is on a slope" but a navigation failure, a failsafe and a
# climb that stops tens of metres short of the layer. That cost this repo seven
# runs. The spawn loop above explains the slope; this is what catches it if a
# future --spacing or --vehicles walks the formation off the asphalt again.
# One artifact, written by the only thing that knows, read by the runner. The
# alternative was for the runner to recompute the formula above, which is the
# copy-that-drifts pattern the week 1 audit spent its second finding on.
{
  printf '{"launcher": "scripts/sitl_multi.sh", "model": "%s", "world": "%s"' \
    "$MODEL" "$WORLD"
  printf ', "spacing_m": %s, "vehicles_requested": %s' "$SPACING" "$VEHICLES"
  printf ', "frame": "gazebo world ENU, origin at the world origin, metres"'
  printf ', "vehicles": [%s]}' "$spawn_rows"
} > "$SPAWN_MANIFEST"
printf '  spawn manifest     %s\n' "$SPAWN_MANIFEST"

gsay "checking every vehicle is standing level"
tilted=""
i=0
while [ "$i" -lt "$VEHICLES" ]; do
  # Six numbers, x y z roll pitch yaw, on one line. timeout, because a wedged
  # gazebo transport would otherwise hang the launcher past the caller's
  # readiness window and the caller would blame the vehicles.
  pose="$(timeout 15 gz model -m "${MODEL}_${i}" -p 2>/dev/null || true)"
  [ -n "$pose" ] \
    || gdie "gazebo reports no pose for ${MODEL}_${i}. The model never spawned, so nothing was flying under that name."
  tilt="$(printf '%s\n' "$pose" | awk "$UAVX_TILT_AWK")" \
    || gdie "gazebo answered for ${MODEL}_${i} and the answer was not a pose: '${pose}'. Six numbers were expected. A guard that reads an unparseable answer as level is worse than no guard, so this stops here."
  over="$(awk -v t="$tilt" -v m="$MAX_TILT_DEG" 'BEGIN { print (t > m) ? 1 : 0 }')"
  printf '  instance %d tilt     %s deg\n' "$i" "$tilt"
  [ "${over:-1}" -eq 0 ] || tilted="${tilted} ${i}(${tilt} deg)"
  i=$((i + 1))
done
[ -z "$tilted" ] \
  || gdie "resting above ${MAX_TILT_DEG} deg of tilt, so not fit to take off:${tilted}. The formation has reached the edge of the asphalt_plane, whose lip is at y = +/-10 m. Lower --spacing or --vehicles until it fits."

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
