"""What the metrics collector measures, with no ROS anywhere in it.

`metrics_collector.py` is the node. This is everything the node does apart from
talking to the graph, which is what makes it testable on a checkout with
nothing built and no simulator running.

The collector is the observer. stage-1/architecture.md section 1 puts it
outside the swarm alongside the link layer, and that is why it may read
simulator ground truth: it reports where the vehicles actually were, not where
a planner meant to send them. Its whole endpoint list is four topics and they
are named here rather than in the node, so `scripts/seam_manifests.json` and
this package can be compared without starting anything.

What it measures is what ground truth can answer:

    coverage        cells of the frozen box a pose sample actually saw
    separation      the closest pair, the frames that broke the floor, and how
                    many frames were watched at all

What it deliberately does not measure is delivery. Counting packets would mean
subscribing to a tx or rx endpoint, and section 1's allowlist gives the
collector ground truth and `/parameter_events` and nothing else. Delivery
belongs to the link layer and the GCS, which are inside the radio.
"""

from __future__ import annotations

import math

from uavx_eval.coverage import CoverageGrid, grid_from_spec
from uavx_eval.separation import SeparationMonitor

# The endpoint list, exactly as scripts/seam_manifests.json holds it for
# /metrics_collector. test/test_collector.py compares the two, so a topic
# renamed here without the manifest agreeing fails before a run does.
NODE_NAME = "metrics_collector"
METRICS_TOPIC = "/uavx_eval/metrics"
GROUND_TRUTH_TOPIC = "/gazebo/model_states"
PARAMETER_EVENTS_TOPIC = "/parameter_events"

# The rate stage-1/architecture.md freezes for observing physics, given three
# times: section 2 for the link model, section 5 for the separation monitor and
# section 6 for the coverage source. It is the target and it is not what any
# run has reached. Gazebo publishes /clock at 10.000 Hz, a sampler on simulated
# time can land at most once per tick, and week 1 measured 9.78 Hz across eight
# archived runs; docs/progress/week-1.md carries the diagnosis and the two
# fixes that did not work. So the payload reports the rate that was reached
# next to the rate that was asked for, and a coverage figure is read against
# the first of those. A collector that published only the target would be
# describing a resolution nothing achieved.
POSE_SAMPLE_HZ = 20.0

# The gate asserts this string, because a coverage number computed off the
# planned path and one computed off flown poses are the same type and mean
# opposite things. The producer has to write the label somewhere and this is
# the one place it is written.
COVERAGE_SOURCE = "pose_samples"

# scenarios/run-record.schema.json carries no version of its own, so this is
# the first. It moves when a change to that file would make an older reader
# misread a payload, not when a field is added.
SCHEMA_VERSION = 1


class CollectorError(ValueError):
    """The collector was asked for something it cannot honestly report."""


def parse_model_map(entries) -> dict:
    """Turn `model=uav_1` strings into a mapping, refusing anything ambiguous.

    Gazebo names models after the airframe and the instance, and the frozen
    geometry names vehicles `uav_1` to `uav_4`. Nothing in this package guesses
    the correspondence: a wrong guess silently attributes one vehicle's track
    to another, and every coverage and separation number downstream inherits
    it.
    """
    mapping = {}
    for entry in entries or []:
        text = str(entry)
        model, sep, vehicle = text.partition("=")
        model, vehicle = model.strip(), vehicle.strip()
        if not sep or not model or not vehicle:
            raise CollectorError(
                f"{text!r} is not a model to vehicle mapping. Write each one "
                f"as model=uav_1.")
        if model in mapping and mapping[model] != vehicle:
            raise CollectorError(
                f"model {model} is mapped to both {mapping[model]} and "
                f"{vehicle}")
        if vehicle in mapping.values() and mapping.get(model) != vehicle:
            raise CollectorError(
                f"vehicle {vehicle} is claimed by more than one model, so its "
                f"track would be two vehicles at once")
        mapping[model] = vehicle
    return mapping


