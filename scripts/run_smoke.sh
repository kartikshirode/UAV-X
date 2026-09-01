#!/usr/bin/env bash
# Four vehicles arm, climb to a hold altitude, hold, and land. Headless.
#
# Chunk 1.2 asks one question: can this machine put four PX4 instances in the
# air at the same time with no GUI. Every later week assumes the answer is yes,
# so the answer has to come off something a person can reread afterwards.
#
# Four decisions here are deliberate.
#
#   1. The launcher is scripts/sitl_multi.sh and nothing else. PX4's own
#      Tools/simulation/gazebo-classic/sitl_multiple_run.sh ignores HEADLESS and
#      ends in an unconditional gzclient. The Gazebo GUI binary takes this WSL
#      distribution down, so the documented launcher would kill its own gate.
#      That has happened three times on this machine.
#   2. Control goes over MAVLink with pymavlink, on the API link PX4 opens at
#      UDP 14540+instance in build/px4_sitl_default/etc/init.d-posix/px4-rc.mavlink.
#      MAVSDK is not installed here, and px4-commander would only tell us a
#      command was accepted by the shell, which is a different claim from a
#      vehicle that climbed. MAVLink also carries LOCAL_POSITION_NED and
#      EXTENDED_SYS_STATE, which is the evidence this script is written around.
#   3. The verdict is taken after teardown, by reading the per-vehicle track
#      files written during the flight. Standing rule 5: assert on an artifact.
#      A COMMAND_ACK saying ACCEPTED is metadata about a request, and this repo
#      has already spent hours green on a machine with no simulator binaries
#      because a check asked a database instead of the filesystem. It reads
#      those files up to each vehicle's touchdown and no further: the rows
#      after it are a parked vehicle whose estimated altitude free runs, by
#      over three metres across one 326 second tail here, and a verdict that
#      reads them is judging the estimator rather than the flight.
#   4. Teardown runs from a trap, so an interrupt leaves nothing behind either.
#      A later gate that inherits a live gzserver measures a simulator it did
#      not start.
#
# The run record written here is a placeholder and says so in its own text.
# Chunk 1.7 owns the real writer, the schema and the run id grammar, and this
# script will be rewritten to call it. It deliberately does not publish
# runs/latest.jsonl: publishing that file is the runner's job, and a smoke
# record sitting there would satisfy a later gate written for a scenario run.
#
# Usage:
#   scripts/run_smoke.sh [--vehicles 4] [--runs-dir runs]
#
# Exit 0 means every vehicle reached its hold altitude and landed, and nothing
# named px4, gzserver or gzclient is still running.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

VEHICLES=4
RUNS_DIR="${UAVX_RUNS_DIR}"

# The flight profile. 5 m is above ground effect, is reached in a few seconds
# at the default MPC_TKO_SPEED, and a box that cannot manage it is not going to
# manage a mission. The tolerance covers the estimator, not a weaker claim.
HOLD_ALT_M=5.0
ALT_TOL_M=1.0
HOLD_S=10
FLIGHT_DEADLINE_S=420
READY_TIMEOUT_S=300
# sitl_multi.sh holds the stack up while we fly it, so its hold has to outlast
# the whole flight window. We stop it as soon as the last vehicle is down.
SIM_HOLD_S=$((FLIGHT_DEADLINE_S + 120))

while [ $# -gt 0 ]; do
  case "$1" in
    --vehicles)
      [ $# -ge 2 ] || gdie "--vehicles needs a number"
      VEHICLES="$2"; shift 2 ;;
    --runs-dir)
      [ $# -ge 2 ] || gdie "--runs-dir needs a directory"
      RUNS_DIR="$2"; shift 2 ;;
    -h|--help)
      printf 'usage: %s [--vehicles N] [--runs-dir DIR]\n' "$0"
      exit 0 ;;
    *)
      gdie "unknown argument: $1. This script takes --vehicles and --runs-dir, nothing else." ;;
  esac
done

case "$VEHICLES" in
  ''|*[!0-9]*) gdie "--vehicles must be a positive integer, got: ${VEHICLES}" ;;
esac
[ "$VEHICLES" -ge 1 ] || gdie "--vehicles must be at least 1, got: ${VEHICLES}"
[ -n "$RUNS_DIR" ] || gdie "--runs-dir must not be empty"

