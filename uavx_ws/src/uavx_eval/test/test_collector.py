"""The collector: its endpoints, its sample rate, and what it refuses to claim.

Two of these are worth more than the rest. The endpoint test compares the topic
names in this package against `scripts/seam_manifests.json`, so a topic renamed
on one side and not the other fails here rather than during a run that has
already cost twenty minutes. The rate test drives the frozen sampling rate,
which the week 1 audit found the runner missing by a factor of four; a survey
scored at a quarter of the intended resolution is a different measurement.

Nothing here imports rclpy. `metrics_collector.py` is a wiring file and
everything it wires is in `collector.py`, which is the point of the split.
"""

import json

import pytest
from check_geometry import MIN_SEPARATION

from uavx_eval import collector
from uavx_eval.collector import (COVERAGE_SOURCE, CollectorError,
                                 MetricsCollector, parse_model_map)

MANIFEST_NODE = "/metrics_collector"
MODELS = ["iris_0=uav_1", "iris_1=uav_2"]


@pytest.fixture
def manifest(repo):
    return json.loads(
        (repo / "scripts" / "seam_manifests.json").read_text(encoding="utf-8"))


def made(**kwargs):
    settings = dict(run_id="r_20260903T000000Z",
                    scenario_path="scenarios/harness_check.yaml",
                    min_separation_m=MIN_SEPARATION,
                    model_map=parse_model_map(MODELS))
    settings.update(kwargs)
    return MetricsCollector(**settings)


# --------------------------------------------------------------- the seam
def test_the_node_name_is_the_one_the_seam_check_looks_for(manifest):
    """Matched by exact name, so the name is part of the contract."""
    assert MANIFEST_NODE == "/" + collector.NODE_NAME
    assert MANIFEST_NODE in manifest["outside_processes"]
    assert MANIFEST_NODE in manifest["required_outside"]


def test_every_endpoint_this_package_names_is_on_the_allowlist(manifest):
    allowed = manifest["outside_allowlist"][MANIFEST_NODE]
    assert collector.METRICS_TOPIC in allowed["publishers"]
    assert collector.GROUND_TRUTH_TOPIC in allowed["subscribers"]
    assert collector.PARAMETER_EVENTS_TOPIC in allowed["subscribers"]


def test_the_endpoints_the_manifest_demands_are_the_ones_it_holds(manifest):
    required = manifest["required_endpoints"]["metrics_collector"]
    assert required["publishers"] == [collector.METRICS_TOPIC]
    assert len(required["subscribers"]) == 1
    assert "model_states" in required["subscribers"][0]
    assert "model_states" in collector.GROUND_TRUTH_TOPIC


def test_the_collector_claims_no_delivery_number(manifest):
    """It cannot see a packet, so it must not report one.

    The allowlist gives it ground truth and nothing on a tx or rx endpoint, so
    every delivery field belongs to the link layer and the GCS. A payload that
    carried one would be a number with no measurement behind it.
    """
    payload = made().payload()
    forbidden = ("delivery", "packets", "app_packets_sent_by_node",
                 "delivered_hops_by_node")
    assert not [key for key in payload
                if any(word in key for word in forbidden)]


# ------------------------------------------------------------ the mapping
def test_a_model_map_entry_that_is_not_a_mapping_is_refused():
    for bad in ("iris_0", "=uav_1", "iris_0=", ""):
        with pytest.raises(CollectorError):
            parse_model_map([bad])


def test_two_models_cannot_be_the_same_vehicle():
    """One vehicle's track being two vehicles at once poisons every metric."""
    with pytest.raises(CollectorError):
        parse_model_map(["iris_0=uav_1", "iris_1=uav_1"])


def test_scenery_is_not_a_vehicle():
    metrics = made()
    assert metrics.vehicle_for_model("ground_plane") is None
    poses = metrics.poses_from_models(
        ["ground_plane", "iris_0"], [(0.0, 0.0, 0.0), (1.0, 2.0, 3.0)])
    assert poses == {"uav_1": (1.0, 2.0, 3.0)}


# --------------------------------------------------------------- the rate
def test_ground_truth_is_sampled_at_the_frozen_rate():
    """Ground truth arrives far faster than the design samples it."""
    metrics = made(sample_hz=collector.POSE_SAMPLE_HZ)
    offered = 0
    for step in range(1000):                       # 10 s of 100 Hz messages
        sim_time = step / 100.0
        if metrics.offer(sim_time, {"uav_1": (0.0, 0.0, 0.0)}):
            offered += 1
    assert offered == metrics.frames
    assert metrics.frames == int(collector.POSE_SAMPLE_HZ * 10)


