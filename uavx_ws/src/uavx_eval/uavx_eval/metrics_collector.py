"""The node that wires the collector to the graph, and nothing more.

Every measurement lives in `collector.py`, `coverage.py` and `separation.py`,
none of which import rclpy. This file holds the four endpoints
`scripts/seam_manifests.json` allows `/metrics_collector` to hold, the
parameters that say what run it is watching, and the timer that publishes.
Keeping it this thin is what lets the arithmetic be tested on a checkout with
nothing built, which matters because a simulator run is the most expensive way
there is to find a division in the wrong place.

Parameters, all of them declared and none of them guessed:

    run_id             the run this payload belongs to
    scenario_path      repository relative, the same string the record carries
    min_separation_m   the safety floor, from scripts/check_geometry.py
    model_map          gazebo model name to vehicle id, as `iris_0=uav_1`
    sample_hz          defaults to the frozen rate in collector.py
    sim_time_zero_s    scenario time zero, or NaN to take the first observation
    publish_period_s   how often a partial payload goes out
    coverage.*         the frozen survey box, or NaN for a run with no survey

The collector reads simulator ground truth. stage-1/architecture.md section 1
allows exactly this package and the link layer to do that, and
`scripts/check_seam.sh` enforces it, so nothing else in the workspace may copy
the subscription below.
"""

from __future__ import annotations

import json
import math
import signal

import rclpy
from gazebo_msgs.msg import ModelStates
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from uavx_msgs.msg import RunMetrics

from uavx_eval.collector import (GROUND_TRUTH_TOPIC, METRICS_TOPIC, NODE_NAME,
                                 POSE_SAMPLE_HZ, SCHEMA_VERSION,
                                 CollectorError, MetricsCollector,
                                 parse_model_map)
from uavx_eval.coverage import GridError

COVERAGE_KEYS = ("origin_x", "origin_y", "width_m", "height_m", "cell_m",
                 "footprint_m")


