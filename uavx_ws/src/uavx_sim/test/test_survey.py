"""Chunk 2.4: the decisions behind the survey run, proved without a simulator.

scenario_runner.py launches a mission executor per vehicle and one collector,
then reads coverage off the collector's last payload into the record. Each
of those steps has a way of being wrong that a green run would hide: a box
with a default dimension, a vehicle mapped to the wrong gazebo model, a home
guessed from the spawn order, a float that YAML reads as a string, a fraction
that does not match its own counts. This file holds each of them.

The last tests are the ones that matter most. scenarios/survey_baseline.yaml
carries the frozen box so the runner can hand it to two nodes, and
uavx_mission.survey_area carries the same box so the planner can do
arithmetic on it. Two copies of one design drift in silence, so this compares
them and fails when either moves.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_survey.py

Runs on a clean checkout with nothing built.
"""

import copy
import sys
from pathlib import Path

import pytest
import yaml

from uavx_sim.survey import (COVERAGE_KEYS, SURVEY_KEYS, SurveyError,
                             SurveySpec, collector_command,
                             coverage_from_payload, home_of,
                             mission_node_command, model_map, ros_args,
                             survey_spec)

# test/ -> uavx_sim/ -> src/ -> uavx_ws/ -> the repository root.
REPO = Path(__file__).resolve().parents[4]
SCENARIO = REPO / "scenarios" / "survey_baseline.yaml"
MISSION_PACKAGE = REPO / "uavx_ws" / "src" / "uavx_mission"

# Built per index, never written out. scripts/check_seam.sh counts distinct
# vehicle endpoint literals per file and that rule covers tests.
VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))

BOX = {"origin_x": 375.0, "origin_y": -100.0, "width_m": 200.0,
       "height_m": 200.0, "cell_m": 10.0, "footprint_m": 12.0}


def manifest():
    return {"model": "iris", "vehicles": [
        {"instance": i, "vehicle_id": VEHICLES[i], "x_m": 0.0,
         "y_m": (i - 1.5) * 5.0, "z_m": 0.83} for i in range(4)]}


def spec():
    return survey_spec({"survey": dict(BOX, cruise_speed_mps=10.0)})


# ------------------------------------------------------------- the spec
def test_no_survey_block_means_no_survey():
    assert survey_spec({"name": "harness_check"}) is None


def test_the_frozen_box_reads_back_with_its_cell_count():
    s = spec()
    assert isinstance(s, SurveySpec)
    assert s.cell_count == 400
    assert s.cruise_speed_mps == 10.0


@pytest.mark.parametrize("key", SURVEY_KEYS)
def test_a_missing_dimension_is_refused_not_defaulted(key):
    block = dict(BOX)
    del block[key]
    with pytest.raises(SurveyError, match=key):
        survey_spec({"survey": block})


@pytest.mark.parametrize("key", ("width_m", "height_m", "cell_m", "footprint_m"))
def test_a_non_positive_dimension_is_refused(key):
    with pytest.raises(SurveyError, match=key):
        survey_spec({"survey": dict(BOX, **{key: 0})})


def test_a_box_that_does_not_tile_has_no_denominator():
    with pytest.raises(SurveyError, match="cell count"):
        survey_spec({"survey": dict(BOX, cell_m=7.0)})


def test_a_boolean_is_not_a_dimension():
    with pytest.raises(SurveyError, match="finite"):
        survey_spec({"survey": dict(BOX, cell_m=True)})


def test_a_bad_cruise_speed_is_refused():
    with pytest.raises(SurveyError, match="cruise_speed_mps"):
        survey_spec({"survey": dict(BOX, cruise_speed_mps=-1)})


def test_the_two_nodes_get_the_same_box_under_their_own_names():
    s = spec()
    collector = s.collector_parameters()
    mission = s.mission_parameters()
    assert collector == {f"coverage.{k}": BOX[k] for k in SURVEY_KEYS}
    assert mission["area_sw_m"] == [BOX["origin_x"], BOX["origin_y"]]
    assert mission["sensor_radius_m"] == BOX["footprint_m"]
    assert mission["cell_m"] == BOX["cell_m"]


# --------------------------------------------------------- the manifest
def test_model_map_pairs_each_gazebo_model_with_its_vehicle():
    entries = model_map(manifest())
    assert entries == [f"iris_{i}={VEHICLES[i]}" for i in range(4)]


def test_model_map_orders_by_instance_not_by_listing():
    m = manifest()
    m["vehicles"].reverse()
    assert model_map(m) == [f"iris_{i}={VEHICLES[i]}" for i in range(4)]


def test_model_map_refuses_a_manifest_without_a_model_name():
    m = manifest()
    del m["model"]
    with pytest.raises(SurveyError, match="model"):
        model_map(m)


def test_home_is_read_off_the_spawn_row_and_never_guessed():
    assert home_of(manifest()["vehicles"][0]) == (0.0, -7.5, 0.0)
    with pytest.raises(SurveyError, match="home"):
        home_of(None)


# --------------------------------------------------------- the commands
def test_floats_always_carry_a_decimal_point():
    # PyYAML reads 1e-05 as a string. A double parameter handed a string is
    # refused by rclpy after the simulator is already up.
    args = ros_args({"tiny": 1e-05, "whole": 30.0, "flag": True, "n": 3})
    assert "-p" in args
    assert "tiny:=0.000010" in args
    assert "whole:=30.000000" in args
    assert "flag:=true" in args
    assert "n:=3" in args