uavx_load_env
# The launcher needs both, and finding that out 60 seconds into a bring-up
# wastes a bring-up.
uavx_require_ros
uavx_require_sim

python3 -c 'import pymavlink' >/dev/null 2>&1 \
  || gdie "python module pymavlink is missing, and the flight talks MAVLink on PX4's API link. Install it with: python3 -m pip install --user pymavlink"

mkdir -p "$RUNS_DIR" || gdie "could not create the runs directory ${RUNS_DIR}"
RUNS_DIR="$(cd "$RUNS_DIR" && pwd)"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ID="smoke_${STAMP}"
RECORD="${RUNS_DIR}/smoke-${STAMP}.json"
# Tracks live on ext4 next to the launcher's own logs. Sustained writes to the
# /mnt/c 9P mount drop the connection, and a dropped track is a lost verdict.
TRACK_DIR="/tmp/uavx-smoke-${STAMP}"
LAUNCH_LOG="/tmp/uavx-smoke-launcher-${STAMP}.log"

LAUNCHER_PID=""

stop_everything() {
  # The launcher spends its hold inside `sleep`. A TERM to the script alone is
  # queued until that sleep returns, which is up to SIM_HOLD_S away, so end its
  # children first: bash then runs its own cleanup trap immediately.
  if [ -n "$LAUNCHER_PID" ] && kill -0 "$LAUNCHER_PID" 2>/dev/null; then
    kill -TERM "$LAUNCHER_PID" 2>/dev/null || true
    pkill -TERM -P "$LAUNCHER_PID" >/dev/null 2>&1 || true
    local waited=0
    while [ "$waited" -lt 20 ] && kill -0 "$LAUNCHER_PID" 2>/dev/null; do
      sleep 1
      waited=$((waited + 1))
    done
    kill -KILL "$LAUNCHER_PID" 2>/dev/null || true
  fi
  pkill -x px4 >/dev/null 2>&1 || true
  pkill -x MicroXRCEAgent >/dev/null 2>&1 || true
  pkill -x gzserver >/dev/null 2>&1 || true
  pkill -x gzclient >/dev/null 2>&1 || true
  sleep 2
  # A px4 instance wedged on the simulator socket ignores TERM. Nothing here is
  # worth keeping, so the second pass does not ask twice.
  pkill -KILL -x px4 >/dev/null 2>&1 || true
  pkill -KILL -x MicroXRCEAgent >/dev/null 2>&1 || true
  pkill -KILL -x gzserver >/dev/null 2>&1 || true
  pkill -KILL -x gzclient >/dev/null 2>&1 || true
  sleep 1
}

on_exit() {
  local rc=$?
  trap - INT TERM EXIT
  stop_everything
  exit "$rc"
}
trap on_exit INT TERM EXIT

# ---------------------------------------------------------------- preflight
gsay "checking nothing is already flying"
busy=""
for name in gzserver gzclient px4; do
  n="$(pgrep -xc "$name" || true)"
  [ -n "$n" ] || n=0
  [ "$n" -eq 0 ] || busy="${busy} ${name}(${n})"
done
if [ -n "$busy" ]; then
  pgrep -a gzserver || true
  pgrep -a gzclient || true
  pgrep -a px4 || true
  gdie "a simulator is already up:${busy}. A run that inherits someone else's gzserver is not measuring what it thinks. Stop it first with: pkill -x px4; pkill -x gzserver"
fi
printf '  no gzserver, gzclient or px4 running\n'

rm -rf "$TRACK_DIR"
mkdir -p "$TRACK_DIR" || gdie "could not create the track directory ${TRACK_DIR}"
STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ------------------------------------------------------------------ bring-up
gsay "bringing up ${VEHICLES} vehicles through scripts/sitl_multi.sh, headless"
bash "${HERE}/sitl_multi.sh" --vehicles "$VEHICLES" --hold "$SIM_HOLD_S" \
  > "$LAUNCH_LOG" 2>&1 &
LAUNCHER_PID=$!