def _described(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


class MetricsCollectorNode(Node):
    """Subscribe to ground truth, sample it at the frozen rate, publish."""

    def __init__(self):
        super().__init__(NODE_NAME)

        self.declare_parameter("run_id", "", _described(
            "the run this payload belongs to. Empty is refused: an "
            "unattributed metrics payload is evidence for no run."))
        self.declare_parameter("scenario_path", "", _described(
            "repository relative scenario path, matching the run record."))
        self.declare_parameter("min_separation_m", float("nan"), _described(
            "the safety floor in metres. Frozen in architecture.md section 5 "
            "and defined in scripts/check_geometry.py."))
        self.declare_parameter("model_map", [""], _described(
            "gazebo model name to vehicle id, one `iris_0=uav_1` per entry."))
        self.declare_parameter("sample_hz", POSE_SAMPLE_HZ, _described(
            "ground truth sample rate. The frozen rate is the default."))
        self.declare_parameter("sim_time_zero_s", float("nan"), _described(
            "scenario time zero. NaN takes the first observation instead."))
        self.declare_parameter("publish_period_s", 5.0, _described(
            "how often a partial payload is published."))
        for key in COVERAGE_KEYS:
            self.declare_parameter(f"coverage.{key}", float("nan"), _described(
                f"{key} of the frozen survey box. All six or none."))

        run_id = self.get_parameter("run_id").value
        scenario_path = self.get_parameter("scenario_path").value
        model_entries = [entry for entry
                         in (self.get_parameter("model_map").value or [])
                         if str(entry).strip()]
        if not model_entries:
            raise CollectorError(
                "model_map is empty, so no gazebo model is known to be a "
                "vehicle and every pose would be scenery. The launcher knows "
                "the spawn order; this node does not guess it.")

        self.collector = MetricsCollector(
            run_id=run_id,
            scenario_path=scenario_path,
            min_separation_m=self.get_parameter("min_separation_m").value,
            coverage=self._coverage_spec(),
            sample_hz=self.get_parameter("sample_hz").value,
            model_map=parse_model_map(model_entries))

        self._zero_s = self.get_parameter("sim_time_zero_s").value
        self._published = 0

        self.metrics_publisher = self.create_publisher(
            RunMetrics, METRICS_TOPIC, 10)
        self.ground_truth = self.create_subscription(
            ModelStates, GROUND_TRUTH_TOPIC, self.on_model_states, 10)
        period = float(self.get_parameter("publish_period_s").value)
        self.timer = self.create_timer(period, self.publish_partial)

        self.get_logger().info(
            f"watching {GROUND_TRUTH_TOPIC} at "
            f"{self.collector.sample_hz} Hz for run {self.collector.run_id}")

    def _coverage_spec(self):
        """The six frozen box numbers, or None when this run surveys nothing."""
        values = {key: self.get_parameter(f"coverage.{key}").value
                  for key in COVERAGE_KEYS}
        given = {key: value for key, value in values.items()
                 if value is not None and math.isfinite(value)}
        if not given:
            return None
        if len(given) != len(COVERAGE_KEYS):
            missing = [key for key in COVERAGE_KEYS if key not in given]
            raise GridError(
                f"the coverage box is missing {', '.join(missing)}. Six "
                f"numbers describe it and a partial box has no cell count, so "
                f"no fraction of it means anything.")
        return given

    # ------------------------------------------------------------ the graph
    def sim_time_s(self) -> float:
        """Scenario time, from the ROS clock, with zero at scenario start."""
        now = self.get_clock().now().nanoseconds / 1e9
        if self._zero_s is None or not math.isfinite(self._zero_s):
            self._zero_s = now
        return now - self._zero_s

    def on_model_states(self, message) -> None:
        poses = self.collector.poses_from_models(
            message.name,
            [(pose.position.x, pose.position.y, pose.position.z)
             for pose in message.pose])
        if poses:
            self.collector.offer(self.sim_time_s(), poses)

    def publish_partial(self) -> None:
        self.publish(final=False)

    def publish(self, final: bool) -> None:
        message = RunMetrics()
        message.schema_version = SCHEMA_VERSION
        message.run_id = self.collector.run_id
        message.scenario_path = self.collector.scenario_path
        message.json_payload = json.dumps(self.collector.payload(),
                                          sort_keys=True, ensure_ascii=True)
        message.final = bool(final)
        self.metrics_publisher.publish(message)
        self._published += 1


def main(args=None) -> int:
    """Sample until asked to stop, then publish the payload that counts.

    The signal handling is not decoration. `rclpy.init` installs handlers for
    SIGINT and SIGTERM that shut the context down before the exception reaches
    any `finally`, so the final publish below ran against a dead context and
    logged "publisher's context is invalid" every single time. Chunk 2.4's
    first survey run found it: the runner waits for a final payload, and this
    node could never send one.

    So the handlers are replaced after init with ones that only set a flag,
    the spin becomes a loop that watches it, and the last publish happens
    while the context is still up. The originals go back on the way out, since
    a process that swallows its own termination signal is worse than one that
    misses a payload.
    """
    rclpy.init(args=args)
    node = None
    stopping = []

    def request_stop(signum, frame):                 # noqa: ARG001
        stopping.append(signum)

    previous = {}
    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[number] = signal.signal(number, request_stop)
        except (OSError, ValueError):
            # Not the main thread, or a platform without the signal. The loop
            # still ends when the context goes down.
            pass
    try:
        node = MetricsCollectorNode()
        while rclpy.ok() and not stopping:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            # The last payload is the one the run record is built from, so it
            # goes out on the way down rather than on the next timer tick that
            # never comes.
            try:
                node.publish(final=True)
            except Exception as exc:                 # noqa: BLE001
                node.get_logger().error(f"final metrics publish failed: {exc}")
            node.destroy_node()
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except (OSError, ValueError):
                pass
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
