"""One vehicle's role executive: it hears a grant, and it flies.

The election is not here. `uavx_comms.router.Router` opens epochs, ranks bids,
fixes the epoch owner and runs the make before break handback, every router
carries one, and chunk 3.1 proved it without a simulator. A second copy of any
of that would be a second answer to a question the run record then reports one
of.

This node is the half that was missing. A grant is a message; flying because
of one is a decision about an aircraft. It listens on its own rx endpoint,
keeps the grants addressed to it, tells its own mission executor where to be,
and writes what its role did.

**Why it holds a tx endpoint.** It sends one message and only one: the
`ROLE_ACK` for its own grant, and it sends it when the vehicle reaches the
slot rather than when the grant arrives. The router used to send that the
instant an `ASSIGN` was decoded, with the aircraft still 195 m away, and
nothing read the flag it set. Sent from here it means what its name says: the
node that was told to move has moved.

**Why the slot goes over a vehicle-local topic.** `RoleAssignment` on
`/<vehicle>/role_slot`, which is this vehicle's own namespace and carries no
`SwarmPacket`, so the seam rules allow it and rule 6 is not touched. The
alternative was a service between two swarm nodes, which the static pass
refuses, and rightly: a service is a private channel between two processes
that the graph snapshot cannot show carrying anything.

**What it does when the lease runs out.** Gives the role back and goes home,
with nothing to detect and no message to wait for. That is the entire recovery
from a dead epoch owner.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from px4_msgs.msg import VehicleLocalPosition
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from uavx_msgs.msg import RoleAssignment, SwarmPacket

from uavx_comms import codec, election
from uavx_comms import packet as pk
from uavx_comms.router_node import spin
from uavx_comms.simclock import ClockGate, Drain
from uavx_mission import frames, station as station_mod

from . import grant as gr
from . import trace as tr
from .flight import ARRIVAL_M, arrived, gap_m, where_to

NODE_NAME = "role_manager"

# The command is republished at this rate rather than latched. A mission
# executor that started a second later than this node would miss a latched
# message, and the vehicle would hold its old plan while the record said it
# had been sent somewhere.
COMMAND_HZ = 2.0

# Same profile as every other PX4 subscription in this workspace. A reliable
# one never matches the best effort publisher and the node waits forever for a
# position being published a metre away.
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

UNSET = float("nan")


def _described(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


class RoleManager(Node):
    """The role of one vehicle, and where that role puts the aircraft."""

    def __init__(self, node_name: str = NODE_NAME) -> None:
        super().__init__(node_name)

        self.declare_parameter("vehicle_id", "", _described(
            "which vehicle this is. Every endpoint is built from it and every "
            "grant is addressed by it."))
        self.declare_parameter("home_enu", [0.0, 0.0, 0.0], _described(
            "where the launcher put this vehicle, from the spawn manifest. "
            "PX4 reports relative to it and the frozen frame is not."))
        self.declare_parameter("station_enu", list(station_mod.unset()),
                               _described(
            "the point this vehicle holds when it is not the relay. Three "
            "NaNs for a vehicle whose work is a survey strip, which the "
            "mission executor owns."))
        self.declare_parameter("role", "survey", _described(
            "the role this vehicle starts and returns to."))
        self.declare_parameter("survey_peers", [], _described(
            "the vehicles the survey box is split between, in any order. "
            "Empty for a scenario with no survey. It decides which staying "
            "vehicle takes an elected relay's unflown strip, and that has to "
            "be exactly one of them or the work is flown twice."))
        self.declare_parameter("ledger_path", "", _described(
            "where to write what this vehicle's role did. Empty writes "
            "nothing."))

        vehicle_id = str(self.get_parameter("vehicle_id").value)
        if not vehicle_id:
            raise ValueError(
                "vehicle_id is required. A role manager that does not know "
                "which vehicle it is cannot tell its own grants from anybody "
                "else's, and it holds endpoints that have to be its own.")
        self.vehicle_id = vehicle_id

        role_name = str(self.get_parameter("role").value)
        if role_name not in ROLE_BY_NAME:
            raise ValueError(
                f"role {role_name!r} is not one of "
                f"{', '.join(sorted(ROLE_BY_NAME))}")
        self.home_role = ROLE_BY_NAME[role_name]

        self.home = tuple(float(v)
                          for v in self.get_parameter("home_enu").value)
        self.station = station_mod.station_of(
            self.get_parameter("station_enu").value)

        self.tracker = gr.GrantTracker(self.vehicle_id, self.home_role)
        self.trace = tr.RoleTrace(self.vehicle_id, role_name)
        self.ledger_path = str(self.get_parameter("ledger_path").value)

        swarm = "/uavx/" + self.vehicle_id
        px4 = "/" + self.vehicle_id + "/fmu"
        self.tx = self.create_publisher(SwarmPacket, swarm + "/tx", 50)
        self.create_subscription(SwarmPacket, swarm + "/rx", self.on_rx, 50)
        self.command = self.create_publisher(
            RoleAssignment, "/" + self.vehicle_id + "/role_slot", 10)
        # Chunk 4.7. Two vehicle-local topics carrying the survey handover.
        # The executor beside this process says what it is dropping on the
        # first, and this process tells it what to take on the second, having
        # heard the other half over the radio.
        self.inherit = self.create_publisher(
            RoleAssignment, "/" + self.vehicle_id + "/survey_inherit", 10)
        self.create_subscription(
            RoleAssignment, "/" + self.vehicle_id + "/survey_handed",
            self.on_handed, 10)
        self.create_subscription(
            VehicleLocalPosition, px4 + "/out/vehicle_local_position",
            self.on_position, PX4_QOS)

        # Nothing is acted on before the simulated clock is live. A
        # grant applied at a reading of zero carries a lease that has
        # already expired by the time the radio comes up, so the
        # vehicle would be granted the role and drop it in the same
        # instant. See uavx_comms.simclock.
        self.gate = ClockGate()
        self.shutdown_drain = Drain()
        self.position: Optional[tuple] = None
        self.positions_seen = 0
        self.decode_failures = 0
        self.encode_failures = 0
        self.commands_sent = 0
        self.acked_epoch: Optional[int] = None
        self._seq = 0
        self.surveyors = [str(v) for v
                          in self.get_parameter("survey_peers").value]
        # The first waypoint this vehicle's own executor gave up, waiting to
        # go out with the acknowledgement. One point, because RoleAssignment
        # carries one and the five messages are frozen; the vehicle that takes
        # the work rebuilds the rest of the plan from the box it already has.
        self.handed_point = None

        self.create_timer(1.0 / COMMAND_HZ, self.tick)

        self.get_logger().info(
            f"{self.vehicle_id} role manager, home role {role_name}, "
            f"station {self.station}")

    # ------------------------------------------------------------- the clock
    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ----------------------------------------------------------- its own PX4
    def on_position(self, message: VehicleLocalPosition) -> None:
        self.position = frames.px4_to_frozen(
            (message.x, message.y, message.z), self.home)
        self.positions_seen += 1

    # -------------------------------------------------------------- the radio
    def on_rx(self, message: SwarmPacket) -> None:
        """Role messages addressed to this vehicle, off its own rx endpoint.

        A packet that will not decode is counted and dropped rather than
        raised, for the reason the router gives: one malformed packet from
        somebody else must not take this vehicle's role manager down.
        """
        now = self.now_s()
        if not self.gate.sample(now):
            self.gate.hold(message)
            return
        self._deliver(message, now)

    def _deliver(self, message: SwarmPacket, now: float) -> None:
        incoming = codec.decode(message)
        if incoming is None:
            self.decode_failures += 1
            return
        if incoming.kind != pk.KIND_ROLE:
            return
        if isinstance(incoming.payload, dict)                 and incoming.payload.get("kind") == election.ROLE_ACK:
            self.hand_on(incoming.payload, now)
        outcome = self.tracker.apply(incoming.payload, now)
        if outcome == gr.ACCEPTED and self.tracker.grant is not None:
            held = self.tracker.grant
            self.trace.granted(held.epoch, held.slot, now)
            self.get_logger().info(
                f"{self.vehicle_id} granted the relay role for epoch "
                f"{held.epoch}, flying to {held.slot}")
            self.publish_command(now)
        elif outcome == gr.RENEWED:
            self.trace.renewed()
        elif outcome == gr.RELEASED:
            self.trace.released(now)
            self.get_logger().info(
                f"{self.vehicle_id} released, returning to {self.station}")
            self.publish_command(now)

    # ------------------------------------------------------- the survey work
    def on_handed(self, message: RoleAssignment) -> None:
        """This vehicle's executor giving up the rest of its strip."""
        point = tuple(float(v) for v in message.slot)
        if not all(v == v for v in point):
            return
        self.handed_point = point

    def takes_over(self, leaver: str) -> bool:
        """Whether this vehicle is the one to fly what `leaver` dropped.

        The lowest id of the surveyors that are still surveying. It has to be
        exactly one of them: nobody and the strip is abandoned, more than one
        and two aircraft fly the same lane at two altitudes, and both of those
        read as a successful handover in a run record that counts cells.
        """
        if self.home_role != election.ROLE_SURVEY:
            return False
        staying = sorted(v for v in self.surveyors if v != leaver)
        return bool(staying) and staying[0] == self.vehicle_id

    def hand_on(self, payload, now: float) -> None:
        """Somebody else's acknowledgement, carrying the work it gave up."""
        leaver = str(payload.get("node_id") or "")
        slot = payload.get("handover")
        if not leaver or leaver == self.vehicle_id or slot is None:
            return
        if self.trace.inherited_from is not None:
            return
        try:
            point = [float(v) for v in slot]
        except (TypeError, ValueError):
            return
        if len(point) != 3 or not all(v == v for v in point):
            return
        if not self.takes_over(leaver):
            return
        message = RoleAssignment()
        message.epoch = int(payload.get("epoch") or self.tracker.epoch)
        message.node_id = leaver
        message.role = int(election.ROLE_RELAY)
        message.slot = point
        message.sender_id = self.vehicle_id
        self.inherit.publish(message)
        self.trace.inherited(leaver, now)
        self.get_logger().info(
            f"{self.vehicle_id} taking over {leaver}'s strip from "
            f"{point[0]:.1f}, {point[1]:.1f}")

    # ---------------------------------------------------------- what it wants
    def target(self, now: float):
        return where_to(self.tracker.grant, self.station, now)

    def publish_command(self, now: float) -> None:
        """Tell this vehicle's own mission executor where to be."""
        target = self.target(now)
        held = self.tracker.grant
        message = RoleAssignment()
        message.epoch = int(self.tracker.epoch)
        message.node_id = self.vehicle_id
        message.role = int(held.role if held is not None else self.home_role)
        message.slot = [float(v) for v in (target if target is not None
                                           else (UNSET, UNSET, UNSET))]
        message.lease_expires_at = float(
            held.lease_expires_at if held is not None else 0.0)
        message.sender_id = self.vehicle_id
        self.command.publish(message)
        self.commands_sent += 1

    def send_ack(self, epoch: int, now: float) -> None:
        """Accept the grant, having actually arrived.

        The router used to send this the instant it decoded the assignment,
        which said only that a message had been received. Sent from here it
        says the aircraft is on the slot.
        """
        self._seq += 1
        body = {"kind": election.ROLE_ACK, "epoch": int(epoch),
                "node_id": self.vehicle_id}
        if self.handed_point is not None:
            # The work this vehicle gave up, riding out with the message that
            # says it has arrived. Sent here rather than the moment the
            # executor dropped it, because this is the packet the swarm was
            # already going to carry and a second one would be a new kind.
            body["handover"] = [float(v) for v in self.handed_point]
        outgoing = pk.control(self.vehicle_id, pk.KIND_ROLE, now, body,
                              sequence=self._seq)
        try:
            self.tx.publish(codec.encode(outgoing))
        except codec.CodecError as exc:
            self.encode_failures += 1
            self.get_logger().error(f"unencodable role ack: {exc}")
            return
        self.acked_epoch = int(epoch)

    # --------------------------------------------------------------- the tick
    def tick(self) -> None:
        now = self.now_s()
        if not self.gate.sample(now):
            return
        for held in self.gate.release():
            self._deliver(held, now)

        if self.tracker.expire(now):
            self.trace.lapsed(now)
            self.get_logger().warning(
                f"{self.vehicle_id} lease expired, giving the role back and "
                f"returning to {self.station}")

        target = self.target(now)
        held = self.tracker.grant
        if self.position is not None and arrived(self.position, target,
                                                 ARRIVAL_M):
            if held is not None and held.is_relay:
                self.trace.reached_slot(now)
                if self.acked_epoch != held.epoch:
                    self.send_ack(held.epoch, now)
            else:
                self.trace.returned_home(now)

        self.publish_command(now)

    # ------------------------------------------------------------- the ledger
    def ledger(self) -> dict:
        now = self.now_s()
        target = self.target(now)
        gap = gap_m(self.position, target) if self.position else None
        out = self.trace.as_record()
        out.update({
            "station": list(self.station) if self.station else None,
            "target": list(target) if target else None,
            "target_gap_m": None if gap is None else round(gap, 3),
            "positions_seen": self.positions_seen,
            "commands_sent": self.commands_sent,
            "decode_failures": self.decode_failures,
            "encode_failures": self.encode_failures,
            "acked_epoch": self.acked_epoch,
            "arrival_radius_m": ARRIVAL_M,
        })
        out.update(self.gate.as_record())
        out.update(self.shutdown_drain.as_record())
        out["grants_seen"] = self.tracker.as_record()
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


def main(args=None) -> int:
    return spin(RoleManager, args)


if __name__ == "__main__":
    raise SystemExit(main())