# Wait for the launcher's own health assertions to pass rather than for a
# process count. If we started flying while it was still checking, one of its
# gdie paths would tear the stack down underneath the flight and the failure
# would be reported against the wrong thing. The marker is the launcher's own
# "up and healthy" line; if that wording ever changes this wait is what breaks.
waited=0
ready=0
while [ "$waited" -lt "$READY_TIMEOUT_S" ]; do
  found="$(grep -c 'vehicles up and healthy' "$LAUNCH_LOG" 2>/dev/null || true)"
  [ -n "$found" ] || found=0
  if [ "$found" -gt 0 ]; then
    ready=1
    break
  fi
  kill -0 "$LAUNCHER_PID" 2>/dev/null || break
  sleep 5
  waited=$((waited + 5))
done

if [ "$ready" -ne 1 ]; then
  alive="$(pgrep -xc px4 || true)"
  [ -n "$alive" ] || alive=0
  printf '\n--- last 40 lines of %s\n' "$LAUNCH_LOG"
  tail -n 40 "$LAUNCH_LOG" 2>/dev/null || true
  gdie "the launcher never reported ${VEHICLES} vehicles up and healthy within ${READY_TIMEOUT_S}s. ${alive} px4 process(es) were alive at that point. The launcher log above is the whole story."
fi
printf '  launcher reported %s vehicles up and healthy after %ss\n' "$VEHICLES" "$waited"

# -------------------------------------------------------------------- flight
gsay "arm, takeoff to ${HOLD_ALT_M} m, hold ${HOLD_S}s, land"
FLIGHT_RC=0
python3 - "$TRACK_DIR" "$VEHICLES" "$HOLD_ALT_M" "$ALT_TOL_M" "$HOLD_S" \
         "$FLIGHT_DEADLINE_S" <<'PY' || FLIGHT_RC=$?
"""Fly the smoke profile and write down what happened while it happens.

Control is MAVLink on the API link PX4 opens at UDP 14540+instance, one socket
per vehicle, all four driven from one loop so they fly together rather than in
turn. Nothing here decides whether the run passed: every vehicle writes a track
file and a separate pass reads those back once the simulator is gone.
"""
import json
import os
import sys
import time

from pymavlink import mavutil

TRACK_DIR = sys.argv[1]
VEHICLES = int(sys.argv[2])
HOLD_ALT = float(sys.argv[3])
ALT_TOL = float(sys.argv[4])
HOLD_S = float(sys.argv[5])
DEADLINE = float(sys.argv[6])

ARMED_FLAG = 128            # MAV_MODE_FLAG_SAFETY_ARMED
ON_GROUND = 1               # MAV_LANDED_STATE_ON_GROUND
IN_AIR = 2
TAKING_OFF = 3
LANDING = 4

CMD_ARM = 400               # MAV_CMD_COMPONENT_ARM_DISARM
CMD_TAKEOFF = 22            # MAV_CMD_NAV_TAKEOFF
CMD_LAND = 21               # MAV_CMD_NAV_LAND

# MAV_RESULT values that will not become an acceptance however long we wait.
# TEMPORARILY_REJECTED and IN_PROGRESS are deliberately not in here.
HARD_ACK = {2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 6: "CANCELLED"}

T_CONNECT = 90.0
T_POSITION = 150.0
T_ARM = 120.0
T_CLIMB = 150.0
T_LAND = 240.0
RESEND_S = 5.0
SAMPLE_S = 0.2
RESOURCE_S = 1.0
NAN = float("nan")


def meminfo():
    out = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                out[parts[0].rstrip(":")] = int(parts[1])
    return out


