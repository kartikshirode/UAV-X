"""The complete harness: fly a scenario, measure it, and publish one record.

Chunk 1.7. This is the piece that composes the other six. It loads the
scenario, brings the stack up through scripts/sitl_multi.sh, samples poses and
memory on ROS simulated time, drives the event injector, captures the ROS graph
while the scenario is still running, tears everything down, and only then
writes the record and publishes the two `latest` files.

Where the numbers come from, and why
------------------------------------

**Time.** Every `_s` value in the record is ROS simulated time from `/clock`,
with zero at scenario start. Wall time appears only in `started_at` and
`ended_at`. Mixing the two would make a seeded replay depend on when it was
launched, which standing rule 4 forbids.

Two measured facts about `/clock` on this stack. It exists because
scripts/sitl_multi.sh starts gzserver with `libgazebo_ros_init.so`, and that
plugin publishes it BEST_EFFORT. A subscription left on the rclpy default of
RELIABLE receives nothing at all and logs one line about incompatible QoS,
which reads exactly like a simulator that has not started yet. `CLOCK_QOS`
below is the fix.

**Poses.** From each vehicle's own PX4 state estimate, read as
`LOCAL_POSITION_NED` over the MAVLink API link PX4 opens on UDP
14540 + instance. Not from the simulator's ground-truth pose interface. Three
reasons, in order:

  1. scripts/check_seam.sh forbids every source file outside
     `uavx_comms/link_layer` and `uavx_eval` from reading simulator ground
     truth, and `uavx_sim` is neither. A runner that read it would fail the
     static pass in chunk 3.3.
  2. Round 3 finding 2 records the other half of that rule: a vehicle's own
     PX4 estimate is explicitly allowed, because banning it would make a
     correct implementation unbuildable.
  3. Measured on this machine on 1 September 2026: the ground-truth pose topic
     did not exist here at all. sitl_multi.sh loaded the init and factory
     plugins and neither publishes model state; `ros2 topic list` during a
     four vehicle bring-up returned 176 topics and none of them was one.
     Chunk 2.4 added libgazebo_ros_state to the launcher because the metrics
     collector scores coverage off exactly that topic. The collector is one of
     the two privileged readers; this runner still is not, and still does not.

**Survey.** Chunk 2.4. When the scenario carries a `survey` block the runner
also starts one mission executor per vehicle and the metrics collector, puts
each vehicle into PX4's offboard mode so it flies the setpoints its executor
publishes, and reads the collector's last payload off `/uavx_eval/metrics`
into the record. Every decision in that sentence is made in uavx_sim.survey,
which has no ROS in it and is tested without a simulator; this file only
carries them out. What the runner adds is the one thing a test cannot: the
mode change is observed in the vehicle's own HEARTBEAT before it counts, and
the cruise speed is confirmed by PX4 echoing the parameter back.

Reading them over MAVLink rather than over the uXRCE-DDS bridge is the same
decision taken twice. Both are the vehicle's own estimate. MAVLink is the path
scripts/run_smoke.sh already proved on this machine, it needs no px4_msgs
import inside uavx_sim, and it puts no per vehicle endpoint on the runner's ROS
node, so the runner never names a second vehicle's namespace.

**Injection.** `kill` stops the target's PX4 process. The effect is observed on
the target, never assumed from the request: the injector only stamps
`observed_t` once that instance's process is gone and its telemetry has been
silent for `KILL_SILENCE_S` of simulated time. Measured on 1 September 2026:
killing one of four PX4 instances does not stall the lockstep simulation. The
other three carried on publishing at 30 Hz and the killed one stopped dead,
which is what makes telemetry silence an honest witness.

**Publication.** `<runs-dir>/<run_id>.jsonl` and `<runs-dir>/<run_id>-graph.json`
are written after teardown. `latest.jsonl` and `latest-graph.json` are published
by atomic rename after that, and only when the run completed. Any non-zero exit
leaves neither behind.

Nothing heavy is imported at module scope. rclpy, pymavlink and the graph
capture's subprocesses are all reached inside functions, so this module imports
on a machine with no ROS, which is what lets test/test_run_record.py run on a
clean checkout.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from uavx_sim import run_record
from uavx_sim.event_injector import EventInjector
from uavx_sim.graph_snapshot import (CaptureFailed, IncompleteSnapshot,
                                     capture_snapshot, sha256_of, utc_stamp,
                                     write_snapshot)
from uavx_sim.resource_sampler import ResourceSampler, ResourceSamplerError
from uavx_sim.scenario import ScenarioError
from uavx_sim.scenario import load as load_scenario
from uavx_sim.survey import (CRUISE_SPEED_PARAM, SurveyError,
                             collector_command, coverage_from_payload,
                             mission_node_command, model_map, survey_spec)

# architecture.md section 1b freezes these. Nothing here invents a code, and a
# path that cannot say which of them it is has no business returning at all.
EXIT_OK = 0
EXIT_ARGS = 10
EXIT_DEPENDENCY = 20
EXIT_CHILD = 30
EXIT_TIMEOUT = 31
EXIT_ARTIFACT = 32
EXIT_RECORDING = 40

# scripts/seam_manifests.json requires exactly this node of a harness_check
# graph. It is the runner's whole ROS presence.
NODE_NAME = "scenario_runner"

# PX4 opens its API link here, one port per instance, in
# build/px4_sitl_default/etc/init.d-posix/px4-rc.mavlink.
MAVLINK_BASE_PORT = 14540

# MAVLink command ids and flags, spelled out rather than imported, so a reader
# does not have to open pymavlink to know what is being sent.
CMD_ARM = 400            # MAV_CMD_COMPONENT_ARM_DISARM
CMD_TAKEOFF = 22         # MAV_CMD_NAV_TAKEOFF
ARMED_FLAG = 128         # MAV_MODE_FLAG_SAFETY_ARMED
IN_AIR = 2               # MAV_LANDED_STATE_IN_AIR
TAKING_OFF = 3

# Wall time budgets for the preparation phase, which happens before scenario
# time zero and is therefore not part of the run. Sized from the measured
# climb: PX4's default MPC_TKO_SPEED is 1.5 m/s, so the 60 m layer takes about
# 40 s and the whole fleet needs a little over a minute.
READY_TIMEOUT_S = 300.0
PREP_DEADLINE_S = 300.0
T_CONNECT_S = 90.0
T_POSITION_S = 150.0
T_ARM_S = 120.0
T_CLIMB_S = 120.0
RESEND_S = 5.0
HOVER_TOLERANCE_M = 2.0
# How many times a vehicle may arm and be asked to take off before the
# preparation phase writes it off. See Vehicle.prepare for the measurement.
TAKEOFF_CYCLES = 3
# A climb that gains less than a metre in this long has stopped climbing.
STALLED_CLIMB_S = 20.0

# Scenario time cadences.
# Week 1 audit finding 10. This was 0.2, which is 5 Hz, while architecture.md
# names 20 Hz twice as frozen: once as the coverage source and once as the rate
# the separation monitor runs at. Week 2 computes coverage_fraction from sampled
# poses and never from the planned path, so a quarter of the intended resolution
# is not a smaller number, it is a different measurement. The rate is
# deliberately not a scenario knob. The design freezes it, and a scenario able to
# override it could quietly change what its own coverage figure meant.
POSE_HZ = 20.0               # frozen in architecture.md, section 6
POSE_PERIOD_S = 1.0 / POSE_HZ
# What we ask PX4 to stream. Above the sampling target on purpose, so the rate
# is decided by the sampler rather than by whatever default PX4 chose for the
# API link. A sample is only counted when the estimate is fresh, so a stream
# slower than the target silently caps the rate and the record would claim a
# resolution it never had.
POSE_STREAM_HZ = 50.0
RESOURCE_PERIOD_S = 1.0      # gate asks for resources.samples >= 10
GRAPH_CAPTURE_FRACTION = 0.75

# How long a killed vehicle has to stay silent before the injector will call
# the effect observed. Telemetry arrives at 30 Hz, so this is sixty missing
# messages and not a dropped packet.
KILL_SILENCE_S = 2.0

# Chunk 2.4. A survey needs the vehicles in PX4's offboard mode, flying the
# setpoints their mission executors publish. MAV_CMD_DO_SET_MODE with the
# custom mode flag and PX4's main mode 6 is how MAVLink asks for it, and the
# HEARTBEAT's custom_mode carries the main mode in bits 16 to 23, which is how
# the runner knows the request was granted rather than merely sent. PX4 refuses
# the switch until OffboardControlMode has been arriving, so the executors are
# given NODE_SETTLE_S to find their PX4 before anybody asks.
CMD_SET_MODE = 176               # MAV_CMD_DO_SET_MODE
CUSTOM_MODE_ENABLED = 1.0        # MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
PX4_MAIN_MODE_OFFBOARD = 6
PX4_MAIN_MODES = {1: "manual", 2: "altitude", 3: "position", 4: "auto",
                  5: "acro", 6: "offboard", 7: "stabilized", 8: "rattitude"}
MAV_PARAM_TYPE_REAL32 = 9

# PX4's simulated battery drains to SIM_BAT_MIN_PCT over SIM_BAT_DRAIN, and the
# defaults are 50 percent over 60 seconds. Every run longer than a minute
# therefore ends up flying on a battery warning, which is a property of the
# simulator and not of the mission. Raising the floor to 100 stops the drain
# without touching COM_LOW_BAT_ACT, so a real low-battery action would still
# fire if a scenario ever wanted one.
SIM_BATTERY_FLOOR_PARAM = "SIM_BAT_MIN_PCT"
SIM_BATTERY_FLOOR_PCT = 100.0
NODE_SETTLE_S = 6.0              # wall seconds for the nodes to find PX4
PARAM_DEADLINE_S = 30.0          # wall seconds for a PARAM_SET to be echoed
OFFBOARD_DEADLINE_S = 90.0       # wall seconds for the fleet to enter offboard
METRICS_TOPIC = "/uavx_eval/metrics"
METRICS_FINAL_WAIT_S = 15.0      # for the collector's last payload on shutdown
COLLECTOR_LABEL = "metrics-collector"

# The simulation is lockstep, so a clock that stops advancing means a process
# is wedged rather than that the run is slow.
CLOCK_STALL_S = 60.0
CLOCK_WAIT_S = 120.0

READY_MARKER = "vehicles up and healthy"

# How many times to ask scripts/sitl_multi.sh for a stack before calling the
# machine broken. See Harness.bring_up for the measurement behind this.
BRING_UP_ATTEMPTS = 3


class HarnessFailure(RuntimeError):
    """A run that cannot finish, carrying the exit code the contract fixes."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class ArgumentError(HarnessFailure):
    """Bad input. Raised before anything is launched, so exit 10 costs nothing."""

    def __init__(self, message):
        super().__init__(message, EXIT_ARGS)


