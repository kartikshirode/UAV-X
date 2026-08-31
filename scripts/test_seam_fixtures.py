#!/usr/bin/env python3
"""Prove the seam checker catches the bypasses it claims to, and says why.

Round 3 finding 2 asked for fixtures. Round 4 finding 4 found that the fixtures
themselves had the defect they were written to prevent. On a tree with no
uavx_ws/src the shell wrapper died before reading any snapshot, so every fixture
exited non-zero: the clean case was reported as a failure, and all eight bypass
cases were reported as successfully caught. The suite exited 1 while printing
eight ticks. Nothing checked WHY a fixture failed, so a setup error and a
correctly detected bypass were the same observation.

So each fixture now states the text it expects, and a fixture that fails for a
reason other than its own is a failure of the suite.

    python3 scripts/test_seam_fixtures.py

Exit 0 if every fixture behaved as specified.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEAM_GRAPH = REPO / "scripts" / "seam_graph.py"
CHECK_SEAM = REPO / "scripts" / "check_seam.sh"

VEHICLES = ["uav_1", "uav_2", "uav_3", "uav_4"]
PACKET = "uavx_msgs/msg/SwarmPacket"
POSE = "px4_msgs/msg/VehicleLocalPosition"
LOG = "rcl_interfaces/msg/Log"
PARAM = "rcl_interfaces/msg/ParameterEvent"
TRUTH = "gazebo_msgs/msg/ModelStates"
SETPOINT = "px4_msgs/msg/TrajectorySetpoint"
METRICS = "uavx_msgs/msg/RunMetrics"

CASES: list = []
STATIC_CASES: list = []


def ep(topic: str, typ: str) -> dict:
    return {"topic": topic, "type": typ}


def add_node(g: dict, node: str, spec: dict) -> dict:
    """Add a node the way a capture would, command entry included.

    Round 6 finding 6 requires every node in a snapshot to have a `ros2 node
    info` result behind it. A fixture that adds a bare node would now fail on
    the missing command rather than on the bypass it was written to prove,
    which would quietly stop testing the bypass.
    """
    g[node] = spec
    g["_meta"]["commands"][node] = {"cmd": f"ros2 node info {node}",
                                    "returncode": 0}
    return g


def clean_graph(week: int) -> dict:
    """The graph a correct implementation produces.

    W3 has no role managers because uavx_roles does not exist yet. That is the
    whole point of per-scenario manifests.

    Every node carries the endpoints its manifest row requires. Round 5 finding
    7: the previous version of this helper happened to include them, so nothing
    noticed that the checker never looked.
    """
    procs = ["router", "mission_executor"]
    if week >= 4:
        procs.append("role_manager")

    g = {"_meta": {
        "captured_at": "2026-09-20T10:00:00",
        "scenario": "mission_integrated" if week >= 4 else "relay_required",
        "run_id": "fixture_run",
        "source_tree_sha256": "0" * 64,
        # Round 6 finding 6: commands was optional and an empty object passed,
        # so a capture that never reached a running graph wrote the same file
        # as a clean swarm. run_graph fills one entry per node below.
        "commands": {"node_list": {"returncode": 0}},
    }}
    for v in VEHICLES:
        for proc in procs:
            node = f"/{v}/{proc}"
            pubs = [ep(f"/uavx/{v}/tx", PACKET), ep("/rosout", LOG)]
            subs = [ep(f"/uavx/{v}/rx", PACKET),
                    ep(f"/{v}/fmu/out/vehicle_local_position", POSE),
                    ep("/parameter_events", PARAM)]
            if proc == "mission_executor":
                # It has to be able to fly the aircraft.
                pubs.append(ep(f"/{v}/fmu/in/trajectory_setpoint", SETPOINT))
            g[node] = {
                "publishers": pubs, "subscribers": subs,
                "services": [ep(f"{node}/get_parameters",
                                "rcl_interfaces/srv/GetParameters")],
                "actions": [],
            }
    g["/gcs/gcs_node"] = {
        "publishers": [ep("/uavx/gcs/tx", PACKET), ep("/rosout", LOG)],
        "subscribers": [ep("/uavx/gcs/rx", PACKET)],
        "services": [ep("/gcs/gcs_node/list_parameters",
                        "rcl_interfaces/srv/ListParameters")],
        "actions": [],
    }
    # Outside the swarm. The radio and the observer.
    g["/link_layer"] = {
        "publishers": [ep(f"/uavx/{v}/rx", PACKET) for v in VEHICLES]
        + [ep("/uavx/gcs/rx", PACKET)],
        "subscribers": [ep(f"/uavx/{v}/tx", PACKET) for v in VEHICLES]
        + [ep("/uavx/gcs/tx", PACKET), ep("/gazebo/model_states", TRUTH)],
        "services": [], "actions": [],
    }
    g["/metrics_collector"] = {
        "publishers": [ep("/uavx_eval/metrics", METRICS)],
        "subscribers": [ep("/gazebo/model_states", TRUTH)],
        "services": [], "actions": [],
    }
    # One capture command per node, which is what a real capture writes and
    # what round 6 finding 6 now requires. A case that adds a node has to add
    # its command too, and that is the point: a node in the graph with nothing
    # saying how it was read is a node somebody typed.
    for node in list(g):
        if node != "_meta":
            g["_meta"]["commands"][node] = {"cmd": f"ros2 node info {node}",
                                            "returncode": 0}
    return g


def case(name: str, scenario: str, week: int, expect: str | None):
    """expect None means the fixture must pass; otherwise the text it must say."""
    def deco(fn):
        CASES.append((name, scenario, week, expect, fn))
        return fn
    return deco


# ------------------------------------------------------------- clean graphs
@case("clean W3 graph, no role managers yet", "relay_required", 3, None)
def _c3(g):
    return g


@case("clean W4 graph, role managers present", "mission_integrated", 4, None)
def _c4(g):
    return g


@case("W4 processes running under a W3 scenario", "relay_required", 4,
      "in neither this scenario's manifest")
def _w4_in_w3(g):
    # The graph is a W4 one. It claims to be the relay_required capture,
    # because the bypass under test is W4 processes appearing in a W3 run, not
    # a mislabelled snapshot. That is its own case now.
    g["_meta"]["scenario"] = "relay_required"
    return g


@case("expected process missing", "mission_integrated", 4,
      "is absent from the graph")
def _missing(g):
    del g["/gcs/gcs_node"]
    return g


# ------------------------------------------------------------------ bypasses
@case("reads another vehicle's rx", "relay_required", 3, "which belongs to uav_4")
def _other_rx(g):
    g["/uav_3/router"]["subscribers"].append(ep("/uavx/uav_4/rx", PACKET))
    return g


@case("shared /uavx broadcast topic", "relay_required", 3, "shared endpoint")
def _shared_uavx(g):
    g["/uav_3/router"]["publishers"].append(ep("/uavx/broadcast", PACKET))
    return g


@case("shared topic outside /uavx entirely", "relay_required", 3,
      "on no allowlist row")
def _shared_swarm(g):
    g["/uav_3/router"]["publishers"].append(ep("/swarm/broadcast",
                                               "std_msgs/msg/String"))
    return g


@case("publishes to its own rx", "relay_required", 3, "publish to tx only")
def _wrong_direction(g):
    g["/uav_2/router"]["publishers"].append(ep("/uavx/uav_2/rx", PACKET))
    return g


@case("reads another vehicle's PX4 namespace", "relay_required", 3,
      "the namespace of uav_4")
def _other_px4(g):
    g["/uav_1/mission_executor"]["subscribers"].append(
        ep("/uav_4/fmu/out/vehicle_local_position", POSE))
    return g


@case("service between swarm nodes", "mission_integrated", 4, "exposes service")
def _service(g):
    g["/uav_2/role_manager"]["services"].append(
        ep("/uavx/uav_2/elect", "uavx_msgs/srv/Elect"))
    return g


@case("swarm node reads simulator ground truth", "relay_required", 3,
      "simulator ground truth")
def _ground_truth(g):
    g["/uav_4/mission_executor"]["subscribers"].append(
        ep("/gazebo/model_states", TRUTH))
    return g


@case("SwarmPacket on an innocent-looking topic", "relay_required", 3,
      "which is not its tx or rx")
def _packet_elsewhere(g):
    g["/uav_1/router"]["publishers"].append(ep("/uav_1/telemetry", PACKET))
    return g


@case("unknown helper publishing to its own tx", "relay_required", 3,
      "in neither this scenario's manifest")
def _unknown(g):
    return add_node(g, "/uav_2/sneaky_helper", {
        "publishers": [ep("/uavx/uav_2/tx", PACKET)],
        "subscribers": [], "services": [], "actions": [],
    })


@case("node name merely containing an outside-process name", "relay_required", 3,
      "in neither this scenario's manifest")
def _substring_exemption(g):
    return add_node(g, "/uav_2/link_layer_helper", {
        "publishers": [],
        "subscribers": [ep("/gazebo/model_states", TRUTH)],
        "services": [], "actions": [],
    })


@case("link layer publishing into a tx", "relay_required", 3,
      "publishing to tx would let it invent traffic")
def _link_layer_injects(g):
    g["/link_layer"]["publishers"].append(ep("/uavx/uav_1/tx", PACKET))
    return g


@case("scenario runner carrying swarm traffic", "mission_integrated", 4,
      "never by sending swarm traffic")
def _runner_traffic(g):
    return add_node(g, "/scenario_runner", {
        "publishers": [ep("/uavx/uav_3/rx", PACKET)],
        "subscribers": [], "services": [], "actions": [],
    })


@case("every expected node present with no endpoints at all", "mission_integrated", 4,
      "never opened it")
def _all_empty(g):
    for node in list(g):
        if node == "_meta":
            continue
        g[node] = {"publishers": [], "subscribers": [], "services": [],
                   "actions": []}
    return g


@case("one mandatory endpoint missing", "mission_integrated", 4,
      "holds no publisher matching /uavx/uav_3/tx")
def _missing_endpoint(g):
    g["/uav_3/router"]["publishers"] = [ep("/rosout", LOG)]
    return g


@case("the mission executor cannot command its aircraft", "mission_integrated", 4,
      "holds no publisher matching /uav_2/fmu/in/*")
def _no_setpoints(g):
    g["/uav_2/mission_executor"]["publishers"] = [
        ep("/uavx/uav_2/tx", PACKET), ep("/rosout", LOG)]
    return g


@case("the link layer never ran", "mission_integrated", 4,
      "/link_layer is absent")
def _no_link_layer(g):
    del g["/link_layer"]
    return g


@case("a snapshot with no provenance", "mission_integrated", 4,
      "CANNOT:no _meta block")
def _no_meta(g):
    del g["_meta"]
    return g


# Round 6 finding 6. Every one of these produced a passing seam check before.
@case("a capture that never ran ros2 node list", "mission_integrated", 4,
      "CANNOT:no successful `ros2 node list`")
def _no_listing(g):
    g["_meta"]["commands"] = {}
    return g


@case("a node in the graph with no capture command behind it",
      "mission_integrated", 4, "CANNOT:records no `ros2 node info` result")
def _partial_commands(g):
    del g["_meta"]["commands"]["/uav_1/router"]
    return g


@case("a command entry with no return code at all", "mission_integrated", 4,
      "CANNOT:records no `ros2 node info` result")
def _no_returncode(g):
    g["_meta"]["commands"]["/uav_1/router"] = {"cmd": "ros2 node info"}
    return g


@case("a node info call that reported failure", "mission_integrated", 4,
      "CANNOT:exiting 1")
def _failed_node_info(g):
    g["_meta"]["commands"]["/uav_1/router"]["returncode"] = 1
    return g


@case("the previous scenario's graph left behind", "mission_integrated", 4,
      "CANNOT:says nothing about this one")
def _wrong_scenario(g):
    g["_meta"]["scenario"] = "relay_required"
    return g


# Round 7 finding 6, reproduced by the reviewer and confirmed here: both of
# these returned "respect the seam" on a graph carrying a swarm side channel.
@case("the observer carrying swarm payload on a side topic",
      "mission_integrated", 4, "not in its allowlist")
def _observer_sidechannel(g):
    g["/metrics_collector"]["publishers"].append(
        ep("/swarm/sidechannel", PACKET))
    return g


@case("the radio reading swarm payload off a side topic",
      "mission_integrated", 4, "not in its allowlist")
def _radio_sidechannel(g):
    g["/link_layer"]["subscribers"].append(ep("/swarm/sidechannel", PACKET))
    return g


@case("an outside process on a topic nobody listed", "mission_integrated", 4,
      "not in its allowlist")
def _outside_unlisted(g):
    g["/metrics_collector"]["subscribers"].append(
        ep("/telemetry/raw", "std_msgs/msg/String"))
    return g


@case("SwarmPacket outside the seam, on an allowlisted topic",
      "mission_integrated", 4, "not a tx or rx topic")
def _packet_on_allowed_topic(g):
    # /rosout is legitimately in the observer's allowlist. Rule 6 still bans
    # swarm payload on it, and that rule had never been applied to the three
    # processes outside the swarm.
    g["/metrics_collector"]["publishers"].append(ep("/rosout", PACKET))
    return g


@case("metrics collector reading swarm tx traffic", "mission_integrated", 4,
      "not in its allowlist")
def _metrics_swarm_traffic(g):
    g["/metrics_collector"]["subscribers"].append(
        ep("/uavx/uav_1/tx", PACKET))
    return g


@case("outside allowlist wildcard cannot cross a namespace",
      "mission_integrated", 4, "not in its allowlist")
def _outside_nested_topic(g):
    g["/link_layer"]["subscribers"].append(
        ep("/uavx/uav_1/private/tx", PACKET))
    return g


@case("GCS node cannot open a PX4 namespace", "relay_required", 3,
      "GCS has no PX4 namespace")
def _gcs_px4(g):
    g["/gcs/gcs_node"]["subscribers"].append(
        ep("/gcs/fmu/out/status", "px4_msgs/msg/VehicleStatus"))
    return g


@case("an endpoint with a blank message type", "relay_required", 3,
      "CANNOT:without a message type")
def _blank_type(g):
    g["/uav_1/router"]["publishers"].append(ep("/rosout", ""))
    return g


def run_record_for(snapshot: Path, meta: dict, dest: Path) -> Path:
    """The run record a correct capture would have been written beside.

    Round 6 finding 6 requires the snapshot to be tied to a run, so every
    fixture needs one. Built from the snapshot's own _meta so a fixture that
    mutates the metadata is testing the binding rather than fighting it.
    """
    rec = {
        "run_id": meta.get("run_id"),
        "scenario_path": f"scenarios/{meta.get('scenario')}.yaml",
        "source_tree_sha256": meta.get("source_tree_sha256"),
        "started_at": "2026-09-20T09:00:00",
        "ended_at": "2026-09-20T11:00:00",
        "graph_snapshot_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    }
    dest.write_text(json.dumps(rec), encoding="utf-8")
    return dest


def run_graph(scenario: str, snapshot: Path, record: Path = None) -> tuple:
    if record is None:
        # Derive one from whatever the snapshot claims, so the case under test
        # is the only thing that can fail.
        try:
            meta = json.loads(snapshot.read_text(encoding="utf-8")).get("_meta") or {}
        except (json.JSONDecodeError, OSError):
            meta = {}
        record = run_record_for(snapshot, meta,
                                snapshot.with_suffix(".run.jsonl"))
    p = subprocess.run(
        [sys.executable, str(SEAM_GRAPH), "--scenario", scenario,
         "--snapshot", str(snapshot), "--expect-run", str(record)],
        capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout + p.stderr


# ------------------------------------------------------------ static fixtures
def static_case(name: str, expect: str | None):
    def deco(fn):
        STATIC_CASES.append((name, expect, fn))
        return fn
    return deco


@static_case("clean source tree", None)
def _s_clean(src: Path):
    (src / "uavx_comms").mkdir(parents=True)
    (src / "uavx_comms" / "router.py").write_text(
        "self.tx = self.create_publisher(SwarmPacket, f'/uavx/{self.own}/tx', 10)\n",
        encoding="utf-8")


@static_case("a swarm module reading simulator ground truth", "ground truth")
def _s_truth(src: Path):
    (src / "uavx_mission").mkdir(parents=True)
    (src / "uavx_mission" / "executor.py").write_text(
        "sub = self.create_subscription(ModelStates, '/gazebo/model_states', cb, 10)\n",
        encoding="utf-8")


@static_case("a swarm module naming two vehicles", "different vehicles")
def _s_two_ids(src: Path):
    (src / "uavx_roles").mkdir(parents=True)
    (src / "uavx_roles" / "manager.py").write_text(
        "A = '/uavx/uav_1/tx'\nB = '/uavx/uav_2/tx'\n", encoding="utf-8")


@static_case("a swarm module creating a service", "service or action")
def _s_service(src: Path):
    (src / "uavx_roles").mkdir(parents=True)
    (src / "uavx_roles" / "elect.py").write_text(
        "self.srv = self.create_service(Elect, 'elect', cb)\n", encoding="utf-8")


@static_case("the link layer reading ground truth, which is its job", None)
def _s_link_layer(src: Path):
    (src / "uavx_comms" / "link_layer").mkdir(parents=True)
    (src / "uavx_comms" / "link_layer" / "model.py").write_text(
        "sub = self.create_subscription(ModelStates, '/gazebo/model_states', cb, 10)\n",
        encoding="utf-8")


@static_case("a module merely named like the link layer", "ground truth")
def _s_link_layer_lookalike(src: Path):
    (src / "uavx_comms" / "not_link_layer").mkdir(parents=True)
    (src / "uavx_comms" / "not_link_layer" / "model.py").write_text(
        "sub = self.create_subscription(ModelStates, '/gazebo/model_states', cb, 10)\n",
        encoding="utf-8")


def find_bash() -> "str | None":
    """A real POSIX bash, never the WSL launcher.

    On Windows the bash on PATH is often System32's, which is a shim that starts
    a WSL distro. Handing it a Windows path gets execvpe(/bin/bash) failed, and
    the suite would then report every static fixture as a failure. That is the
    setup-error-reads-as-a-result bug round 4 found in this very file, so it is
    worth being careful about here of all places.
    """
    if os.name != "nt":
        return shutil.which("bash")
    for c in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe"):
        if os.path.isfile(c):
            return c
    b = shutil.which("bash")
    return b if b and "System32" not in b else None


def run_static(bash: str, ws: Path) -> tuple:
    env = dict(os.environ, UAVX_WS_SRC=str(ws))
    p = subprocess.run([bash, str(CHECK_SEAM), "--static-only"],
                       capture_output=True, text=True, timeout=180, env=env)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    failures = 0
    tmp = Path(tempfile.mkdtemp(prefix="uavx-seam-"))

    print("--- graph fixtures")
    for name, scenario, week, expect, mutate in CASES:
        g = mutate(clean_graph(week))
        snap = tmp / (name.replace(" ", "_").replace("'", "").replace("/", "-") + ".json")
        snap.write_text(json.dumps(g, indent=2), encoding="utf-8")
        rc, out = run_graph(scenario, snap)

        if expect is None:
            good, why = rc == 0, "expected a clean pass"
        elif expect.startswith("CANNOT:"):
            # Unusable input must stop the check, not fail it as a violation.
            # A snapshot the checker cannot trust and a swarm that broke the
            # seam are different answers and must not share an exit code.
            want = expect.split(":", 1)[1]
            good = rc == 2 and want in out
            why = f"expected exit 2 saying {want!r}, got {rc}"
        elif rc == 0:
            good, why = False, "passed, but should have been caught"
        elif rc != 1:
            good, why = False, f"exited {rc}; a bypass must be a violation, not a setup error"
        else:
            good = expect in out
            why = f"caught, but never said {expect!r}"
        print(f"  {'ok  ' if good else 'FAIL'}  {name:<52} rc={rc}")
        if not good:
            failures += 1
            print(f"           {why}")
            for line in out.strip().splitlines()[:6]:
                print(f"           {line}")

    print("\n--- the snapshot has to belong to the run that was accepted")
    for label, mutate, want in (
        ("a snapshot from another run id",
         lambda r: r.update({"run_id": "some_other_run"}), "was not captured during this run"),
        ("a snapshot from another source tree",
         lambda r: r.update({"source_tree_sha256": "9" * 64}), "was not captured during this run"),
        ("a run record for a different scenario",
         lambda r: r.update({"scenario_path": "scenarios/direct_only.yaml"}),
         "is a run of"),
        ("a graph captured before the run started",
         lambda r: r.update({"started_at": "2026-09-20T10:30:00",
                             "ended_at": "2026-09-20T11:00:00"}),
         "outside the run window"),
        ("a run accepted against a different graph",
         lambda r: r.update({"graph_snapshot_sha256": "1" * 64}),
         "different graph"),
    ):
        snap = tmp / "clean_W4_graph_with_role_managers.json"
        if not snap.is_file():
            snap = next(tmp.glob("clean_W4*.json"), None)
        if snap is None:
            print(f"  FAIL  {label:<44} no clean W4 snapshot to mutate")
            failures += 1
            continue
        meta = json.loads(snap.read_text(encoding="utf-8"))["_meta"]
        rec_path = tmp / f"bind_{label.replace(' ', '_')}.jsonl"
        run_record_for(snap, meta, rec_path)
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        mutate(rec)
        rec_path.write_text(json.dumps(rec), encoding="utf-8")
        rc, out = run_graph("mission_integrated", snap, rec_path)
        good = rc == 2 and want in out
        print(f"  {'ok  ' if good else 'FAIL'}  {label:<44} rc={rc}")
        if not good:
            failures += 1
            print(f"           expected exit 2 saying {want!r}")
            for line in out.strip().splitlines()[:4]:
                print(f"           {line}")

    print("\n--- malformed input must stop the check, not pass it")
    bad = tmp / "untyped.json"
    bad.write_text(json.dumps({
        "_meta": {"captured_at": "2026-09-20T10:00:00", "scenario": "relay_required",
                  "run_id": "fixture_run", "source_tree_sha256": "0" * 64,
                  "commands": {"node_list": {"returncode": 0},
                               "/uav_1/router": {"returncode": 0}}},
        "/uav_1/router": {"publishers": ["/uavx/uav_1/tx"]}}), encoding="utf-8")
    rc, out = run_graph("relay_required", bad)
    good = rc == 2 and "without a message type" in out
    print(f"  {'ok  ' if good else 'FAIL'}  untyped snapshot refused              rc={rc}")
    failures += 0 if good else 1

    rc, out = run_graph("no_such_scenario", tmp / "clean_W3_graph,_no_role_managers_yet.json")
    good = rc == 2 and "no seam manifest" in out
    print(f"  {'ok  ' if good else 'FAIL'}  unknown scenario refused              rc={rc}")
    failures += 0 if good else 1

    print("\n--- static fixtures")
    bash = find_bash()
    if bash is None:
        print("  FAIL  no POSIX bash found, so the static pass was never exercised.")
        print("        A suite that silently skips half of itself is the bug this")
        print("        file exists to prevent.")
        failures += 1
    else:
        for name, expect, build in STATIC_CASES:
            ws = tmp / ("ws_" + name.replace(" ", "_").replace(",", ""))
            (ws / "src").mkdir(parents=True)
            build(ws / "src")
            rc, out = run_static(bash, ws)
            if expect is None:
                good, why = rc == 0, "expected a clean pass"
            else:
                good = rc != 0 and expect in out
                why = f"expected rc!=0 and text {expect!r}"
            print(f"  {'ok  ' if good else 'FAIL'}  {name:<52} rc={rc}")
            if not good:
                failures += 1
                print(f"           {why}")
                for line in out.strip().splitlines()[:6]:
                    print(f"           {line}")

    total = len(CASES) + 2 + len(STATIC_CASES)
    print()
    if failures:
        print(f"FAILED: {failures} of {total} fixtures behaved wrongly")
        return 1
    print(f"all {total} seam fixtures behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