class Vehicle:
    def __init__(self, index):
        self.index = index
        self.name = "uav_%d" % (index + 1)
        self.port = 14540 + index
        self.conn = mavutil.mavlink_connection(
            "udpin:127.0.0.1:%d" % self.port,
            source_system=255, source_component=190)
        self.track = open(os.path.join(TRACK_DIR, "%s.jsonl" % self.name),
                          "w", encoding="utf-8")
        self.state = "connect"
        self.entered = 0.0
        self.sent_at = -99.0
        self.tries = 0
        self.armed = False
        self.z = None
        self.z_ground = None
        self.home_alt = None
        self.global_alt = None
        self.landed_state = 0
        self.beats = 0
        self.acks = {}
        self.texts = []
        self.reason = ""

    # Altitude above the point the vehicle sat at when it was armed. The EKF
    # origin is close enough to that already, but taking the difference means a
    # non-zero origin cannot quietly buy the vehicle a metre of climb.
    def alt(self):
        if self.z is None:
            return 0.0
        base = self.z_ground if self.z_ground is not None else 0.0
        return -(self.z - base)

    def tail(self):
        return "last messages: %s" % (self.texts[-3:] or "none")

    def drain(self):
        while True:
            try:
                msg = self.conn.recv_match(blocking=False)
            except Exception as exc:
                self.texts.append("recv failed: %s" % exc)
                return
            if msg is None:
                return
            kind = msg.get_type()
            if kind == "HEARTBEAT":
                self.beats += 1
                self.armed = bool(msg.base_mode & ARMED_FLAG)
            elif kind == "LOCAL_POSITION_NED":
                self.z = msg.z
            elif kind == "EXTENDED_SYS_STATE":
                self.landed_state = msg.landed_state
            elif kind == "HOME_POSITION":
                self.home_alt = msg.altitude / 1000.0
            elif kind == "GLOBAL_POSITION_INT":
                self.global_alt = msg.alt / 1000.0
            elif kind == "COMMAND_ACK":
                self.acks[msg.command] = msg.result
            elif kind == "STATUSTEXT":
                text = msg.text
                if isinstance(text, bytes):
                    text = text.decode("utf-8", "replace")
                self.texts.append(text)
                del self.texts[:-10]

    def send(self, now, command, *params):
        args = list(params) + [0.0] * (7 - len(params))
        target = self.conn.target_system or (self.index + 1)
        # Drop the previous answer so a stale ack cannot be read as this one's.
        self.acks.pop(command, None)
        self.conn.mav.command_long_send(target, 1, command, 0, *args)
        self.sent_at = now

    def rejected(self, command):
        result = self.acks.get(command)
        if result in HARD_ACK:
            return "%s (MAV_RESULT %d)" % (HARD_ACK[result], result)
        return ""

    # Everything but the altitude is NaN, and that is not tidiness. PX4's
    # navigator takes a finite param5 and param6 as the takeoff position
    # (navigator_main.cpp:622), so a well meant 0.0 commands a takeoff towards
    # latitude 0, longitude 0. The vehicle arms, sits there, and PX4 disarms it
    # ten seconds later under COM_DISARM_PRFLT. That is what the first four
    # vehicle run of this script actually did, on all four vehicles.
    def takeoff(self, now):
        base = self.home_alt if self.home_alt is not None else self.global_alt
        self.send(now, CMD_TAKEOFF, NAN, NAN, NAN, NAN, NAN, NAN,
                  base + HOLD_ALT)

    # Same trap. A finite lat and lon here is an instruction to fly somewhere
    # and land there, not to land where the vehicle is.
    def land(self, now):
        self.send(now, CMD_LAND, 0.0, 0.0, 0.0, NAN, NAN, NAN, NAN)

    def go(self, now, state):
        self.state = state
        self.entered = now
        self.tries = 0
        print("  %-6s %-12s t=%6.1fs  alt=%5.1f m"
              % (self.name, state, now, self.alt()), flush=True)

    def fail(self, now, reason):
        self.reason = reason
        self.state = "failed"
        self.entered = now
        print("  %-6s %-12s t=%6.1fs  %s"
              % (self.name, "FAILED", now, reason), flush=True)

    def tick(self, now):
        if self.state in ("done", "failed"):
            return
        held = now - self.entered

        if self.state == "connect":
            if self.beats > 0 and self.conn.target_system:
                self.go(now, "wait_pos")
            elif held > T_CONNECT:
                self.fail(now, "no MAVLink heartbeat on udp 127.0.0.1:%d after "
                               "%.0fs. PX4 did not open its API link."
                               % (self.port, held))

        elif self.state == "wait_pos":
            if self.z is not None and (self.home_alt is not None
                                       or self.global_alt is not None):
                self.z_ground = self.z
                self.go(now, "arming")
                self.send(now, CMD_ARM, 1.0)
            elif held > T_POSITION:
                self.fail(now, "no local and global position after %.0fs, so "
                               "the estimator never converged. %s"
                          % (held, self.tail()))

        elif self.state == "arming":
            if self.armed:
                self.go(now, "climb")
                self.takeoff(now)
            elif held > T_ARM:
                self.fail(now, "never armed in %.0fs. arm ack=%s. %s"
                          % (held, self.acks.get(CMD_ARM), self.tail()))
            elif now - self.sent_at > RESEND_S:
                self.send(now, CMD_ARM, 1.0)

        elif self.state == "climb":
            bad = self.rejected(CMD_TAKEOFF)
            if self.alt() >= HOLD_ALT - ALT_TOL:
                self.go(now, "hold")
            elif bad and self.tries >= 2:
                self.fail(now, "takeoff rejected: %s. %s" % (bad, self.tail()))
            elif not self.armed and held > 9.0:
                self.fail(now, "disarmed %.0fs after arming without leaving "
                               "the ground, which is PX4 giving up on a "
                               "takeoff that never started (COM_DISARM_PRFLT). "
                               "takeoff ack=%s. %s"
                          % (held, self.acks.get(CMD_TAKEOFF), self.tail()))
            elif held > T_CLIMB:
                self.fail(now, "reached %.1f m of %.1f m in %.0fs. takeoff "
                               "ack=%s. %s"
                          % (self.alt(), HOLD_ALT, held,
                             self.acks.get(CMD_TAKEOFF), self.tail()))
            elif ((bad or now - self.sent_at > 4.0)
                    and self.alt() < 0.5 and self.tries < 2):
                self.tries += 1
                self.takeoff(now)

        elif self.state == "hold":
            if not self.armed:
                self.fail(now, "disarmed while holding at %.1f m. %s"
                          % (self.alt(), self.tail()))
            elif held >= HOLD_S:
                self.go(now, "landing")
                self.land(now)

        elif self.state == "landing":
            bad = self.rejected(CMD_LAND)
            if (self.landed_state == ON_GROUND and not self.armed
                    and self.alt() < 1.0):
                self.go(now, "done")
            elif bad and self.tries >= 2:
                self.fail(now, "land rejected at %.1f m: %s. %s"
                          % (self.alt(), bad, self.tail()))
            elif held > T_LAND:
                self.fail(now, "still at %.1f m after %.0fs, landed_state=%d, "
                               "armed=%s, land ack=%s. %s"
                          % (self.alt(), held, self.landed_state, self.armed,
                             self.acks.get(CMD_LAND), self.tail()))
            elif ((bad or now - self.sent_at > 20.0) and self.tries < 2
                    and self.landed_state not in (ON_GROUND, LANDING)):
                self.tries += 1
                self.land(now)

    def sample(self, now, reason=False):
        row = {"t": round(now, 2),
               "state": self.state,
               "alt_m": round(self.alt(), 3),
               "z": None if self.z is None else round(self.z, 3),
               "armed": self.armed,
               "landed_state": self.landed_state}
        if reason:
            # The last row carries why, so the record is readable on its own
            # and a failure does not live only in this terminal's scrollback.
            row["reason"] = self.reason
            row["final"] = True
        self.track.write(json.dumps(row) + "\n")

    def close(self, now):
        self.sample(now, reason=True)
        self.track.flush()
        self.track.close()


