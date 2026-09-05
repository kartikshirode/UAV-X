"""The radio, as the one process allowed to see where everybody is.

stage-1/architecture.md section 1 puts this node and the metrics collector
outside the swarm, and that is why they may read simulator ground truth: they
represent physics and the observer. Every other process is inside the radio and
may name only its own endpoints. scripts/check_seam.sh enforces both halves,
and this file is named in the exemption by its real ament path.

What it does, once per message that any node transmits: decode it, work out how
far every other node is from the sender, ask the frozen link model whether that
one hop delivers, and put a copy on the receiver's rx topic after the frozen hop
latency. Nothing here knows what a route is. A transmitting node has already
appended its chosen next hop to `path`; everybody in range hears the
transmission and the routers decide what to do with it, which is what a radio
actually does and what makes the delivered path evidence.

Three decisions worth naming.

**Positions come from ground truth, not from the packets.** A node's own idea of
where its neighbours are is stale by up to a HELLO period, and after a split it
is as stale as the split. The radio is physics: it uses where the vehicles
actually are. The ground station is not a gazebo model and never moves, so its
position is the frozen origin.

**Latency is modelled with a queue rather than a sleep.** Every delivery is
stamped with the arrival time the frozen hop latency implies and released by a
timer. Sleeping inside the callback would stall every other delivery behind it,
and `queue_drain` asserts that control traffic is not delayed behind a backlog,
so the harness must not invent a delay the design does not have.

**A packet that will not decode is counted and dropped.** One node emitting
something malformed must not take the radio down for the swarm, so `decode`
returns None and the count reaches the run record through the ledger.
"""

from __future__ import annotations

import json
import math
import os
import signal
from typing import Dict, List, Optional, Tuple

import rclpy
from gazebo_msgs.msg import ModelStates
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from uavx_msgs.msg import SwarmPacket

from . import codec, link, params

NODE_NAME = "link_layer"
GROUND_TRUTH_TOPIC = "/gazebo/model_states"

# How often the delivery queue is drained. Well under the frozen hop latency,
# so a delivery lands in the tick after the one it was due in rather than a
# whole period late.
DRAIN_PERIOD_S = 0.005

# The ground station sits at the origin of the frozen frame and does not move.
# architecture.md section 6, the common geometry table.
GCS_POSITION = (0.0, 0.0, 0.0)