def test_the_rate_reached_is_reported_next_to_the_rate_asked_for():
    """The frozen 20 Hz is a target, and no run has reached it.

    Gazebo publishes /clock at 10 Hz and a sampler on simulated time cannot
    land more than once per tick, which week 1 measured at 9.78 Hz. A payload
    carrying only the target would describe a resolution nothing achieved, so
    a coverage figure has the rate that produced it sitting beside it.
    """
    metrics = made(sample_hz=collector.POSE_SAMPLE_HZ)
    for step in range(101):                        # 10 s of 10 Hz ground truth
        metrics.offer(step / 10.0, {"uav_1": (0.0, 0.0, 0.0)})
    payload = metrics.payload()
    assert payload["ground_truth_rate_hz_target"] == collector.POSE_SAMPLE_HZ
    assert payload["ground_truth_rate_hz"] == pytest.approx(10.0)
    assert payload["ground_truth_rate_hz"] < payload["ground_truth_rate_hz_target"]


def test_one_frame_is_not_a_rate():
    """Intervals over span. A single sample has no rate to report."""
    metrics = made(sample_hz=1.0)
    metrics.offer(0.0, {"uav_1": (0.0, 0.0, 0.0)})
    assert metrics.achieved_rate_hz() is None
    assert "ground_truth_rate_hz" not in metrics.payload()


def test_a_stall_does_not_produce_a_burst_of_catch_up_frames():
    """A sample is a moment that was watched, not a debt owed by the clock."""
    metrics = made(sample_hz=10.0)
    assert metrics.offer(0.0, {"uav_1": (0.0, 0.0, 0.0)}) is True
    assert metrics.offer(30.0, {"uav_1": (0.0, 0.0, 0.0)}) is True
    assert metrics.offer(30.01, {"uav_1": (0.0, 0.0, 0.0)}) is False
    assert metrics.frames == 2


def test_a_sample_rate_that_is_not_a_rate_is_refused():
    for bad in (0, -20.0, float("inf")):
        with pytest.raises(CollectorError):
            made(sample_hz=bad)


def test_an_unattributed_payload_is_refused():
    """A metrics payload naming no run is evidence for no run."""
    with pytest.raises(CollectorError):
        made(run_id="  ")
    with pytest.raises(CollectorError):
        made(scenario_path="")


# ------------------------------------------------------------ the payload
def test_the_payload_reports_what_it_watched():
    metrics = made(sample_hz=1.0)
    for step in range(4):
        metrics.offer(float(step), {"uav_1": (0.0, 0.0, 0.0),
                                    "uav_2": (100.0, 0.0, 0.0)})
    payload = metrics.payload()
    assert payload["ground_truth_frames"] == 4
    assert payload["ground_truth_samples_by_node"] == {"uav_1": 4, "uav_2": 4}
    assert payload["vehicle_ids_observed"] == ["uav_1", "uav_2"]
    assert payload["contact_monitor_samples"] == 4
    assert payload["separation_violations"] == 0
    assert payload["min_pairwise_separation_m"] == pytest.approx(100.0)


def test_a_run_with_no_survey_reports_no_coverage():
    """Coverage of a box nobody flew is not zero, it is absent."""
    payload = made().payload()
    assert "coverage_fraction" not in payload
    assert "coverage_source" not in payload


def test_a_survey_run_reports_coverage_off_the_poses_it_saw():
    box = {"origin_x": 0.0, "origin_y": 0.0, "width_m": 4.0, "height_m": 1.0,
           "cell_m": 1.0, "footprint_m": 0.4}
    metrics = made(sample_hz=1.0, coverage=box)
    for step in range(2):
        metrics.offer(float(step), {"uav_1": (0.5 + step, 0.5, 10.0)})
    payload = metrics.payload()
    assert payload["coverage_cells_total"] == 4
    assert payload["coverage_cells_seen"] == 2
    assert payload["coverage_fraction"] == pytest.approx(0.5)
    assert payload["coverage_source"] == COVERAGE_SOURCE


def test_the_payload_is_json_the_run_metrics_message_can_carry():
    """RunMetrics.json_payload is one UTF-8 JSON object, so it has to serialise."""
    metrics = made(sample_hz=1.0)
    metrics.offer(0.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (50.0, 0.0, 0.0)})
    text = json.dumps(metrics.payload(), sort_keys=True, ensure_ascii=True)
    assert json.loads(text)["ground_truth_frames"] == 1