fleet = [Vehicle(i) for i in range(VEHICLES)]
resources = open(os.path.join(TRACK_DIR, "resources.jsonl"), "w",
                 encoding="utf-8")

started = time.time()
next_sample = 0.0
next_resource = 0.0
while True:
    now = time.time() - started
    for v in fleet:
        v.drain()
        v.tick(now)

    if now >= next_sample:
        next_sample = now + SAMPLE_S
        for v in fleet:
            v.sample(now)

    if now >= next_resource:
        next_resource = now + RESOURCE_S
        mem = meminfo()
        resources.write(json.dumps({
            "t": round(now, 1),
            "mem_available_mib": mem.get("MemAvailable", 0) // 1024,
            "swap_used_mib": (mem.get("SwapTotal", 0)
                              - mem.get("SwapFree", 0)) // 1024,
        }) + "\n")
        resources.flush()

    if all(v.state in ("done", "failed") for v in fleet):
        break
    if now > DEADLINE:
        for v in fleet:
            if v.state not in ("done", "failed"):
                v.fail(now, "the %.0fs flight deadline passed in state %s"
                       % (DEADLINE, v.state))
        break
    time.sleep(0.02)

closed_at = time.time() - started
for v in fleet:
    v.close(closed_at)
resources.close()

flown = sum(1 for v in fleet if v.state == "done")
print("  %d of %d vehicles completed the profile" % (flown, len(fleet)),
      flush=True)
