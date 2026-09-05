"""The ground station: a node in the graph, reachable only through the radio.

Round 2 of the reviews found that leaving the ground station out of the
topology invited an implementer to wire it straight to every router, and a
delivery ratio measured over a direct wire is a measurement of nothing. So it
is an ordinary node. It holds `/uavx/gcs/tx` and `/uavx/gcs/rx`, it sits at the
origin of the frozen frame, and `uav_4` is 484.6 m away from it, well past the
range at which any link exists. Everything that reaches it came over hops the
link model actually drew.

It runs the same `Router` the vehicles run, with `node_id` equal to the
destination, which is what makes it the node that accepts and deduplicates.
There is no second implementation of acceptance here, because a ground station
with its own idea of what delivery means would be the only opinion that
mattered and nothing would contradict it.

Everything it concludes from what it accepted lives in `ledger.py`, which
imports no ROS, so the arithmetic is provable on a checkout with nothing built.
A simulator run is the most expensive way there is to find a division in the
wrong place.

**The ledger is a file and not a topic.** `seam_manifests.json` gives this node
two endpoints. A metrics topic would be a third, and the seam pass would be
right to call it a bypass, so what the run record needs is written to disk and
the runner reads it. `delivered_ids` is the set the delivery claim rests on,
not a count: an implementation that forwarded the same packet twice and one
that delivered two different packets produce the same count and different sets.
"""

from __future__ import annotations

import json
import os

from rcl_interfaces.msg import ParameterDescriptor
from rclpy.node import Node
from uavx_msgs.msg import SwarmPacket

from uavx_comms import codec, election, params
from uavx_comms.router import Router
from uavx_comms.router_node import spin

from . import ledger as led

NODE_NAME = "gcs_node"

# The router's timers are the frozen protocol periods and the shortest is the
# HELLO period, so this is well inside it. The ground station generates no
# observations, so nothing else here is rate sensitive.
TICK_HZ = 20.0

# architecture.md section 6, the common geometry table: the ground station is
# at the origin and does not move. It is not a gazebo model and has no PX4.
GCS_POSITION = (0.0, 0.0, 0.0)


def _described(text: str) -> ParameterDescriptor:
    return ParameterDescriptor(description=text)


class GcsNode(Node):
    """The destination, and the only place delivery is decided."""

    def __init__(self) -> None:
        super().__init__(NODE_NAME)

        self.declare_parameter("forwarding", True, _described(
            "false is the direct_only control. The ground station forwards "
            "nothing in either case, and the flag is carried so the whole "
            "graph is configured the same way rather than by exception."))
        self.declare_parameter("ledger_path", "", _described(
            "where to write the delivered set. Empty writes nothing, which is "
            "only useful for a run nobody is going to measure."))

        self.ledger_path = str(self.get_parameter("ledger_path").value)
        self.router = Router(
            node_id=params.GCS_ID,
            position=GCS_POSITION,
            role=election.ROLE_GCS_ANCHOR,
            forwarding=bool(self.get_parameter("forwarding").value),
            elections_enabled=False,
        )

        # The whole endpoint list. Written as literals rather than built,
        # because unlike a vehicle there is exactly one ground station and its
        # id is frozen; the static seam pass counts one distinct vehicle
        # endpoint in this file, which is what it should count.
        self.tx = self.create_publisher(SwarmPacket, "/uavx/gcs/tx", 50)
        self.create_subscription(SwarmPacket, "/uavx/gcs/rx", self.on_rx, 50)

        self._last_tick = None
        self.decode_failures = 0
        self.encode_failures = 0

        self.create_timer(1.0 / TICK_HZ, self.tick)
        self.get_logger().info(
            f"ground station up at the frozen origin, accepting for "
            f"{params.GCS_ID}")

    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_rx(self, message: SwarmPacket) -> None:
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

    # ---------------------------------------------------------- the ledger
    def ledger(self) -> dict:
        out = {
            "node": params.GCS_ID,
            "forwarding": self.router.forwarding,
            "decode_failures": self.decode_failures,
            "encode_failures": self.encode_failures,
            "delivered_hops_by_node":
                led.delivered_hops_by_node(self.router.accepted_hops),
            "delivered_edges_by_node":
                led.delivered_edges_by_node(self.router.accepted_path),
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


def main(args=None) -> int:
    return spin(GcsNode, args)


if __name__ == "__main__":
    raise SystemExit(main())