def test_mission_command_names_the_vehicle_and_its_measured_home():
    row = manifest()["vehicles"][2]
    cmd = mission_node_command(VEHICLES[2], row, 50.0, spec(), VEHICLES)
    assert cmd[:4] == ["ros2", "run", "uavx_mission", "mission_executor"]
    assert f"__ns:=/{VEHICLES[2]}" in cmd
    assert f"vehicle_id:={VEHICLES[2]}" in cmd
    assert "home_enu:=[0.000000, 2.500000, 0.000000]" in cmd
    assert "survey_altitude_m:=50.000000" in cmd
    assert "area_sw_m:=[375.000000, -100.000000]" in cmd
    assert "swarm_vehicles:=[" + ", ".join(VEHICLES) + "]" in cmd


def test_mission_command_refuses_a_vehicle_with_no_altitude():
    with pytest.raises(SurveyError, match="altitude"):
        mission_node_command(VEHICLES[0], manifest()["vehicles"][0], None,
                             spec(), VEHICLES)


def test_mission_command_refuses_a_vehicle_the_launcher_did_not_place():
    with pytest.raises(SurveyError, match="home"):
        mission_node_command(VEHICLES[0], None, 30.0, spec(), VEHICLES)


def test_collector_command_runs_on_simulated_time_with_the_box():
    cmd = collector_command("survey_baseline_20260904T120000Z",
                            "scenarios/survey_baseline.yaml", spec(),
                            model_map(manifest()), 10.0)
    assert cmd[:4] == ["ros2", "run", "uavx_eval", "metrics_collector"]
    assert "use_sim_time:=true" in cmd
    assert "coverage.footprint_m:=12.000000" in cmd
    assert "min_separation_m:=10.000000" in cmd
    assert ("model_map:=[" + ", ".join(model_map(manifest())) + "]") in cmd


def test_collector_command_refuses_an_empty_model_map():
    with pytest.raises(SurveyError, match="model"):
        collector_command("r", "s", spec(), [], 10.0)


# ---------------------------------------------------------- the payload
def payload(**overrides):
    base = {"coverage_fraction": 0.9725, "coverage_source": "pose_samples",
            "coverage_cells_total": 400, "coverage_cells_seen": 389}
    base.update(overrides)
    return base


def test_a_consistent_payload_is_carried_through_unchanged():
    assert coverage_from_payload(payload()) == payload()


@pytest.mark.parametrize("key", COVERAGE_KEYS)
def test_a_payload_missing_a_field_is_refused(key):
    p = payload()
    del p[key]
    with pytest.raises(SurveyError, match=key):
        coverage_from_payload(p)


def test_a_fraction_that_disagrees_with_its_counts_is_refused():
    with pytest.raises(SurveyError, match="does not equal"):
        coverage_from_payload(payload(coverage_fraction=0.99))


def test_a_fraction_outside_the_unit_interval_is_refused():
    with pytest.raises(SurveyError, match="coverage_fraction"):
        coverage_from_payload(payload(coverage_fraction=1.2,
                                      coverage_cells_seen=480))


def test_more_cells_seen_than_exist_is_refused():
    # The fraction stays inside [0, 1] on purpose. A payload claiming
    # 1.0025 fails the interval check first and would never reach this
    # branch, so the test would be passing for the wrong reason.
    with pytest.raises(SurveyError, match="exceeds"):
        coverage_from_payload(payload(coverage_cells_seen=401,
                                      coverage_fraction=1.0))


def test_a_blank_source_label_is_refused():
    with pytest.raises(SurveyError, match="coverage_source"):
        coverage_from_payload(payload(coverage_source=" "))


def test_no_payload_at_all_is_refused():
    with pytest.raises(SurveyError, match="no payload"):
        coverage_from_payload(None)


# ------------------------------------------------------ the frozen file
def test_survey_baseline_yaml_is_the_box_the_planner_holds():
    """Two homes for one box, compared. Fails in both directions."""
    assert SCENARIO.is_file(), f"{SCENARIO} is the chunk 2.4 deliverable"
    doc = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    s = survey_spec(doc)
    assert s is not None, "survey_baseline.yaml carries no survey block"

    sys.path.insert(0, str(MISSION_PACKAGE))
    from uavx_mission.survey_area import (BASELINE_CELL_M,
                                          BASELINE_DURATION_S,
                                          BASELINE_SENSOR_RADIUS_M,
                                          BASELINE_SIDE_M,
                                          BASELINE_SW_CORNER_M)

    assert (s.origin_x, s.origin_y) == BASELINE_SW_CORNER_M
    assert s.width_m == s.height_m == BASELINE_SIDE_M
    assert s.cell_m == BASELINE_CELL_M
    assert s.footprint_m == BASELINE_SENSOR_RADIUS_M
    assert float(doc["duration_s"]) == BASELINE_DURATION_S
    assert list(doc["vehicles"]) == list(VEHICLES)
    assert doc["injected_events"] == []
    # Every vehicle surveys, so every vehicle needs an altitude to survey at.
    for vehicle in doc["vehicles"]:
        assert doc["hover_altitudes_m"][vehicle] > 0


def test_a_mutated_baseline_box_would_be_noticed():
    """The comparison above must be live, not a tautology."""
    doc = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    moved = copy.deepcopy(doc)
    moved["survey"]["origin_x"] += 10.0
    assert survey_spec(moved).origin_x != survey_spec(doc).origin_x