def _described(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


class LinkLayerNode(Node):
    """One radio for the whole swarm, and the only reader of where they are."""

    def __init__(self) -> None:
        super().__init__(NODE_NAME)

        self.declare_parameter("vehicles", [""], _described(
            "every vehicle id in this scenario. The ground station is added "
            "by this node and must not be listed."))
        self.declare_parameter("model_map", [""], _described(
            "gazebo model name to vehicle id, one `iris_0=uav_1` per entry, "
            "from the launcher's spawn manifest."))
        self.declare_parameter("seed", 0, _described(
            "the scenario's seed. The fade band is random and a run nobody "
            "can replay is not evidence."))
        self.declare_parameter("radio_off", [""], _described(
            "vehicles whose radio starts gated, for a scenario that begins "
            "in a blackout rather than injecting one."))
        self.declare_parameter("ledger_path", "", _described(
            "where to write what this radio did. Empty writes nothing."))

        vehicles = [str(v) for v in self.get_parameter("vehicles").value
                    if str(v).strip()]
        if not vehicles:
            raise ValueError(
                "the link layer was given no vehicles, so there is nobody to "
                "carry traffic between and every delivery ratio it reported "
                "would be a division by nothing")
        if params.GCS_ID in vehicles:
            raise ValueError(
                f"{params.GCS_ID} is in the vehicle list. The ground station "
                f"is a node in the graph and is not a vehicle; this node adds "
                f"it, and listing it twice would give it two rx topics")
        self.vehicles = tuple(vehicles)
        self.nodes = tuple(vehicles) + (params.GCS_ID,)

        self.model_map = self._model_map()
        missing = [v for v in self.vehicles if v not in self.model_map.values()]
        if missing:
            raise ValueError(
                f"no gazebo model is mapped to {', '.join(missing)}, so the "
                f"radio cannot tell where they are and every link involving "
                f"them would be scored at an invented distance")

        self.model = link.LinkModel(
            seed=int(self.get_parameter("seed").value),
            radio_off=[str(v) for v
                       in self.get_parameter("radio_off").value
                       if str(v).strip()])
        self.ledger_path = str(self.get_parameter("ledger_path").value)

        self.positions: Dict[str, Tuple[float, float, float]] = {
            params.GCS_ID: GCS_POSITION}
        # Deliveries waiting out the hop latency, as (due_s, receiver, message).
        self._pending: List[Tuple[float, str, SwarmPacket]] = []

        self.transmissions = 0
        self.undecodable = 0
        self.deliveries = 0
        self.dropped_no_position = 0
        self.by_pair: Dict[str, int] = {}

        # One rx publisher per node, and one tx subscription per node. This is
        # the whole endpoint list seam_manifests.json allows /link_layer, and
        # it is built from the vehicle list rather than written out, so a
        # scenario with different vehicles needs no edit here.
        self.rx: Dict[str, object] = {}
        for node_id in self.nodes:
            self.rx[node_id] = self.create_publisher(
                SwarmPacket, self._endpoint(node_id, "rx"), 50)
        for node_id in self.nodes:
            self.create_subscription(
                SwarmPacket, self._endpoint(node_id, "tx"),
                self._on_tx(node_id), 50)

        self.create_subscription(ModelStates, GROUND_TRUTH_TOPIC,
                                 self.on_model_states, 10)
        self.create_timer(DRAIN_PERIOD_S, self.drain)

        self.get_logger().info(
            f"radio up for {', '.join(self.nodes)}, seed {self.model.seed}, "
            f"r_full {params.R_FULL_M:.0f} m, r_max {params.R_MAX_M:.0f} m")

    # ------------------------------------------------------------ endpoints
    @staticmethod
    def _endpoint(node_id: str, direction: str) -> str:
        """The one place a swarm endpoint is spelled, for every node.

        Built rather than written out. The static seam pass counts distinct
        vehicle endpoint literals per file and calls a file naming two of them
        a bypass; this file is exempt because it is the radio, and building
        the strings keeps the exemption from being load bearing.
        """
        return "/uavx/" + node_id + "/" + direction

    def _model_map(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for entry in self.get_parameter("model_map").value or []:
            text = str(entry).strip()
            if not text:
                continue
            if "=" not in text:
                raise ValueError(
                    f"model_map entry {text!r} is not `model=vehicle_id`")
            model, vehicle = text.split("=", 1)
            model, vehicle = model.strip(), vehicle.strip()
            if not model or not vehicle:
                raise ValueError(f"model_map entry {text!r} has an empty side")
            out[model] = vehicle
        if not out:
            raise ValueError(
                "model_map is empty, so no gazebo model is known to be a "
                "vehicle and every pose would be scenery")
        return out

    # ----------------------------------------------------------- the world
    def on_model_states(self, message: ModelStates) -> None:
        for name, pose in zip(message.name, message.pose):
            vehicle = self.model_map.get(str(name))
            if vehicle is not None:
                self.positions[vehicle] = (pose.position.x, pose.position.y,
                                           pose.position.z)

    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    # ------------------------------------------------------- the radio
    def _on_tx(self, sender: str):
        def handler(message: SwarmPacket) -> None:
            self.transmit(sender, message)
        return handler

    def transmit(self, sender: str, message: SwarmPacket) -> None:
        """One transmission, offered to every other node in the scenario."""
        self.transmissions += 1
        if codec.decode(message) is None:
            self.undecodable += 1
            return
        here = self.positions.get(sender)
        if here is None:
            self.dropped_no_position += 1
            return
        due = self.now_s() + params.HOP_LATENCY_S
        for receiver in self.nodes:
            if receiver == sender:
                continue
            there = self.positions.get(receiver)
            if there is None:
                self.dropped_no_position += 1
                continue
            distance = link.distance(here, there)
            if not math.isfinite(distance):
                self.dropped_no_position += 1
                continue
            if not self.model.deliver(sender, receiver, distance):
                continue
            self.deliveries += 1
            pair = sender + "->" + receiver
            self.by_pair[pair] = self.by_pair.get(pair, 0) + 1
            self._pending.append((due, receiver, message))

    def drain(self) -> None:
        """Release every delivery whose hop latency has elapsed."""
        if not self._pending:
            return
        now = self.now_s()
        keep: List[Tuple[float, str, SwarmPacket]] = []
        for due, receiver, message in self._pending:
            if due <= now:
                self.rx[receiver].publish(message)
            else:
                keep.append((due, receiver, message))
        self._pending = keep

    # ---------------------------------------------------------- the ledger
    def ledger(self) -> dict:
        """What this radio did, for the run record to read off disk.

        Not a topic. seam_manifests.json gives /link_layer the rx and tx
        endpoints and ground truth and nothing else, and a metrics topic here
        would be a channel the seam pass would rightly call a bypass.
        """
        return {
            "node": NODE_NAME,
            "seed": self.model.seed,
            "nodes": list(self.nodes),
            "transmissions": self.transmissions,
            "deliveries": self.deliveries,
            "undecodable": self.undecodable,
            "dropped_no_position": self.dropped_no_position,
            "radio_draws": self.model.draws,
            "radio_delivered": self.model.delivered,
            "radio_dropped": self.model.dropped,
            "radio_off": sorted(self.model.radio_off),
            "absent": sorted(self.model.absent),
            "deliveries_by_pair": dict(sorted(self.by_pair.items())),
            "still_pending": len(self._pending),
        }

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
    """Sample until asked to stop, then write the ledger that counts.

    The signal handling is the same fix chunk 2.4 made in the metrics
    collector. `rclpy.init` installs handlers that shut the context down before
    the exception reaches any `finally`, so anything written there runs against
    a dead context. Here the ledger is a file rather than a publish, so it would
    survive, but the node still has to stop cleanly rather than by exception.
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
        node = LinkLayerNode()
        while rclpy.ok() and not stopping:
            rclpy.spin_once(node, timeout_sec=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            try:
                node.write_ledger()
            except OSError as exc:
                node.get_logger().error(f"the radio ledger did not write: {exc}")
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