# ------------------------------------------------------------------- inputs
def find_repo():
    """The repository this runner belongs to.

    `UAVX_REPO` comes from scripts/gate-env.sh and is the answer whenever the
    runner was started the way every gate starts it. The walk up from this file
    is the fallback for a direct `python3 -m` invocation, and both candidates
    are confirmed by looking for two files rather than trusted for their shape.
    """
    candidates = []
    named = os.environ.get("UAVX_REPO")
    if named:
        candidates.append(Path(named))
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        if ((candidate / "scripts" / "source_tree_hash.py").is_file()
                and (candidate / "stage-1" / "setup" / "versions.lock").is_file()):
            return candidate
    raise HarnessFailure(
        "cannot find the repository root from UAVX_REPO or from this file. "
        "The record needs scripts/source_tree_hash.py and "
        "stage-1/setup/versions.lock, and a record without them is not "
        "provenance.", EXIT_DEPENDENCY)


def parse_args(argv):
    """The flags architecture.md section 1b freezes, and nothing else.

    Hand rolled rather than argparse, because argparse exits 2 on a bad flag
    and the contract fixes invalid arguments at 10. An exit code nobody
    documented is the same problem as a wrong one.
    """
    options = {"scenario": None, "run_id": None, "record": None,
               "record_seconds": None, "overlay_text": None, "runs_dir": None}
    takes_value = {"--run-id": "run_id", "--record": "record",
                   "--record-seconds": "record_seconds",
                   "--overlay-text": "overlay_text", "--runs-dir": "runs_dir"}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in takes_value:
            if index + 1 >= len(argv):
                raise ArgumentError(f"{token} needs a value")
            value = argv[index + 1]
            if not value or value.startswith("--"):
                raise ArgumentError(f"{token} needs a value, got {value!r}")
            options[takes_value[token]] = value
            index += 2
            continue
        if token.startswith("-"):
            raise ArgumentError(
                f"unknown option {token}. This runner takes a scenario path, "
                f"--run-id, --record, --record-seconds, --overlay-text and "
                f"--runs-dir.")
        if options["scenario"] is not None:
            raise ArgumentError(
                f"two scenarios given, {options['scenario']!r} and {token!r}. "
                f"One run is one scenario.")
        options["scenario"] = token
        index += 1

    if not options["scenario"]:
        raise ArgumentError("no scenario given. The path is required.")
    if options["record_seconds"] is not None:
        try:
            options["record_seconds"] = float(options["record_seconds"])
        except ValueError:
            raise ArgumentError(
                f"--record-seconds must be a number, got "
                f"{options['record_seconds']!r}") from None
        if options["record_seconds"] <= 0:
            raise ArgumentError(
                f"--record-seconds must be positive, got "
                f"{options['record_seconds']}")
    if options["record"]:
        for flag, key in (("--record-seconds", "record_seconds"),
                          ("--overlay-text", "overlay_text")):
            if options[key] is None:
                raise ArgumentError(
                    f"--record requires {flag}. A clip nobody can tie to a run "
                    f"is a clip that proves nothing.")
    elif options["record_seconds"] is not None or options["overlay_text"]:
        raise ArgumentError(
            "--record-seconds and --overlay-text only mean anything with "
            "--record.")
    return options


def require_dependencies(repo):
    """Everything the run needs, checked before a simulator is started.

    Standing rule 5: assert on the artifact. A package manager's opinion that
    Gazebo is installed once let this repo run green on a machine with no
    simulator binaries at all.
    """
    import shutil

    missing = []
    for tool in ("ros2", "gzserver", "MicroXRCEAgent"):
        if shutil.which(tool) is None:
            missing.append(f"{tool} is not on PATH")

    px4_dir = Path(os.environ.get("UAVX_PX4_DIR",
                                  Path.home() / "PX4-Autopilot"))
    px4_binary = px4_dir / "build" / "px4_sitl_default" / "bin" / "px4"
    if not os.access(px4_binary, os.X_OK):
        missing.append(f"no PX4 SITL binary at {px4_binary}")

    launcher = repo / "scripts" / "sitl_multi.sh"
    if not launcher.is_file():
        missing.append(f"no launcher at {launcher}")

    for module in ("rclpy", "rosgraph_msgs", "pymavlink", "yaml"):
        try:
            __import__(module)
        except ImportError as exc:
            missing.append(f"python module {module} is not importable: {exc}")

    if missing:
        raise HarnessFailure(
            "the run cannot start: " + "; ".join(missing), EXIT_DEPENDENCY)
    return launcher


