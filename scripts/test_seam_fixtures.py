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

CASES: list = []
STATIC_CASES: list = []


def ep(topic: str, typ: str) -> dict:
    return {"topic": topic, "type": typ}


def clean_graph(week: int) -> dict:
    """The graph a correct implementation produces.

    W3 has no role managers because uavx_roles does not exist yet. That is the
    whole point of per-scenario manifests.
    """
    procs = ["router", "mission_executor"]
    if week >= 4:
        procs.append("role_manager")

    g = {}
    for v in VEHICLES:
        for proc in procs:
            node = f"/{v}/{proc}"
            g[node] = {
                "publishers": [ep(f"/uavx/{v}/tx", PACKET), ep("/rosout", LOG)],
                "subscribers": [
                    ep(f"/uavx/{v}/rx", PACKET),
                    ep(f"/{v}/fmu/out/vehicle_local_position", POSE),
                    ep("/parameter_events", PARAM),
                ],
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
        "publishers": [], "subscribers": [ep("/gazebo/model_states", TRUTH)],
        "services": [], "actions": [],
    }
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
    g["/uav_2/sneaky_helper"] = {
        "publishers": [ep("/uavx/uav_2/tx", PACKET)],
        "subscribers": [], "services": [], "actions": [],
    }
    return g


@case("node name merely containing an outside-process name", "relay_required", 3,
      "in neither this scenario's manifest")
def _substring_exemption(g):
    g["/uav_2/link_layer_helper"] = {
        "publishers": [],
        "subscribers": [ep("/gazebo/model_states", TRUTH)],
        "services": [], "actions": [],
    }
    return g


@case("link layer publishing into a tx", "relay_required", 3,
      "publishing to tx would let it invent traffic")
def _link_layer_injects(g):
    g["/link_layer"]["publishers"].append(ep("/uavx/uav_1/tx", PACKET))
    return g


@case("scenario runner carrying swarm traffic", "mission_integrated", 4,
      "never by sending swarm traffic")
def _runner_traffic(g):
    g["/scenario_runner"] = {
        "publishers": [ep("/uavx/uav_3/rx", PACKET)],
        "subscribers": [], "services": [], "actions": [],
    }
    return g


def run_graph(scenario: str, snapshot: Path) -> tuple:
    p = subprocess.run(
        [sys.executable, str(SEAM_GRAPH), "--scenario", scenario,
         "--snapshot", str(snapshot)],
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

    print("\n--- malformed input must stop the check, not pass it")
    bad = tmp / "untyped.json"
    bad.write_text(json.dumps({"/uav_1/router": {"publishers": ["/uavx/uav_1/tx"]}}),
                   encoding="utf-8")
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