sys.exit(0 if flown == len(fleet) else 1)
PY

ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '  flight helper exited %s\n' "$FLIGHT_RC"

# ------------------------------------------------------------------ teardown
# Before the verdict, not after. A machine left holding a gzserver is a worse
# outcome than an unread verdict, and the next gate would inherit it.
gsay "tearing the simulator down"
stop_everything

left=""
for name in px4 gzserver gzclient; do
  n="$(pgrep -xc "$name" || true)"
  [ -n "$n" ] || n=0
  [ "$n" -eq 0 ] || left="${left} ${name}(${n})"
done
if [ -n "$left" ]; then
  pgrep -a px4 || true
  pgrep -a gzserver || true
  pgrep -a gzclient || true
  gdie "teardown left processes running:${left}. Nothing may survive this script."
fi
printf '  nothing named px4, gzserver or gzclient is left running\n'

# ------------------------------------------------------------------- verdict
gsay "reading the tracks back"
python3 - "$RECORD" "$TRACK_DIR" "$VEHICLES" "$HOLD_ALT_M" "$ALT_TOL_M" \
         "$HOLD_S" "$RUN_ID" "$STARTED" "$ENDED" "$FLIGHT_RC" "$LAUNCH_LOG" \
         <<'PY' || gdie "not every vehicle reached its hold altitude and landed. The table above says which, and the tracks are still in the directory it names."
"""Decide the run from the tracks, then write the record.

This runs after the simulator is gone, and it reads nothing but the files the
flight left behind. That is the point: a command that returned successfully and
a vehicle that climbed are different claims, and only one of them is evidence.
"""
import json
import os
import sys

record_path = sys.argv[1]
track_dir = sys.argv[2]
vehicles = int(sys.argv[3])
hold_alt = float(sys.argv[4])
alt_tol = float(sys.argv[5])
hold_s = float(sys.argv[6])
run_id = sys.argv[7]
started = sys.argv[8]
ended = sys.argv[9]
flight_rc = int(sys.argv[10])
launch_log = sys.argv[11]

ON_GROUND = 1
IN_AIR = 2
TAKING_OFF = 3
MIN_SAMPLES = 50


