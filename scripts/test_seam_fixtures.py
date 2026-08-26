#!/usr/bin/env python3
"""Prove check_seam.sh catches the bypasses it claims to.

Round 3 finding 2: "Add fixtures for all six named bypasses and one clean graph
before trusting this gate." A seam checker nobody has seen fail is not evidence
of anything, and this repo has already shipped two checks that passed on broken
systems.

Each fixture is a ROS graph snapshot in the shape the scenario runner captures.
One must pass. Every other must fail, and for the stated reason.

    python3 scripts/test_seam_fixtures.py
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEAM = REPO / "scripts" / "check_seam.sh"

VEHICLES = ["uav_1", "uav_2", "uav_3", "uav_4"]


def clean_graph() -> dict:
    """The graph a correct implementation produces."""
    g = {}
    for v in VEHICLES:
        for proc in ("router", "mission_executor", "role_manager"):
            g[f"/{v}/{proc}"] = {
                "publishers": [f"/uavx/{v}/tx"],
                "subscribers": [f"/uavx/{v}/rx", f"/{v}/fmu/out/vehicle_local_position"],
                "services": [f"/{v}/{proc}/get_parameters"],
                "actions": [],
            }
    g["/gcs/gcs_node"] = {
        "publishers": ["/uavx/gcs/tx"],
        "subscribers": ["/uavx/gcs/rx"],
        "services": ["/gcs/gcs_node/list_parameters"],
        "actions": [],
    }
    # Outside the swarm, allowed to see everything.
    g["/link_layer"] = {
        "publishers": [f"/uavx/{v}/rx" for v in VEHICLES] + ["/uavx/gcs/rx"],
        "subscribers": [f"/uavx/{v}/tx" for v in VEHICLES]
        + ["/uavx/gcs/tx", "/gazebo/model_states"],
        "services": [],
        "actions": [],
    }
    g["/metrics_collector"] = {
        "publishers": [], "subscribers": ["/gazebo/model_states"],
        "services": [], "actions": [],
    }
    return g


FIXTURES: list[tuple[str, bool, callable]] = []


def fixture(name: str, should_pass: bool):
    def deco(fn):
        FIXTURES.append((name, should_pass, fn))
        return fn
    return deco


@fixture("clean graph", True)
def _clean(g):
    return g


@fixture("bypass 1: reads another vehicle's rx", False)
def _other_rx(g):
    g["/uav_3/router"]["subscribers"].append("/uavx/uav_4/rx")
    return g


@fixture("bypass 2: shared broadcast topic", False)
def _shared(g):
    g["/uav_3/router"]["publishers"].append("/uavx/broadcast")
    return g


@fixture("bypass 3: publishes to its own rx", False)
def _wrong_direction(g):
    g["/uav_2/router"]["publishers"].append("/uavx/uav_2/rx")
    return g


@fixture("bypass 4: reads another vehicle's PX4 namespace", False)
def _other_px4(g):
    g["/uav_1/mission_executor"]["subscribers"].append("/uav_4/fmu/out/vehicle_local_position")
    return g


@fixture("bypass 5: service between swarm nodes", False)
def _service(g):
    g["/uav_2/role_manager"]["services"].append("/uavx/uav_2/elect")
    return g


@fixture("bypass 6: swarm node reads simulator ground truth", False)
def _ground_truth(g):
    g["/uav_4/mission_executor"]["subscribers"].append("/gazebo/model_states")
    return g


@fixture("unknown process not in the manifest", False)
def _unknown(g):
    g["/uav_2/sneaky_helper"] = {
        "publishers": ["/uavx/uav_3/tx"], "subscribers": [],
        "services": [], "actions": [],
    }
    return g


@fixture("expected process missing", False)
def _missing(g):
    del g["/gcs/gcs_node"]
    return g


def run(snapshot: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-lc",
         f"cd '{wsl_path(REPO)}' && bash scripts/check_seam.sh --from-snapshot '{wsl_path(snapshot)}'"],
        capture_output=True, text=True, timeout=300)
    return proc.returncode, proc.stdout + proc.stderr


def wsl_path(p: Path) -> str:
    s = str(p).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        s = "/mnt/" + s[0].lower() + s[2:]
    return s


def main() -> int:
    failures = 0
    tmp = Path(tempfile.mkdtemp(prefix="uavx-seam-"))

    for name, should_pass, mutate in FIXTURES:
        g = mutate(clean_graph())
        snap = tmp / (name.replace(" ", "_").replace(":", "") + ".json")
        snap.write_text(json.dumps(g, indent=2), encoding="utf-8")

        rc, out = run(snap)
        passed = rc == 0
        good = passed == should_pass
        verdict = "ok  " if good else "FAIL"
        want = "pass" if should_pass else "be caught"
        print(f"  {verdict}  {name:<48} expected to {want}, rc={rc}")
        if not good:
            failures += 1
            for line in out.strip().splitlines()[-6:]:
                print(f"           {line}")

    print()
    if failures:
        print(f"FAILED: {failures} of {len(FIXTURES)} fixtures behaved wrongly")
        return 1
    print(f"all {len(FIXTURES)} seam fixtures behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