# ------------------------------------------------------------- the processes
def frozen_min_separation(repo):
    """MIN_SEPARATION as scripts/check_geometry.py defines it, never a copy.

    The safety floor is frozen in architecture.md section 5 and has one home
    in code. The collector needs it as a parameter, and a number typed here
    would be the third copy of a value this repository has already found
    drifting once.
    """
    import importlib.util

    path = Path(repo) / "scripts" / "check_geometry.py"
    spec = importlib.util.spec_from_file_location("uavx_check_geometry", path)
    if spec is None or spec.loader is None:
        raise HarnessFailure(f"cannot load {path} for the separation floor",
                             EXIT_DEPENDENCY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return float(module.MIN_SEPARATION)


def _process_table():
    """Every process on this machine, as (pid, argv), read from /proc.

    /proc rather than pgrep, because the injector has to pick one PX4 instance
    out of four and `pgrep -f` matches a pattern against a whole command line.
    Reading argv means the instance number is compared as an argument.
    """
    table = []
    proc = Path("/proc")
    if not proc.is_dir():
        return table
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if argv:
            table.append((int(entry.name), argv))
    return table


def _pids_named(names, table=None):
    table = _process_table() if table is None else table
    found = []
    for pid, argv in table:
        if Path(argv[0]).name in names:
            found.append(pid)
    return found


def px4_instances(table=None):
    """Instance number to pids, from each PX4 process's own `-i` argument."""
    table = _process_table() if table is None else table
    instances = {}
    for pid, argv in table:
        if Path(argv[0]).name != "px4" or "-i" not in argv:
            continue
        position = argv.index("-i")
        if position + 1 < len(argv) and argv[position + 1].isdigit():
            instances.setdefault(int(argv[position + 1]), []).append(pid)
    return instances


def refresh_ros_daemon(timeout=60):
    """Restart the ros2 CLI daemon before the stack comes up.

    Two things this run depends on go through that daemon: the launcher's own
    readiness check, which asks `ros2 topic list` whether every vehicle's
    namespace is visible, and the graph capture, which asks `ros2 node list`
    and `ros2 node info`.

    Measured on 1 September 2026, after a run had killed a PX4 instance and
    torn the stack down. `ros2 daemon status` said the daemon was running and
    `ros2 topic list` raised an XMLRPC traceback out of `parse_response` and
    printed nothing at all on stdout. The launcher pipes that through
    `2>/dev/null | grep -c`, so a wedged daemon reads as zero topics for every
    vehicle, which is what three bring-up attempts in a row reported. Stopping
    and starting it fixed it immediately.

    Restarting is also the honest choice for the capture. A daemon carrying a
    cache from the previous run can answer for nodes that are no longer there,
    and a snapshot is supposed to be a graph of this run.
    """
    for verb in ("stop", "start"):
        try:
            subprocess.run(["ros2", "daemon", verb], capture_output=True,
                           text=True, timeout=timeout, check=False)
        except (OSError, subprocess.SubprocessError):
            # A daemon that cannot be restarted is not on its own a reason to
            # abandon the run. The launcher's own check is what refuses.
            return False
        time.sleep(2.0)
    return True


def _pkill(signal_flag, name):
    subprocess.run(["pkill", signal_flag, "-x", name],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)


class Launcher:
    """scripts/sitl_multi.sh, started as a child and torn down from a finally.

    The launcher is never replaced with a local copy of its work. It clears the
    per instance PX4 state so a run replays, it refuses to start a GUI, and
    reimplementing either here would mean two launchers to keep in step.
    """

    def __init__(self, repo, launcher, vehicles, hold_s, log_path):
        self._repo = repo
        self._launcher = launcher
        self._vehicles = vehicles
        self._hold_s = hold_s
        self._log_path = Path(log_path)
        self.process = None

    def start(self):
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._log_path, "wb")
        try:
            self.process = subprocess.Popen(
                ["bash", str(self._launcher), "--vehicles", str(self._vehicles),
                 "--hold", str(int(self._hold_s))],
                stdout=handle, stderr=subprocess.STDOUT, cwd=str(self._repo),
                # Its own session, so the whole tree can be signalled at once.
                # The launcher spends its hold inside `sleep`, and a TERM to the
                # script alone waits for that sleep to return.
                start_new_session=True)
        finally:
            handle.close()

    def log_text(self):
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def wait_ready(self, timeout_s):
        """Wait for the launcher's own health assertions, not for a pid count.

        Flying while it is still checking would let one of its own failure
        paths tear the stack down underneath the run, and the failure would
        then be reported against the wrong thing.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if READY_MARKER in self.log_text():
                return True
            if self.process.poll() is not None:
                return False
            time.sleep(2.0)
        return False

    def stop(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        waited = 0.0
        while waited < 20.0 and self.process.poll() is None:
            time.sleep(1.0)
            waited += 1.0
        if self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def stop_everything(launcher=None):
    """Leave nothing running, whatever happened. Same order as run_smoke.sh.

    A later gate that inherits this gzserver measures a simulator it did not
    start, and the preflight refuses to run at all if one is already up.
    """
    if launcher is not None:
        launcher.stop()
    for name in ("px4", "MicroXRCEAgent", "gzserver", "gzclient"):
        _pkill("-TERM", name)
    time.sleep(2.0)
    # A PX4 instance wedged on the simulator socket ignores TERM. Nothing here
    # is worth keeping, so the second pass does not ask twice.
    for name in ("px4", "MicroXRCEAgent", "gzserver", "gzclient"):
        _pkill("-KILL", name)
    time.sleep(1.0)


def still_running():
    table = _process_table()
    left = {}
    for name in ("gzserver", "gzclient", "px4"):
        count = len(_pids_named({name}, table))
        if count:
            left[name] = count
    return left


# ------------------------------------------------------------------- clock
class SimClock:
    """The `/scenario_runner` node and its subscriptions.

    This is the runner's entire ROS presence, and it is deliberately small.
    scripts/seam_manifests.json says an outside process may hold only what it
    is listed for, so the runner takes no per vehicle endpoint and opens no
    service of its own beyond the parameter services rclpy creates. Chunk 2.4
    added one subscription, to the collector's metrics, and it is listed in
    the manifest beside the clock.
    """

    def __init__(self):
        import rclpy
        from rclpy.node import Node
        from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                               ReliabilityPolicy)
        from rosgraph_msgs.msg import Clock

        # Measured on 1 September 2026: gazebo_ros publishes /clock BEST_EFFORT
        # with a depth of one. A subscription on the rclpy default of RELIABLE
        # receives nothing and logs "offering incompatible QoS", which looks
        # exactly like a simulator that never started.
        clock_qos = QoSProfile(depth=1,
                               reliability=ReliabilityPolicy.BEST_EFFORT,
                               history=HistoryPolicy.KEEP_LAST,
                               durability=DurabilityPolicy.VOLATILE)

        self._rclpy = rclpy
        rclpy.init(args=[])
        self._node = Node(NODE_NAME)
        self._seconds = None
        self.messages = 0
        self._node.create_subscription(Clock, "/clock", self._on_clock,
                                       clock_qos)

    def _on_clock(self, message):
        self._seconds = message.clock.sec + message.clock.nanosec * 1e-9
        self.messages += 1

    def subscribe_metrics(self, callback):
        """The collector's payloads, on this same node. Chunk 2.4."""
        from uavx_msgs.msg import RunMetrics

        self._node.create_subscription(RunMetrics, METRICS_TOPIC, callback, 10)

    def spin(self, timeout_s=0.0):
        self._rclpy.spin_once(self._node, timeout_sec=timeout_s)

    def now(self):
        return self._seconds

    def wait_for_time(self, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self.spin(0.1)
            if self._seconds is not None:
                return self._seconds
        return None

    def close(self):
        try:
            self._node.destroy_node()
        finally:
            try:
                self._rclpy.shutdown()
            except Exception:
                # Shutting the context down twice is not a run failure, and a
                # raise here would replace the real error on the way out.
                pass


# ------------------------------------------------------------------ vehicles
class Vehicle:
    """One PX4 instance, over its own MAVLink API link.

    Everything read here is the vehicle's own estimate of itself. Nothing in
    this class knows where any other vehicle is, which is the property the
    static seam pass checks for.
    """

    def __init__(self, index, name, hover_alt_m=None):
        from pymavlink import mavutil

        self.index = index
        self.name = name
        self.hover_alt_m = hover_alt_m
        self.port = MAVLINK_BASE_PORT + index
        self.connection = mavutil.mavlink_connection(
            f"udpin:127.0.0.1:{self.port}", source_system=255,
            source_component=190)

        self.state = "connect"
        # Sampling window, so the record can state the rate it achieved rather
        # than the rate it asked for. Week 1 audit finding 10 moved the target
        # from 5 Hz to the 20 Hz architecture.md freezes, and the first run at
        # the new target produced 2.5 times the samples rather than 4 times.
        # take_sample counts a pose only when the estimate is fresh, so the
        # ceiling is PX4's LOCAL_POSITION_NED stream and not the loop cadence.
        # A coverage figure computed off poses has to know which rate it really
        # got, so both numbers go in the record.
        self.first_sample_s = None
        self.last_sample_s = None
        self.stream_requested = False
        self.entered_wall = 0.0
        self.sent_wall = -99.0
        self.attempts = 0
        self.heartbeats = 0
        self.armed = False
        self.landed_state = 0
        self.z = None
        self.z_ground = None
        self.home_alt = None
        self.global_alt = None
        self.reason = ""
        # What the takeoff was commanded against, kept so the record can say
        # why a vehicle stopped where it did rather than only that it did.
        self.takeoff_base_alt_m = None
        self.commanded_alt_m = None
        self.climb_mark_m = 0.0
        self.climb_mark_wall = 0.0

        self.cycles = 0                  # arm and takeoff attempts spent
        self.fresh = False               # a new position since the last sample
        self.samples = 0
        self.max_altitude_m = 0.0
        self.last_seen_sim_s = None
        self.messages = 0

        # Chunk 2.4. The flight mode PX4 reports and the parameters it has
        # echoed back, so a survey can tell "asked for offboard" from "in it"
        # and "sent the cruise speed" from "PX4 has it".
        self.custom_mode = 0
        self.params = {}
        self.offboard_requests = 0
        self.offboard_wall = None
        self.offboard_reason = ""
        self.surveying = False
        self.cruise_param_confirmed = None

    # ------------------------------------------------------------ telemetry
    def drain(self, sim_s):
        """Read everything waiting on the socket. Never blocks the loop."""
        while True:
            try:
                message = self.connection.recv_match(blocking=False)
            except Exception:
                # A malformed frame must not end a 60 second run. The silence
                # detector below is what notices a vehicle that has gone away.
                return
            if message is None:
                return
            self.messages += 1
            if sim_s is not None:
                self.last_seen_sim_s = sim_s
            kind = message.get_type()
            if kind == "HEARTBEAT":
                self.heartbeats += 1
                self.armed = bool(message.base_mode & ARMED_FLAG)
                self.custom_mode = int(message.custom_mode)
            elif kind == "PARAM_VALUE":
                name = message.param_id
                if isinstance(name, bytes):
                    name = name.decode("ascii", "ignore")
                self.params[str(name).rstrip(chr(0))] = float(message.param_value)
            elif kind == "LOCAL_POSITION_NED":
                self.z = message.z
                self.fresh = True
            elif kind == "EXTENDED_SYS_STATE":
                self.landed_state = message.landed_state
            elif kind == "HOME_POSITION":
                self.home_alt = message.altitude / 1000.0
            elif kind == "GLOBAL_POSITION_INT":
                self.global_alt = message.alt / 1000.0

    def altitude_m(self):
        """Height above where the vehicle sat when it was armed.

        Taking the difference means a non zero EKF origin cannot quietly buy
        the vehicle a metre of climb.
        """
        if self.z is None:
            return 0.0
        base = self.z_ground if self.z_ground is not None else 0.0
        return -(self.z - base)

    def request_pose_stream(self):
        """Ask PX4 for LOCAL_POSITION_NED faster than we intend to sample it.

        PX4 picks a default rate per message for the API link, and asking for
        samples faster than it streams just means most loop ticks find nothing
        fresh. Requesting comfortably above the target leaves the sampler, not
        the telemetry, deciding the rate. Sent once the link is up, and repeated
        is harmless because SET_MESSAGE_INTERVAL is idempotent.
        """
        from pymavlink import mavutil

        target = self.connection.target_system
        if not target:
            return False
        interval_us = int(1e6 / POSE_STREAM_HZ)
        self.connection.mav.command_long_send(
            target, self.connection.target_component,
            mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
            mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED,
            interval_us, 0, 0, 0, 0, 0)
        self.stream_requested = True
        return True

    def achieved_pose_hz(self):
        """Samples per second of simulated time, over the window sampled.

        None when there is not enough of a window to divide by. Two samples a
        hundredth of a second apart would otherwise report a spectacular rate.
        """
        if self.first_sample_s is None or self.last_sample_s is None:
            return None
        window = self.last_sample_s - self.first_sample_s
        if window < 1.0 or self.samples < 2:
            return None
        return round((self.samples - 1) / window, 2)

    def take_sample(self, sim_s=None):
        """Count one pose sample if the estimate moved on since the last one.

        A vehicle that has stopped reporting stops contributing samples, which
        is what makes pose_sample_count a measure of what was observed rather
        than of how long the loop ran.
        """
        if not self.fresh:
            return False
        self.fresh = False
        self.samples += 1
        if sim_s is not None:
            if self.first_sample_s is None:
                self.first_sample_s = sim_s
            self.last_sample_s = sim_s
        self.max_altitude_m = max(self.max_altitude_m, self.altitude_m())
        return True

    def silent_for(self, sim_s):
        if self.last_seen_sim_s is None or sim_s is None:
            return None
        return sim_s - self.last_seen_sim_s

    # ---------------------------------------------------------------- mode
    @property
    def main_mode(self):
        return (self.custom_mode >> 16) & 0xFF

    @property
    def in_offboard(self):
        return self.main_mode == PX4_MAIN_MODE_OFFBOARD

    def mode_name(self):
        return PX4_MAIN_MODES.get(self.main_mode, f"mode_{self.main_mode}")

    def set_param(self, now_wall, name, value):
        """PARAM_SET. PX4 answers with a PARAM_VALUE that drain records."""
        target = self.connection.target_system or (self.index + 1)
        self.connection.mav.param_set_send(
            target, 1, name.encode("ascii"), float(value), MAV_PARAM_TYPE_REAL32)
        self.sent_wall = now_wall

    def request_offboard(self, now_wall):
        self._send(now_wall, CMD_SET_MODE, CUSTOM_MODE_ENABLED,
                   float(PX4_MAIN_MODE_OFFBOARD), 0.0)
        self.offboard_requests += 1

    # -------------------------------------------------------------- control
    def _send(self, now_wall, command, *params):
        arguments = list(params) + [0.0] * (7 - len(params))
        target = self.connection.target_system or (self.index + 1)
        self.connection.mav.command_long_send(target, 1, command, 0, *arguments)
        self.sent_wall = now_wall

    def _takeoff(self, now_wall):
        # Everything but the altitude is NaN, and that is not tidiness. PX4's
        # navigator reads a finite param5 and param6 as the takeoff position,
        # so a well meant 0.0 commands a takeoff towards latitude 0, longitude
        # 0. The vehicle arms, sits there, and PX4 disarms it ten seconds later
        # under COM_DISARM_PRFLT. That is what run_smoke.sh's first four
        # vehicle run actually did, on all four vehicles.
        nan = float("nan")
        base = self.home_alt if self.home_alt is not None else self.global_alt
        self.takeoff_base_alt_m = base
        self.commanded_alt_m = base + self.hover_alt_m
        self.climb_mark_m = self.altitude_m()
        self.climb_mark_wall = now_wall
        self._send(now_wall, CMD_TAKEOFF, nan, nan, nan, nan, nan, nan,
                   self.commanded_alt_m)

    def _enter(self, now_wall, state):
        self.state = state
        self.entered_wall = now_wall
        self.attempts = 0

    def prepare(self, now_wall):
        """Arm and climb to this vehicle's hover altitude. Wall time, on purpose.

        The climb happens before scenario time zero. It is setup for the
        scenario rather than part of it, so it is not measured in simulated
        time and none of it reaches the record's `_s` fields.
        """
        if self.state in ("hold", "failed") or self.hover_alt_m is None:
            return
        held = now_wall - self.entered_wall

        if self.state == "connect":
            if self.heartbeats > 0 and self.connection.target_system:
                self._enter(now_wall, "wait_pos")
            elif held > T_CONNECT_S:
                self.give_up(now_wall, f"no MAVLink heartbeat on udp "
                                       f"127.0.0.1:{self.port} after {held:.0f}s")

        elif self.state == "wait_pos":
            if self.z is not None and (self.home_alt is not None
                                       or self.global_alt is not None):
                self.z_ground = self.z
                self._enter(now_wall, "arming")
                self._send(now_wall, CMD_ARM, 1.0)
            elif held > T_POSITION_S:
                self.give_up(now_wall, f"no local and global position after "
                                       f"{held:.0f}s, so the estimator never "
                                       f"converged")

        elif self.state == "arming":
            if self.armed:
                self._enter(now_wall, "climb")
                self._takeoff(now_wall)
            elif held > T_ARM_S:
                self.give_up(now_wall, f"never armed in {held:.0f}s")
            elif now_wall - self.sent_wall > RESEND_S:
                self._send(now_wall, CMD_ARM, 1.0)

        elif self.state == "climb":
            if self.altitude_m() >= self.hover_alt_m - HOVER_TOLERANCE_M:
                self._enter(now_wall, "hold")
            elif not self.armed and held > 9.0:
                # PX4 disarms ten seconds after an arm that never became a
                # takeoff, under COM_DISARM_PRFLT. Run A of this chunk lost
                # uav_3 to it at 6.95 m of a 50 m climb while the other three
                # flew, so it is a per vehicle flake rather than a bad command.
                # Arm again from the top rather than write the vehicle off on
                # the first one, and give up only when the fleet has spent its
                # attempts.
                if self.cycles < TAKEOFF_CYCLES - 1:
                    self.cycles += 1
                    self._enter(now_wall, "wait_pos")
                else:
                    self.give_up(now_wall,
                                 f"disarmed without leaving the ground on all "
                                 f"{TAKEOFF_CYCLES} arm attempts, which is PX4 "
                                 f"giving up on a takeoff that never started "
                                 f"(COM_DISARM_PRFLT)")
            elif held > T_CLIMB_S:
                self.give_up(now_wall, f"reached {self.altitude_m():.1f} m of "
                                       f"{self.hover_alt_m:.1f} m in {held:.0f}s")
            elif self.altitude_m() > self.climb_mark_m + 1.0:
                self.climb_mark_m = self.altitude_m()
                self.climb_mark_wall = now_wall
            elif ((now_wall - self.climb_mark_wall > STALLED_CLIMB_S
                    or (now_wall - self.sent_wall > 4.0
                        and self.altitude_m() < 0.5))
                    and self.attempts < 2):
                # Runs A, B and C all left uav_3 hovering tens of metres below
                # its layer with a correctly commanded altitude: run C shows
                # home 488.19 m, commanded 538.19 m and the vehicle sitting at
                # 497.14 m, so PX4 stopped climbing rather than being told to.
                # Ask again before writing it off. The command is recomputed
                # against the same datum, so a repeat cannot move the target.
                self.attempts += 1
                self.climb_mark_wall = now_wall
                self._takeoff(now_wall)

    def give_up(self, now_wall, reason):
        self.reason = reason
        self.state = "failed"
        self.entered_wall = now_wall

    def reached_hover(self):
        if self.hover_alt_m is None:
            return None
        return self.max_altitude_m >= self.hover_alt_m - HOVER_TOLERANCE_M

    def prep_done(self):
        """A vehicle with no hover altitude has nothing to prepare.

        Without this the preparation loop waits out its whole deadline for a
        vehicle it was never going to fly, which turns an unflown scenario into
        a five minute pause.
        """
        return self.hover_alt_m is None or self.state in ("hold", "failed")


# ------------------------------------------------------------------ the run
class Harness:
    """One scenario, from bring-up to published record."""

    def __init__(self, repo, scenario, options, runs_dir, run_id):
        self.repo = repo
        self.scenario = scenario
        self.options = options
        self.runs_dir = Path(runs_dir)
        self._spawn_cache = None
        self.run_id = run_id

        # Chunk 2.4. Read and refused here, before anything is launched, so a
        # survey block with a dimension missing costs an argument error and
        # not a bring-up.
        try:
            self.spec = survey_spec(scenario.raw)
        except SurveyError as exc:
            raise HarnessFailure(str(exc), EXIT_ARGS) from exc
        self.scenario_relative = None
        self.nodes = []
        self.metrics_payload = None
        self.metrics_final = False
        self.metrics_messages = 0
        self.metrics_foreign = 0
        self.mission_started_wall = None

        self.launcher = None
        self.bring_up_attempts = 0
        self.clock = None
        self.vehicles = []
        self.sampler = None
        self.injector = None
        self.graph_document = None
        self.sim_now = None
        self.zero_s = None
        self.elapsed_s = 0.0
        self.prep_seconds = 0.0
        self.clock_messages = 0

    # ----------------------------------------------------------- injection
    def _apply_effect(self, kind, target):
        if kind != "kill":
            raise HarnessFailure(
                f"scenario asks for a {kind!r} event on {target}, and chunk 1.7 "
                f"implements only 'kill'. The comms blackout belongs to the "
                f"link layer and the GPS degrade to the vehicle bring-up, and "
                f"neither exists yet. A runner that logged an intention here "
                f"would produce a record of things it did not do.", EXIT_CHILD)
        vehicle = self._vehicle(target)
        pids = px4_instances().get(vehicle.index, [])
        if not pids:
            raise HarnessFailure(
                f"asked to kill {target} and no PX4 process is running for "
                f"instance {vehicle.index}. Whatever removed it, this run did "
                f"not, and a record claiming the injection landed would be "
                f"claiming credit for an accident.", EXIT_CHILD)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as exc:
                raise HarnessFailure(
                    f"could not kill pid {pid} for {target}: {exc}",
                    EXIT_CHILD) from exc

    def _effect_visible(self, kind, target):
        """Look at the target, never at whether the request was sent.

        Two witnesses, and both are about the vehicle rather than about us. The
        process is gone, and its telemetry has stopped. Either alone is weaker:
        a dead process could still have a socket buffer draining, and a silent
        link could be one dropped datagram.
        """
        if kind != "kill":
            return False
        vehicle = self._vehicle(target)
        if px4_instances().get(vehicle.index):
            return False
        silence = vehicle.silent_for(self.sim_now)
        return silence is not None and silence >= KILL_SILENCE_S

    def _vehicle(self, name):
        for vehicle in self.vehicles:
            if vehicle.name == name:
                return vehicle
        raise HarnessFailure(
            f"the scenario names {name!r} and no vehicle by that name was "
            f"brought up", EXIT_CHILD)

    # ------------------------------------------------------------ bring-up
    def bring_up(self, launcher_script):
        """Start the stack, and try again if the bring-up lost a race.

        scripts/sitl_multi.sh ends by asking `ros2 topic list` whether every
        vehicle's uXRCE-DDS namespace is visible, and that check is a race it
        loses perhaps half the time on this machine. Measured on 1 September
        2026 over four bring-ups: twice all four namespaces were registered
        within 25 s and the launcher passed; twice one vehicle was still absent
        at its 15 s settle and the launcher died with "no ROS 2 topics under
        namespace(s): uav_4", once with uav_1 showing 29 of its 43 topics at
        the same moment, which is discovery still in flight rather than a
        vehicle that failed to boot.

        The launcher belongs to chunk 1.2 and is not edited here. A bounded
        retry is the honest way to live with it: each attempt is logged, the
        count goes into the record, and three failures in a row is a broken
        machine rather than a lost race.
        """
        duration = float(self.scenario.duration_s)
        hold_s = PREP_DEADLINE_S + duration + 180.0
        last = ""
        for attempt in range(1, BRING_UP_ATTEMPTS + 1):
            self.bring_up_attempts = attempt
            log_path = Path("/tmp") / f"uavx-launcher-{self.run_id}-{attempt}.log"
            refresh_ros_daemon()
            self.launcher = Launcher(self.repo, launcher_script,
                                     len(self.scenario.vehicles), hold_s,
                                     log_path)
            self.launcher.start()
            if self.launcher.wait_ready(READY_TIMEOUT_S):
                print(f"  launcher reported {len(self.scenario.vehicles)} "
                      f"vehicles up on attempt {attempt}", flush=True)
                return
            last = "\n".join(self.launcher.log_text().splitlines()[-25:])
            print(f"  attempt {attempt} of {BRING_UP_ATTEMPTS} did not come up. "
                  f"Its log is {log_path}:\n{last}", flush=True)
            stop_everything(self.launcher)
            self.launcher = None
        raise HarnessFailure(
            f"the launcher never reported {len(self.scenario.vehicles)} "
            f"vehicles up and healthy in {BRING_UP_ATTEMPTS} attempts. The "
            f"last one ended:\n{last}", EXIT_CHILD)

    def connect(self):
        altitudes = self.scenario.raw.get("hover_altitudes_m") or {}
        if not isinstance(altitudes, dict):
            raise HarnessFailure(
                f"hover_altitudes_m is {altitudes!r} and must be a mapping of "
                f"vehicle id to metres", EXIT_ARGS)
        for index, name in enumerate(self.scenario.vehicles):
            altitude = altitudes.get(name)
            if altitude is not None:
                altitude = float(altitude)
            self.vehicles.append(Vehicle(index, name, altitude))

    def prepare_fleet(self):
        """Fly every vehicle to its hover altitude before scenario time zero.

        architecture.md section 1b describes harness_check as four vehicles
        hovering at their layer altitudes for 60 s, so the climb is setup and
        the 60 s is the hover.

        A vehicle that does not get there is recorded as not getting there and
        the run continues. Failing the whole harness on one slow estimator
        would make the scenario a flight test, and harness_check is explicitly
        never cited as evidence for a rubric row. What it proves is the
        harness, and every vehicle that reports a pose proves its part of that
        whether it climbed or not.
        """
        if all(vehicle.hover_alt_m is None for vehicle in self.vehicles):
            print("  scenario names no hover altitudes, so nothing is flown",
                  flush=True)
            return
        started = time.time()
        deadline = started + PREP_DEADLINE_S
        while time.time() < deadline:
            now_wall = time.time() - started
            for vehicle in self.vehicles:
                vehicle.drain(None)
                vehicle.prepare(now_wall)
            if all(vehicle.prep_done() for vehicle in self.vehicles):
                break
            time.sleep(0.05)
        self.prep_seconds = time.time() - started
        for vehicle in self.vehicles:
            if (vehicle.hover_alt_m is not None and vehicle.state != "hold"
                    and not vehicle.reason):
                vehicle.give_up(self.prep_seconds,
                                f"still {vehicle.state} at the {PREP_DEADLINE_S:.0f}s "
                                f"preparation deadline")
            print(f"  {vehicle.name:6s} {vehicle.state:8s} "
                  f"alt={vehicle.altitude_m():6.1f} m "
                  f"target={vehicle.hover_alt_m} {vehicle.reason}", flush=True)

    # -------------------------------------------------------------- survey
    def _start_node(self, label, command):
        log_path = Path("/tmp") / f"uavx-{label}-{self.run_id}.log"
        try:
            with open(log_path, "w", encoding="utf-8") as handle:
                process = subprocess.Popen(command, stdout=handle,
                                           stderr=subprocess.STDOUT,
                                           start_new_session=True)
        except OSError as exc:
            raise HarnessFailure(f"could not start {label}: {exc}",
                                 EXIT_DEPENDENCY) from exc
        self.nodes.append({"label": label, "process": process, "log": log_path})
        return process

    def _node_tail(self, node, lines=15):
        try:
            text = node["log"].read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def _check_nodes_alive(self):
        for node in self.nodes:
            code = node["process"].poll()
            if code is not None:
                raise HarnessFailure(
                    f"{node['label']} exited with {code} during the run. A "
                    f"survey flown by three executors is not the survey the "
                    f"scenario describes. Its log is {node['log']}:\n"
                    f"{self._node_tail(node)}", EXIT_CHILD)

    def _signal_nodes(self, sig, labels=None):
        for node in self.nodes:
            if labels is not None and node["label"] not in labels:
                continue
            process = node["process"]
            if process.poll() is not None:
                continue
            try:
                os.killpg(os.getpgid(process.pid), sig)
            except (OSError, ProcessLookupError):
                pass

    def stop_nodes(self, collect_final):
        """The executors first, then the collector, whose last word counts.

        The collector publishes a final payload on the way down, and the
        record is built from whichever payload arrived last. Waiting for the
        final one is bounded, because a collector that cannot publish during
        its own shutdown still published a partial a second earlier and that
        partial describes the same run to within one publish period.
        """
        if not self.nodes:
            return
        executors = [n["label"] for n in self.nodes if n["label"] != COLLECTOR_LABEL]
        self._signal_nodes(signal.SIGINT, executors)
        self._signal_nodes(signal.SIGINT, [COLLECTOR_LABEL])
        if collect_final and self.clock is not None:
            deadline = time.time() + METRICS_FINAL_WAIT_S
            while not self.metrics_final and time.time() < deadline:
                self.clock.spin(0.1)
        for node in self.nodes:
            process = node["process"]
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._signal_nodes(signal.SIGKILL, [node["label"]])
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.nodes = []

    def _on_metrics(self, message):
        if message.run_id != self.run_id:
            self.metrics_foreign += 1
            return
        try:
            payload = json.loads(message.json_payload)
        except ValueError:
            self.metrics_foreign += 1
            return
        if not isinstance(payload, dict):
            self.metrics_foreign += 1
            return
        self.metrics_messages += 1
        self.metrics_payload = payload
        if message.final:
            self.metrics_final = True

    def _set_fleet_param(self, vehicles, started, name, value):
        """Set one PX4 parameter on every flying vehicle and wait for the echo."""
        pending = list(vehicles)
        last_sent = {}
        deadline = time.time() + PARAM_DEADLINE_S
        while pending and time.time() < deadline:
            now_wall = time.time() - started
            for vehicle in self.vehicles:
                vehicle.drain(None)
            for vehicle in list(pending):
                got = vehicle.params.get(name)
                if got is not None and abs(got - float(value)) < 1e-3:
                    vehicle.cruise_param_confirmed = True
                    pending.remove(vehicle)
                    continue
                if now_wall - last_sent.get(vehicle.name, -99.0) > RESEND_S:
                    vehicle.set_param(now_wall, name, value)
                    last_sent[vehicle.name] = now_wall
            time.sleep(0.05)
        for vehicle in pending:
            vehicle.cruise_param_confirmed = False
        if pending:
            names = ", ".join(v.name for v in pending)
            raise HarnessFailure(
                f"{name}={value} was never echoed back by {names} in "
                f"{PARAM_DEADLINE_S:.0f}s. The cruise speed is a frozen number "
                f"and a run that did not apply it would be flying somebody "
                f"else's.", EXIT_CHILD)
        print(f"  {name}={value} confirmed on {len(vehicles)} vehicle(s)",
              flush=True)

    def start_mission(self):
        """Put the executors and the collector up and the fleet in offboard.

        Wall time, like the climb: this is setup for the scenario and none of
        it reaches a `_s` field. Scenario time zero is when fly_scenario
        starts, with every vehicle that could be put into offboard already
        flying its first waypoint.
        """
        if self.spec is None:
            return
        manifest = self._load_spawn()
        try:
            entries = model_map(manifest)
        except SurveyError as exc:
            raise HarnessFailure(
                f"a survey needs the launcher's spawn manifest: {exc}",
                EXIT_CHILD) from exc
        if not self.scenario_relative:
            raise HarnessFailure("the runner did not record the scenario's "
                                 "repository relative path before the survey",
                                 EXIT_ARTIFACT)
        floor = frozen_min_separation(self.repo)
        started = time.time()

        self._start_node(COLLECTOR_LABEL, collector_command(
            self.run_id, self.scenario_relative, self.spec, entries, floor))
        flyers = [v for v in self.vehicles if v.state == "hold"]
        for vehicle in flyers:
            try:
                command = mission_node_command(
                    vehicle.name, self.spawn_of(vehicle.name),
                    vehicle.hover_alt_m, self.spec, list(self.scenario.vehicles))
            except SurveyError as exc:
                raise HarnessFailure(str(exc), EXIT_CHILD) from exc
            self._start_node(f"mission-{vehicle.name}", command)
        print(f"  started the collector and {len(flyers)} mission "
              f"executor(s) for {self.spec.cell_count} cells", flush=True)

        settle_until = time.time() + NODE_SETTLE_S
        while time.time() < settle_until:
            for vehicle in self.vehicles:
                vehicle.drain(None)
            time.sleep(0.05)
        self._check_nodes_alive()

        self._set_fleet_param(flyers, started, SIM_BATTERY_FLOOR_PARAM,
                              SIM_BATTERY_FLOOR_PCT)
        if self.spec.cruise_speed_mps is not None:
            self._set_fleet_param(flyers, started, CRUISE_SPEED_PARAM,
                                  self.spec.cruise_speed_mps)

        pending = list(flyers)
        deadline = time.time() + OFFBOARD_DEADLINE_S
        while pending and time.time() < deadline:
            now_wall = time.time() - started
            for vehicle in self.vehicles:
                vehicle.drain(None)
            for vehicle in list(pending):
                if vehicle.in_offboard:
                    vehicle.offboard_wall = round(now_wall, 1)
                    vehicle.surveying = True
                    pending.remove(vehicle)
                elif (vehicle.offboard_requests == 0
                      or now_wall - vehicle.sent_wall > RESEND_S):
                    vehicle.request_offboard(now_wall)
            time.sleep(0.05)
        for vehicle in pending:
            vehicle.offboard_reason = (
                f"still in {vehicle.mode_name()} after "
                f"{vehicle.offboard_requests} offboard requests over "
                f"{OFFBOARD_DEADLINE_S:.0f}s")
        for vehicle in flyers:
            detail = (f"offboard granted at {vehicle.offboard_wall}s"
                      if vehicle.surveying else vehicle.offboard_reason)
            print(f"  {vehicle.name:6s} {vehicle.mode_name():10s} {detail}",
                  flush=True)
        self._check_nodes_alive()
        self.mission_started_wall = round(time.time() - started, 1)
        if pending:
            # Not fatal on its own. The record names which vehicles surveyed
            # and the coverage figure says what it cost, so a scenario that
            # needed all four fails on its numbers rather than on a guess.
            print(f"  WARNING  {len(pending)} vehicle(s) never entered "
                  f"offboard and will hover for the run", flush=True)

    # ---------------------------------------------------------------- loop
    def fly_scenario(self):
        duration = float(self.scenario.duration_s)
        self.clock = SimClock()
        first = self.clock.wait_for_time(CLOCK_WAIT_S)
        if first is None:
            raise HarnessFailure(
                f"no /clock message in {CLOCK_WAIT_S:.0f}s. Simulated time is "
                f"what every `_s` field in the record is measured in, so a run "
                f"without it has nothing to write down.", EXIT_CHILD)

        if self.spec is not None:
            self.clock.subscribe_metrics(self._on_metrics)

        self.injector = EventInjector(
            [{"type": event.type, "target": event.target, "at_s": event.at_s}
             for event in self.scenario.injected_events],
            self._apply_effect, self._effect_visible)

        self.sampler = ResourceSampler(root_pid=os.getpid())
        self.zero_s = first
        self.sim_now = 0.0
        next_pose = 0.0
        next_resource = 0.0
        capture_at = duration * GRAPH_CAPTURE_FRACTION
        wall_start = time.time()
        wall_limit = max(600.0, 6.0 * duration)
        last_advance_wall = wall_start
        last_sim = 0.0

        while True:
            self.clock.spin(0.02)
            absolute = self.clock.now()
            if absolute is None:
                raise HarnessFailure("the /clock feed stopped", EXIT_TIMEOUT)
            self.sim_now = absolute - self.zero_s
            self.elapsed_s = self.sim_now

            for vehicle in self.vehicles:
                vehicle.drain(self.sim_now)
                if not vehicle.stream_requested:
                    vehicle.request_pose_stream()

            if self.sim_now >= next_pose:
                next_pose = self.sim_now + POSE_PERIOD_S
                for vehicle in self.vehicles:
                    vehicle.take_sample(self.sim_now)

            self.injector.tick(self.sim_now)
            self.injector.poll_observations(self.sim_now)

            if self.sim_now >= next_resource:
                next_resource = self.sim_now + RESOURCE_PERIOD_S
                self.sampler.add_pids(self._group_pids())
                self.sampler.sample(max(self.sim_now, 0.0))
                self._check_nodes_alive()

            if self.graph_document is None and self.sim_now >= capture_at:
                self.capture_graph()

            now_wall = time.time()
            if self.sim_now > last_sim + 1e-6:
                last_sim = self.sim_now
                last_advance_wall = now_wall
            elif now_wall - last_advance_wall > CLOCK_STALL_S:
                raise HarnessFailure(
                    f"simulated time has not advanced for {CLOCK_STALL_S:.0f}s "
                    f"of wall time. The simulation is lockstep, so this is a "
                    f"wedged process rather than a slow run.", EXIT_TIMEOUT)
            if now_wall - wall_start > wall_limit:
                raise HarnessFailure(
                    f"the {duration:.0f}s scenario has run {wall_limit:.0f}s of "
                    f"wall time and reached {self.sim_now:.1f}s of simulated "
                    f"time", EXIT_TIMEOUT)
            if self.sim_now >= duration:
                break

        # The survey's processes come down while the clock is still running,
        # so the collector's last payload can arrive. Everything else is torn
        # down by the caller after this returns.
        self.stop_nodes(collect_final=True)
        self.clock_messages = self.clock.messages
        if self.graph_document is None:
            raise HarnessFailure(
                "the scenario finished and no ROS graph was captured, so the "
                "seam pass would have nothing belonging to this run to check",
                EXIT_ARTIFACT)

    def _group_pids(self):
        """The processes the run is responsible for, by name.

        sitl_multi.sh starts each PX4 inside a subshell that then exits, so the
        instances are reparented and are not descendants of this process. The
        sampler exists to be told about exactly that case.
        """
        table = _process_table()
        pids = _pids_named({"px4", "gzserver", "MicroXRCEAgent", "gzclient"},
                           table)
        if self.launcher is not None and self.launcher.process is not None:
            pids.append(self.launcher.process.pid)
        for node in self.nodes:
            pids.append(node["process"].pid)
        return pids

    def capture_graph(self):
        """Read the live ROS graph while the scenario is still up.

        Captured here rather than after teardown on purpose: a graph read once
        the simulator has gone is a graph of an empty machine, and an empty
        capture and a clean swarm write the same file.
        """
        source_sha = run_record.source_tree_sha256(self.repo)
        try:
            self.graph_document = capture_snapshot(
                self.run_id, self.scenario.name,
                source_tree_sha256=source_sha, captured_at=utc_stamp())
        except CaptureFailed as exc:
            raise HarnessFailure(f"the ROS graph could not be read: {exc}",
                                 EXIT_CHILD) from exc
        except IncompleteSnapshot as exc:
            raise HarnessFailure(f"the ROS graph capture has a hole in it: {exc}",
                                 EXIT_ARTIFACT) from exc
        nodes = [name for name in self.graph_document if name != "_meta"]
        print(f"  graph captured at t={self.sim_now:.1f}s: "
              f"{', '.join(sorted(nodes))}", flush=True)

    # ------------------------------------------------------------- results
    def observed_vehicles(self):
        """Scenario order, and only the vehicles that actually reported.

        Order matters: the gate compares this list as its comma joined form.
        A vehicle killed halfway through still belongs here, because it was
        observed for the half of the run it was alive for.
        """
        return [vehicle.name for vehicle in self.vehicles if vehicle.samples > 0]

    def spawn_of(self, name):
        """Where the launcher put this vehicle, or None if it did not say.

        Absent is a real answer and the record says so rather than dropping the
        field. A stack brought up by something other than scripts/sitl_multi.sh
        leaves no manifest, and a silently missing offset is how a later week
        ends up converting poses against an origin it guessed.
        """
        for row in self._load_spawn().get("vehicles", []):
            if row.get("vehicle_id") == name:
                return row
        return None

    def _load_spawn(self):
        if self._spawn_cache is None:
            try:
                self._spawn_cache = json.loads(
                    (self.runs_dir / ".launcher-spawn.json")
                    .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._spawn_cache = {}
            if not isinstance(self._spawn_cache, dict):
                self._spawn_cache = {}
        return self._spawn_cache

    @staticmethod
    def _vehicle_metrics(vehicle, spawn):
        """What one vehicle did, in enough detail to argue with.

        The altitude the takeoff was commanded against is in here beside the
        altitude reached. Runs A and B both left uav_3 short of its 50 m layer
        while the other three made theirs, and a record carrying only "did not
        reach" cannot tell a slow estimator from a takeoff commanded against
        the wrong datum.
        """
        return {
            # Audit finding 11. Every other position in this block is in the
            # vehicle's own local frame, whose origin is where it was spawned.
            # These two are what turn that into the single frame the design
            # freezes. Read from the launcher's manifest rather than recomputed,
            # so the formula has one home.
            "spawn_x_m": spawn["x_m"] if spawn else None,
            "spawn_y_m": spawn["y_m"] if spawn else None,
            "pose_samples": vehicle.samples,
            "pose_rate_hz": vehicle.achieved_pose_hz(),
            "first_sample_s": vehicle.first_sample_s,
            "last_sample_s": vehicle.last_sample_s,
            "mavlink_messages": vehicle.messages,
            "max_altitude_m": round(vehicle.max_altitude_m, 2),
            "hover_target_m": vehicle.hover_alt_m,
            "hover_reached": vehicle.reached_hover(),
            "takeoff_base_alt_m": vehicle.takeoff_base_alt_m,
            "commanded_alt_m": vehicle.commanded_alt_m,
            "home_alt_m": vehicle.home_alt,
            "global_alt_m": vehicle.global_alt,
            "z_at_arm_m": vehicle.z_ground,
            "final_z_m": vehicle.z,
            "final_landed_state": vehicle.landed_state,
            "preparation_state": vehicle.state,
            "takeoff_cycles": vehicle.cycles + 1,
            "preparation_reason": vehicle.reason,
            # Chunk 2.4. Whether this vehicle flew the survey, decided by
            # its own HEARTBEAT reporting offboard, and what PX4 said about
            # the cruise speed. The mode at the end is recorded separately
            # because PX4 leaves offboard the moment the setpoints stop.
            "surveying": vehicle.surveying,
            "offboard_granted_wall_s": vehicle.offboard_wall,
            "offboard_requests": vehicle.offboard_requests,
            "offboard_reason": vehicle.offboard_reason,
            "cruise_param_confirmed": vehicle.cruise_param_confirmed,
            "main_mode_at_end": vehicle.mode_name(),
        }

    def metrics(self):
        return {
            "harness": "uavx_sim.scenario_runner",
            "launcher": "scripts/sitl_multi.sh",
            "clock_topic": "/clock",
            "clock_messages": self.clock_messages,
            "pose_source": "PX4 LOCAL_POSITION_NED over the MAVLink API link",
            "pose_rate_hz_target": POSE_HZ,
            "pose_stream_requested_hz": POSE_STREAM_HZ,
            "control_path": f"pymavlink COMMAND_LONG on udp "
                            f"{MAVLINK_BASE_PORT}+instance",
            "preparation_seconds_wall": round(self.prep_seconds, 1),
            "bring_up_attempts": self.bring_up_attempts,
            "vehicles_requested": len(self.scenario.vehicles),
            "by_vehicle": {v.name: self._vehicle_metrics(v, self.spawn_of(v.name))
                           for v in self.vehicles},
            "app_packets_note": (
                "empty on purpose. Nothing counts application packets until "
                "the link layer exists in week 3. A surveying run's mission "
                "executors publish observations on their own tx endpoints and "
                "no process carries them anywhere, so a row of zeros would "
                "claim four senders whose packets were never delivered by "
                "anything."),
            "resource_probe": getattr(self.sampler, "probe_source", None),
            "survey": self.spec.as_record() if self.spec is not None else None,
            "mission_started_wall_s": self.mission_started_wall,
            "metrics_topic": METRICS_TOPIC if self.spec is not None else None,
            "metrics_messages": self.metrics_messages,
            "metrics_final_received": self.metrics_final,
            "metrics_foreign_messages": self.metrics_foreign,
            "ground_truth": self.metrics_payload,
        }

    def close(self):
        # The survey's processes first, in case the run died before the loop
        # brought them down. A mission executor left streaming setpoints at a
        # PX4 that is about to be killed is harmless; one left after the next
        # gate's preflight is not.
        try:
            self.stop_nodes(collect_final=False)
        except Exception:
            pass
        for vehicle in self.vehicles:
            try:
                vehicle.connection.close()
            except Exception:
                pass
        if self.clock is not None:
            self.clock.close()
            self.clock = None


# -------------------------------------------------------------------- entry
def run(options):
    repo = find_repo()

    # The scenario is read and checked before the machine is, so a path that
    # does not exist costs a file test rather than a dependency scan and takes
    # its documented code of 10 either way.
    scenario_path = Path(options["scenario"])
    if not scenario_path.is_absolute():
        candidate = repo / scenario_path
        scenario_path = candidate if candidate.is_file() else scenario_path
    if not scenario_path.is_file():
        raise ArgumentError(f"no scenario at {options['scenario']}")
    try:
        scenario = load_scenario(scenario_path)
    except ScenarioError as exc:
        raise ArgumentError(str(exc)) from exc

    if options["record"]:
        seconds = options["record_seconds"]
        if seconds > float(scenario.duration_s):
            raise ArgumentError(
                f"--record-seconds {seconds} is longer than the scenario's "
                f"{scenario.duration_s}s. A capture cannot outlast the run it "
                f"is a capture of.")
        # Fail here rather than three minutes into a run that cannot be
        # recorded. There is no headless video path on this stack yet: the
        # world scripts/sitl_multi.sh loads carries no camera sensor, and
        # gzclient is never launched because the GUI binary takes this WSL
        # distribution down. Chunk 3.6 owns the recording rehearsal and this is
        # the message it needs to start from.
        raise HarnessFailure(
            "--record is not supported on this stack. gzserver runs headless "
            "with no camera sensor in the world, so there is no frame source, "
            "and gzclient must never be launched. Building the offscreen "
            "capture path belongs to the recording rehearsal in chunk 3.6.",
            EXIT_RECORDING)

    launcher_script = require_dependencies(repo)

    runs_dir = Path(options["runs_dir"]) if options["runs_dir"] else repo / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    if options["run_id"]:
        run_id = run_record.check_run_id(options["run_id"])
        if run_record.record_path(runs_dir, run_id).exists():
            raise ArgumentError(
                f"run id {run_id} already has a record in {runs_dir}. Ids are "
                f"unique within the runs directory, and overwriting one would "
                f"lose the run it belongs to.")
    else:
        # Minted before the run, never after it. scripts/rehearse_recording.sh
        # burns the id into every frame and then requires the record to carry
        # it back, so an id invented at the end could not have been in shot.
        run_id = run_record.mint_run_id(scenario.name, runs_dir)

    # Only now, after every argument has been checked. An invalid launch must
    # leave the runs directory exactly as it found it.
    run_record.invalidate_latest(runs_dir)

    versions = run_record.read_versions(
        repo / "stage-1" / "setup" / "versions.lock")
    commit = run_record.commit_sha(repo)
    scenario_sha = run_record.sha256_of_file(scenario_path)
    try:
        relative = scenario_path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        raise HarnessFailure(
            f"{scenario_path} is outside {repo}. A record cites its scenario by "
            f"repository relative path and there is none for this file.",
            EXIT_ARGS) from None

    print(f"  run {run_id}: {relative}, seed {scenario.seed}, "
          f"{scenario.duration_s}s, {len(scenario.vehicles)} vehicles",
          flush=True)

    harness = Harness(repo, scenario, options, runs_dir, run_id)
    harness.scenario_relative = relative
    started_at = run_record.utc_stamp()
    try:
        harness.bring_up(launcher_script)
        harness.connect()
        harness.prepare_fleet()
        harness.start_mission()
        harness.fly_scenario()
    finally:
        # Before the verdict and before any file is written. A machine left
        # holding a gzserver is a worse outcome than an unwritten record, and
        # the next gate would inherit it.
        harness.close()
        stop_everything(harness.launcher)

    left = still_running()
    if left:
        raise HarnessFailure(
            f"teardown left {left} running. Nothing may survive this run.",
            EXIT_CHILD)
    print("  nothing named px4, gzserver or gzclient is left running",
          flush=True)

    ended_at = run_record.utc_stamp()

    # The graph first, because its sha256 goes into the record and the seam
    # pass recomputes that hash from the file it is handed.
    graph_file = run_record.graph_path(runs_dir, run_id)
    try:
        write_snapshot(graph_file, harness.graph_document)
    except IncompleteSnapshot as exc:
        raise HarnessFailure(f"the captured graph is not publishable: {exc}",
                             EXIT_ARTIFACT) from exc
    graph_sha = sha256_of(graph_file)

    try:
        resources = harness.sampler.summary()
    except ResourceSamplerError as exc:
        raise HarnessFailure(f"the resource sampler cannot answer: {exc}",
                             EXIT_ARTIFACT) from exc

    # Chunk 2.4. Coverage comes off the collector's payload and nowhere else,
    # and a surveying run without one has no coverage to record rather than
    # a coverage of zero.
    coverage = None
    if harness.spec is not None:
        if harness.metrics_payload is None:
            raise HarnessFailure(
                f"the scenario surveys and the collector never published a "
                f"payload for run {run_id} on {METRICS_TOPIC}, so there is no "
                f"coverage figure to record.", EXIT_ARTIFACT)
        try:
            coverage = coverage_from_payload(harness.metrics_payload)
        except SurveyError as exc:
            raise HarnessFailure(f"the collector's payload does not hold up: "
                                 f"{exc}", EXIT_ARTIFACT) from exc

    try:
        record = run_record.build_record(
            run_id=run_id,
            scenario_path=relative,
            scenario_sha256=scenario_sha,
            seed=scenario.seed,
            commit_sha=commit,
            started_at=started_at,
            ended_at=ended_at,
            completion="complete",
            vehicle_ids_observed=harness.observed_vehicles(),
            pose_sample_count=sum(v.samples for v in harness.vehicles),
            versions=versions,
            metrics=harness.metrics(),
            # No swarm node exists in week 1. See metrics.app_packets_note.
            app_packets_sent_by_node={},
            app_packets_delivered_by_node={},
            injected_events=harness.injector.records(),
            requested_duration_s=float(scenario.duration_s),
            elapsed_sim_s=round(harness.elapsed_s, 3),
            clock_source=run_record.CLOCK_SOURCE,
            # Taken from the snapshot rather than measured a second time.
            # seam_graph.bind_to_run refuses a snapshot whose source hash
            # differs from the record's, and hashing the tree twice around a
            # three minute run is two chances to disagree.
            source_tree_sha256=harness.graph_document["_meta"]["source_tree_sha256"],
            resources=resources,
            injected_event_observed=harness.injector.all_observed(),
            injected_event_count=harness.injector.count_observed(),
            graph_snapshot_sha256=graph_sha,
            coverage=coverage,
        )
    except run_record.RecordError as exc:
        raise HarnessFailure(f"the run record does not hold up: {exc}",
                             EXIT_ARTIFACT) from exc

    unobserved = harness.injector.unobserved()
    if unobserved:
        # Named rather than merely absent from a count. An event that did not
        # land is the difference between a recovery time and a number.
        for row in unobserved:
            print(f"  WARNING  {row['type']} on {row['target']} requested at "
                  f"t={row['requested_t']} was never observed to take effect",
                  flush=True)

    run_record.write_record(run_record.record_path(runs_dir, run_id), record)
    # Both latest files last, and only now that every process has exited and
    # the record has validated.
    write_snapshot(runs_dir / run_record.LATEST_GRAPH, harness.graph_document)
    run_record.publish_latest(runs_dir, record)

    print(f"  ok    {run_id}: {record['pose_sample_count']} pose samples from "
          f"{','.join(record['vehicle_ids_observed'])}, "
          f"{record['injected_event_count']} injected event(s) observed, "
          f"peak {resources['peak_rss_mib']} MiB, "
          f"{resources['samples']} resource samples", flush=True)
    if coverage is not None:
        print(f"  ok    coverage {coverage['coverage_fraction']:.4f} from "
              f"{coverage['coverage_source']}, "
              f"{coverage['coverage_cells_seen']} of "
              f"{coverage['coverage_cells_total']} cells", flush=True)
    return EXIT_OK


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        options = parse_args(argv)
    except HarnessFailure as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        return exc.code
    try:
        return run(options)
    except HarnessFailure as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        return exc.code
    except run_record.RecordError as exc:
        print(f"  FAIL  {exc}", file=sys.stderr)
        return EXIT_ARTIFACT
    except KeyboardInterrupt:
        print("  FAIL  interrupted", file=sys.stderr)
        stop_everything()
        return EXIT_CHILD


if __name__ == "__main__":
    sys.exit(main())
