"""One vehicle's router, wired to exactly two swarm endpoints and nothing else.

`router.Router` is the whole state machine and chunk 2.3 proved it with 91
tests and 52 mutations, none of which needed ROS. This file is the wiring, and
it is deliberately thin: it decodes what arrived on this vehicle's rx topic,
hands it to `on_rx`, calls `tick`, and publishes whatever `drain_tx` returns on
this vehicle's tx topic. Every decision is upstream of here.

The endpoint list is the one `scripts/seam_manifests.json` allows a `router`,
and every topic is built from the `vehicle_id` parameter rather than written
out. That is not style. The static seam pass counts the distinct vehicle
endpoint literals in a file and calls a file naming two of them a bypass,
because a process that can name a second vehicle's endpoint can talk to it
without crossing the radio.

**Where the position comes from.** Its own PX4 estimate, in its own local NED
frame, converted to the frozen frame by `uavx_mission.frames` and the home
offset the launcher measured. Not from ground truth, which the seam forbids to
every process but the radio and the observer, and not from a second copy of the
conversion, because `frames` already documents what goes wrong when a home is
subtracted in the wrong frame and the result still looks like a position.

**Why the observation timer is separate from the tick.** architecture.md
section 6 freezes the observation rate per surveying vehicle, and the tick runs
much faster because the router's timers are its own. Generating observations on
the tick would tie the rate the run record divides by to a loop cadence nobody
froze.
"""

from __future__ import annotations

import json
import os
import signal
from typing import Optional

import rclpy
from px4_msgs.msg import VehicleLocalPosition
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from uavx_msgs.msg import SwarmPacket

from uavx_mission import frames

from . import codec, election, params
from .router import Router

NODE_NAME = "router"

# The router's own timers are all frozen periods measured in seconds, and the
# shortest of them is the HELLO period. Ticking well inside it means a timer
# fires in the tick after it was due rather than a whole period late.
TICK_HZ = 20.0

# PX4 publishes its estimates best effort with a small queue. A reliable
# subscription never matches it, and the node then waits forever for a position
# that is being published a metre away. Same profile the mission node uses, for
# the same reason.
PX4_QOS = QoSProfile(
    reliability=QoSReliabilityPolicy.BEST_EFFORT,
    durability=QoSDurabilityPolicy.VOLATILE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=5,
)

ROLE_BY_NAME = {
    "survey": election.ROLE_SURVEY,
    "relay": election.ROLE_RELAY,
    "gcs_anchor": election.ROLE_GCS_ANCHOR,
}