class MetricsCollector:
    """Sample ground truth at the frozen rate and report what it showed."""

    def __init__(self, run_id, scenario_path, min_separation_m,
                 coverage=None, sample_hz=POSE_SAMPLE_HZ, model_map=None):
        if not str(run_id).strip():
            raise CollectorError("the collector needs the run id it is "
                                 "reporting for; an unattributed payload is "
                                 "not evidence for any run")
        if not str(scenario_path).strip():
            raise CollectorError("the collector needs the scenario path it is "
                                 "reporting for")
        if not (isinstance(sample_hz, (int, float)) and math.isfinite(sample_hz)
                and sample_hz > 0):
            raise CollectorError(f"sample_hz is {sample_hz!r}; it is a positive "
                                 f"rate in hertz")
        self.run_id = str(run_id)
        self.scenario_path = str(scenario_path)
        self.sample_hz = float(sample_hz)
        self.period_s = 1.0 / self.sample_hz
        self.model_map = dict(model_map or {})
        self.separation = SeparationMonitor(min_separation_m)
        if coverage is None:
            self.grid = None
        elif isinstance(coverage, CoverageGrid):
            self.grid = coverage
        else:
            self.grid = grid_from_spec(coverage)
        self.frames = 0
        self.samples_by_node = {}
        self.first_sample_s = None
        self.last_sample_s = None
        self._slot = -1

    def vehicle_for_model(self, model_name):
        """The vehicle a gazebo model stands for, or None when it is scenery."""
        return self.model_map.get(str(model_name))

    def poses_from_models(self, names, positions) -> dict:
        """Reduce one ModelStates message to the vehicles this run cares about."""
        poses = {}
        for name, position in zip(names, positions):
            vehicle = self.vehicle_for_model(name)
            if vehicle is not None:
                poses[vehicle] = tuple(float(v) for v in position)
        return poses

    def offer(self, sim_time_s, poses) -> bool:
        """Take a frame if the frozen sample period has elapsed.

        Ground truth arrives far faster than the rate the design samples at, so
        the collector decides when a frame counts rather than counting whatever
        the simulator happened to publish. Returns whether this frame was taken,
        which is what test/test_collector.py asserts the rate against.
        """
        now = float(sim_time_s)
        if not math.isfinite(now):
            return False
        if self.first_sample_s is None:
            slot = 0
        else:
            # The slot is computed from the first sample rather than
            # accumulated one period at a time, because twenty additions of
            # 0.05 do not land on 1.0 and a rate test would then drift by a
            # frame every second. The epsilon is for the same arithmetic: a
            # sample exactly on its boundary must not fall into the slot below.
            elapsed = now - self.first_sample_s
            if elapsed < 0:
                return False
            slot = int(math.floor(elapsed / self.period_s + 1e-9))
        if slot <= self._slot:
            return False
        self._slot = slot

        self.frames += 1
        if self.first_sample_s is None:
            self.first_sample_s = now
        self.last_sample_s = now
        for vehicle, position in poses.items():
            self.samples_by_node[vehicle] = self.samples_by_node.get(vehicle, 0) + 1
            if self.grid is not None:
                self.grid.mark(position[0], position[1])
        self.separation.offer(now, poses)
        return True

    def achieved_rate_hz(self):
        """The rate this run actually sampled at, or None before two frames.

        Intervals over span, not frames over span, so one frame is no rate at
        all and two frames a second apart are 1 Hz. The runner computes
        pose_rate_hz the same way, and the two numbers describe the same
        simulated clock, so they have to be comparable.
        """
        if self.frames < 2:
            return None
        span = self.last_sample_s - self.first_sample_s
        if span <= 0:
            return None
        return (self.frames - 1) / span

    def payload(self) -> dict:
        """The fragment of the run record this collector is answerable for.

        Deliberately a fragment. The runner owns provenance and the link layer
        owns delivery, and a collector that filled in fields it did not measure
        would be the metrics writer that never saw a vehicle, which is round 2
        finding 5.
        """
        out = {
            "ground_truth_frames": self.frames,
            "ground_truth_samples_by_node": dict(
                sorted(self.samples_by_node.items())),
            "ground_truth_rate_hz_target": self.sample_hz,
            "vehicle_ids_observed": sorted(self.samples_by_node),
        }
        if self.first_sample_s is not None:
            out["ground_truth_first_sample_s"] = self.first_sample_s
            out["ground_truth_last_sample_s"] = self.last_sample_s
        achieved = self.achieved_rate_hz()
        if achieved is not None:
            out["ground_truth_rate_hz"] = achieved
        out.update(self.separation.report())
        if self.grid is not None:
            out["coverage_fraction"] = self.grid.fraction()
            out["coverage_source"] = COVERAGE_SOURCE
            out["coverage_cells_total"] = self.grid.cell_count
            out["coverage_cells_seen"] = self.grid.covered
        return out