def read_rows(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def terminal_index(rows):
    """Index of the row where the vehicle stopped flying, or the last row."""
    for n, row in enumerate(rows):
        if row.get("state") in ("done", "failed"):
            return n
    return len(rows) - 1


per_vehicle = []
for i in range(vehicles):
    name = "uav_%d" % (i + 1)
    path = os.path.join(track_dir, "%s.jsonl" % name)
    rows = read_rows(path)
    last = rows[-1] if rows else {}
    # The flight is the rows up to touchdown. Nothing after it is a measurement
    # of a flight. The sampling loop runs until the slowest vehicle finishes,
    # so one that landed at t=32s still collects 326s of rows while it sits
    # disarmed on the ground, and across that tail the estimated altitude free
    # runs: uav_1 wandered from -1.60 m to +1.63 m without moving. Taking the
    # landing off the last row in the file therefore decided uav_1, uav_2 and
    # uav_4 by where their estimators happened to be when an unrelated
    # vehicle's 240s land timeout expired. All three had touched down below
    # 0 m, all three were written down at 1.03 m, 1.09 m and 1.10 m, and all
    # three missed a 1.0 m threshold by under 11 cm. That is a gate failing
    # three correct flights on a coin toss.
    cut = terminal_index(rows)
    flight = rows[:cut + 1]
    term = rows[cut] if rows else {}
    alts = [float(r.get("alt_m", 0.0)) for r in flight]
    states = [int(r.get("landed_state", 0)) for r in flight]

    entry = {
        "id": name,
        "instance": i,
        "mavlink_udp_port": 14540 + i,
        "track": path,
        "samples": len(rows),
        "max_altitude_m": round(max(alts), 2) if alts else 0.0,
        "touchdown_altitude_m": round(float(term.get("alt_m", 0.0)), 2),
        "touchdown_t_s": term.get("t"),
        # Recorded beside the touchdown figure on purpose. The gap between the
        # two is the drift, and a reader who cannot see the drift has no reason
        # to believe the touchdown number.
        "final_altitude_m": round(float(last.get("alt_m", 0.0)), 2),
        "final_landed_state": last.get("landed_state"),
        "final_armed": bool(last.get("armed", True)) if rows else True,
        "final_state": last.get("state"),
        "reason": last.get("reason") or "",
    }
    entry["enough_samples"] = len(rows) >= MIN_SAMPLES
    entry["reached_hold"] = entry["max_altitude_m"] >= hold_alt - alt_tol
    entry["was_airborne"] = any(s in (IN_AIR, TAKING_OFF) for s in states)
    # landed_state and armed are latched states PX4 reports, not estimator
    # output, and they held for the entire tail on every vehicle that landed.
    # Reading them from the last row is both safe and the stronger claim: the
    # vehicle was still down and still disarmed when the run ended, not merely
    # at the instant it touched.
    entry["on_ground"] = last.get("landed_state") == ON_GROUND
    entry["disarmed"] = not entry["final_armed"]
    # A position cross-check on the "and landed" half of the contract, kept
    # because on_ground and disarmed are not two witnesses. Both come off PX4's
    # land detector: it sets landed_state, and commander's auto-disarm is
    # downstream of the same signal. Drop this and the entire second half of
    # the contract rests on one detector, and that detector is the component
    # that misbehaved on uav_3, which reported LANDING for 240s.
    #
    # The measure is a descent, not an altitude, and the difference is the
    # whole point. The first version asked for touchdown_altitude_m <= 1.0 and
    # was a check that had never once worked: it failed three correct flights
    # and passed the only genuine failure anyone has captured. Absolute
    # altitude at touchdown carries the estimator's bias, which is large and
    # differs per vehicle. Across the two green passes every touchdown read
    # negative, from -0.77 m to -3.04 m, a 2.3 m spread over four vehicles
    # that landed on the same flat ground, and a one-sided threshold waved all
    # of it through. A descent is that same estimator subtracted from itself,
    # so a constant bias cancels and no new number has to be invented: reuse
    # the climb threshold from reached_hold and ask that the vehicle came back
    # down by at least as much as it had to go up.
    #
    # What this catches that the latched states do not: a land detector that
    # fires while the position still says hold altitude. That gives
    # 5.16 - 5.0 = 0.16 m against 4.0 m and fails, while on_ground and
    # disarmed both report a clean landing. It is the mirror of the uav_3
    # fault, and it is the only failure mode here that no other criterion sees.
    #
    # What it does not catch, and is not meant to: uav_3 itself. uav_3 came
    # down, 4.65 m to -0.31 m, a 4.96 m descent that passes this comfortably.
    # Its position was never the problem. It never touched down and never
    # disarmed, and on_ground and disarmed are the witnesses for that.
    #
    # Where this would bite a good flight: it shares a threshold with
    # reached_hold, so a vehicle that only just clears the climb and then
    # touches down with an upward bias could miss by centimetres. Every
    # touchdown measured so far is biased downward and the tightest real
    # margin is uav_3's 4.96 m against 4.0 m, but that is the corner to look
    # at first if this ever fails a flight that looked right.
    entry["descent_m"] = round(entry["max_altitude_m"]
                               - entry["touchdown_altitude_m"], 2)
    entry["descended"] = entry["descent_m"] >= hold_alt - alt_tol

    why = []
    if not entry["enough_samples"]:
        why.append("only %d samples in the track, under the %d a real flight "
                   "leaves" % (entry["samples"], MIN_SAMPLES))
    if not entry["reached_hold"]:
        why.append("peaked at %.2f m, short of %.1f m less the %.1f m "
                   "tolerance" % (entry["max_altitude_m"], hold_alt, alt_tol))
    if not entry["was_airborne"]:
        why.append("no sample ever reported IN_AIR or TAKING_OFF")
    if not entry["on_ground"]:
        why.append("ended at landed_state=%s, not ON_GROUND (%d)"
                   % (entry["final_landed_state"], ON_GROUND))
    if not entry["disarmed"]:
        why.append("still armed when the run ended")
    if not entry["descended"]:
        why.append("descended %.2f m from a peak of %.2f m, short of the "
                   "%.1f m a landing from the hold altitude has to show"
                   % (entry["descent_m"], entry["max_altitude_m"],
                      hold_alt - alt_tol))
    entry["pass"] = not why
    # A FAIL with an empty reason is a defect of its own: it tells whoever
    # reads the record that something is wrong and nothing about what, which is
    # how three good flights ended up in runs/ looking like a mystery. The
    # flight helper writes a reason only when it gave up on a vehicle, so a
    # vehicle that flew the profile and still fails the verdict arrives here
    # with nothing to say for itself. Say it for it.
    if why:
        verdict = "; ".join(why)
        entry["reason"] = ("%s. verdict: %s" % (entry["reason"], verdict)
                           if entry["reason"] else verdict)
    per_vehicle.append(entry)

resources = read_rows(os.path.join(track_dir, "resources.jsonl"))
mem = [int(r.get("mem_available_mib", 0)) for r in resources]
swap = [int(r.get("swap_used_mib", 0)) for r in resources]


def yes(flag):
    return "yes" if flag else "NO"


print("  vehicle  samples  max alt  touchdown  descent  final alt  airborne  "
      "on ground  disarmed  verdict")
for e in per_vehicle:
    print("  %-7s  %7d  %6.1fm  %8.1fm  %6.1fm  %8.1fm  %8s  %9s  %8s  %s"
          % (e["id"], e["samples"], e["max_altitude_m"],
             e["touchdown_altitude_m"], e["descent_m"],
             e["final_altitude_m"], yes(e["was_airborne"]),
             yes(e["on_ground"]), yes(e["disarmed"]),
             "pass" if e["pass"] else "FAIL"))
for e in per_vehicle:
    if not e["pass"]:
        print("  %s: %s" % (e["id"], e["reason"]))

if mem:
    print("  memory available fell to %d MiB, swap used peaked at %d MiB"
          % (min(mem), max(swap) if swap else 0))

passed = all(e["pass"] for e in per_vehicle) and len(per_vehicle) == vehicles

record = {
    "kind": "smoke-placeholder",
    "note": ("Chunk 1.7 owns the run record writer, the schema and the run id "
             "grammar. This file is what run_smoke.sh could honestly measure "
             "before that existed, it does not validate against "
             "scenarios/run-record.schema.json, and run_smoke.sh will be "
             "rewritten to call the real writer. Nothing publishes "
             "latest.jsonl from here: that is the runner's job, and a smoke "
             "record sitting there would satisfy a gate written for a "
             "scenario run."),
    "run_id": run_id,
    "result": "pass" if passed else "fail",
    "started_utc": started,
    "ended_utc": ended,
    "headless": True,
    "launcher": "scripts/sitl_multi.sh",
    "launcher_log": launch_log,
    "control_path": "pymavlink, MAVLink COMMAND_LONG on udp 14540+instance",
    "flight_helper_exit": flight_rc,
    "vehicles_requested": vehicles,
    "vehicles_passed": sum(1 for e in per_vehicle if e["pass"]),
    "hold_altitude_m": hold_alt,
    "altitude_tolerance_m": alt_tol,
    "hold_seconds": hold_s,
    "track_dir": track_dir,
    "per_vehicle": per_vehicle,
    "mem_available_min_mib": min(mem) if mem else None,
    "mem_available_start_mib": mem[0] if mem else None,
    "swap_used_max_mib": max(swap) if swap else None,
    "resource_samples": len(resources),
}

tmp = record_path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(record, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, record_path)
print("  record written to %s" % record_path)

sys.exit(0 if passed else 1)
PY

gsay "smoke run passed: ${VEHICLES} vehicles reached ${HOLD_ALT_M} m and landed"
printf '  record   %s\n' "$RECORD"
printf '  tracks   %s\n' "$TRACK_DIR"
printf '  launcher %s\n' "$LAUNCH_LOG"