def _described(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


class RouterNode(Node):
    """The mesh, for one vehicle, across one rx topic and one tx topic."""

    def __init__(self, node_name: str = NODE_NAME) -> None:
        super().__init__(node_name)

        self.declare_parameter("vehicle_id", "", _described(
            "which vehicle this is. Every endpoint is built from it."))
        self.declare_parameter("position_enu", [0.0, 0.0, 0.0], _described(
            "where this vehicle starts in the frozen frame. Replaced by its "
            "own PX4 estimate as soon as one arrives."))
        self.declare_parameter("home_enu", [0.0, 0.0, 0.0], _described(
            "where the launcher put this vehicle, from the spawn manifest. "
            "PX4 reports relative to it and the frozen frame is not."))
        self.declare_parameter("role", "survey", _described(
            "starting role: survey, relay or gcs_anchor."))
        self.declare_parameter("forwarding", True, _described(
            "false is the direct_only control. A relay that works in a run "
            "where the direct link also worked has shown nothing."))
        self.declare_parameter("elections_enabled", True, _described(
            "false for queue_drain, which holds the outage open on purpose."))
        self.declare_parameter("observations", True, _described(
            "whether this vehicle generates observations at the frozen rate."))
        self.declare_parameter("ledger_path", "", _described(
            "where to write this node's counters. Empty writes nothing."))

        vehicle_id = str(self.get_parameter("vehicle_id").value)
        if not vehicle_id:
            raise ValueError(
                "vehicle_id is required. A router that does not know which "
                "vehicle it is cannot build its own endpoints, and every "
                "topic it holds has to be its own.")
        self.vehicle_id = vehicle_id

        role_name = str(self.get_parameter("role").value)
        if role_name not in ROLE_BY_NAME:
            raise ValueError(
                f"role {role_name!r} is not one of "
                f"{', '.join(sorted(ROLE_BY_NAME))}")

        self.home = tuple(float(v) for v in self.get_parameter("home_enu").value)
        start = tuple(float(v) for v
                      in self.get_parameter("position_enu").value)

        self.router = Router(
            node_id=self.vehicle_id,
            position=start,
            role=ROLE_BY_NAME[role_name],
            forwarding=bool(self.get_parameter("forwarding").value),
            elections_enabled=bool(
                self.get_parameter("elections_enabled").value),
        )
        self.generates = bool(self.get_parameter("observations").value)
        self.ledger_path = str(self.get_parameter("ledger_path").value)

        swarm = "/uavx/" + self.vehicle_id
        px4 = "/" + self.vehicle_id + "/fmu"
        self.tx = self.create_publisher(SwarmPacket, swarm + "/tx", 50)
        self.create_subscription(SwarmPacket, swarm + "/rx", self.on_rx, 50)
        self.create_subscription(
            VehicleLocalPosition, px4 + "/out/vehicle_local_position",
            self.on_position, PX4_QOS)

        self._last_tick: Optional[float] = None
        self.encode_failures = 0
        self.decode_failures = 0
        self.positions_seen = 0

        self.create_timer(1.0 / TICK_HZ, self.tick)
        if self.generates:
            self.create_timer(1.0 / params.APP_PACKET_RATE_HZ, self.observe)

        self.get_logger().info(
            f"{self.vehicle_id} routing as {role_name}, forwarding "
            f"{self.router.forwarding}, observations {self.generates}")

    # ------------------------------------------------------------- the clock
    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ---------------------------------------------------------- its own PX4
    def on_position(self, message: VehicleLocalPosition) -> None:
        """This vehicle's own estimate, in the frame the design is frozen in."""
        here = frames.px4_to_frozen((message.x, message.y, message.z), self.home)
        self.router.set_position(here)
        self.positions_seen += 1

    # ------------------------------------------------------------ the radio
    def on_rx(self, message: SwarmPacket) -> None:
        """Everything the radio delivered to this vehicle.

        A packet that will not decode is counted and dropped rather than
        raised. A raise here would take this vehicle's router down because
        somebody else emitted something malformed, which is a failure mode the
        radio is supposed to absorb.
        """
        incoming = codec.decode(message)
        if incoming is None:
            self.decode_failures += 1
            return
        self.router.on_rx(incoming, self.now_s())

    def publish(self) -> None:
        now = self.now_s()
        for outgoing in self.router.drain_tx(now):
            try:
                self.tx.publish(codec.encode(outgoing))
            except codec.CodecError as exc:
                # This node built the packet, so this is our defect and not the
                # radio's. Counted and reported rather than raised, because one
                # unencodable control packet must not end the run.
                self.encode_failures += 1
                self.get_logger().error(f"unencodable packet: {exc}")

    def tick(self) -> None:
        now = self.now_s()
        if self._last_tick is None:
            self._last_tick = now
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        self.router.tick(now, dt)
        self.publish()

    def observe(self) -> None:
        """One observation at the frozen rate. The router keeps it until acked."""
        self.router.observe(self.now_s())
        self.publish()

    # ---------------------------------------------------------- the ledger
    def ledger(self) -> dict:
        out = {
            "node": self.vehicle_id,
            "role": self.router.roles.role,
            "forwarding": self.router.forwarding,
            "elections_enabled": self.router.elections_enabled,
            "route_status": self.router.route_status(),
            "positions_seen": self.positions_seen,
            "decode_failures": self.decode_failures,
            "encode_failures": self.encode_failures,
        }
        out.update(self.router.observation_summary())
        return out

    def write_ledger(self) -> None:
        if not self.ledger_path:
            return
        payload = json.dumps(self.ledger(), indent=1, sort_keys=True)
        temporary = self.ledger_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.ledger_path)


def spin(node_factory, args=None) -> int:
    """Run a node until it is asked to stop, then let it write its ledger.

    Shared by this node and the ground station, because both of them have a
    file to write on the way down and both of them hit the same thing chunk 2.4
    found in the metrics collector: `rclpy.init` installs signal handlers that
    shut the context down before the exception reaches any `finally`, so the
    spin has to end on a flag instead.
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
            pass
    try:
        node = node_factory()
        while rclpy.ok() and not stopping:
            rclpy.spin_once(node, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            try:
                node.write_ledger()
            except OSError as exc:
                node.get_logger().error(f"the ledger did not write: {exc}")
            node.destroy_node()
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except (OSError, ValueError):
                pass
        if rclpy.ok():
            rclpy.shutdown()
    return 0


def main(args=None) -> int:
    return spin(RouterNode, args)


if __name__ == "__main__":
    raise SystemExit(main())
