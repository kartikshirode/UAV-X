"""The pure mirror of uavx_msgs/msg/SwarmPacket, and what identity means.

The wire type is a ROS message and importing it would make every test in this
package need a built workspace before it could report. So the pure logic
carries a dataclass with the same fields in the same order and the same
constant values, and test/test_packet_contract.py parses the frozen .msg file
and fails if the two ever disagree. W3 converts one to the other and adds
nothing.

`(origin_id, sequence)` is the identity, and deduplication at the destination
is on that pair and nothing else. That is what makes "delivered once" a set
comparison rather than a count, which is the distinction RFC 9171 draws for
exactly this problem: identity is source plus creation sequence, and delivery
is reported against that identity rather than counted as transmissions.
"""

from dataclasses import dataclass, field, replace
from typing import Any, List, Tuple

from . import params

# Values are the ones frozen in SwarmPacket.msg, so a bag or a log can be read
# without importing the package that wrote it.
KIND_OBSERVATION = 0
KIND_HELLO = 1
KIND_LSA = 2
KIND_ROLE = 3
KIND_ACK = 4

KIND_NAMES = {
    KIND_OBSERVATION: "OBSERVATION",
    KIND_HELLO: "HELLO",
    KIND_LSA: "LSA",
    KIND_ROLE: "ROLE",
    KIND_ACK: "ACK",
}

# Observations queue behind control. Everything else is control and is always
# served first, because a backlog sitting in front of the traffic that would
# end the outage makes the swarm slower to recover the harder it was working.
CONTROL_KINDS = frozenset({KIND_HELLO, KIND_LSA, KIND_ROLE, KIND_ACK})

# Field order is part of the interface, so it is written down as an ordered
# tuple rather than left to dataclass introspection to imply.
PACKET_FIELDS = (
    "origin_id",
    "sequence",
    "created_at",
    "expires_at",
    "kind",
    "dest_id",
    "hop_count",
    "path",
    "payload",
)


@dataclass
class Packet:
    """One SwarmPacket, decoded.

    `payload` is the decoded body rather than the uint8 array on the wire.
    An observation carries bytes and its length is the frozen observation
    size; a control packet carries a small mapping that W3 serializes. The
    pure logic never inspects an observation payload, so nothing here depends
    on the encoding W3 chooses.
    """

    origin_id: str
    sequence: int
    created_at: float
    expires_at: float
    kind: int
    dest_id: str = ""
    hop_count: int = 0
    path: List[str] = field(default_factory=list)
    payload: Any = None

    def identity(self) -> Tuple[str, int]:
        return (self.origin_id, self.sequence)

    def identity_str(self) -> str:
        """The spelling the run record uses in generated_ids and delivered_ids."""
        return "{0}:{1}".format(self.origin_id, self.sequence)

    def is_control(self) -> bool:
        return self.kind in CONTROL_KINDS

    def is_broadcast(self) -> bool:
        return self.dest_id == ""

    def copy(self, **changes: Any) -> "Packet":
        """A detached copy. `path` is a list, so it is rebuilt rather than shared.

        A forwarder that mutated a shared list would let one node's decision
        rewrite the history another node already transmitted, and the delivered
        path is the evidence behind handback.prepared_path.
        """
        out = replace(self, **changes)
        out.path = list(out.path)
        return out

    def wire_size(self) -> int:
        """Payload bytes on the wire, for the queue and budget arithmetic."""
        if isinstance(self.payload, (bytes, bytearray)):
            return len(self.payload)
        return 0


def observation(origin_id: str, sequence: int, created_at: float) -> Packet:
    """A new observation, addressed to the ground station and nowhere else."""
    return Packet(
        origin_id=origin_id,
        sequence=sequence,
        created_at=created_at,
        expires_at=created_at + params.OBSERVATION_LIFETIME_S,
        kind=KIND_OBSERVATION,
        dest_id=params.GCS_ID,
        hop_count=0,
        path=[origin_id],
        payload=bytes(params.OBSERVATION_BYTES),
    )


def control(sender_id: str, kind: int, created_at: float, payload: Any,
            dest_id: str = "", sequence: int = 0) -> Packet:
    """A control packet. Sequence is per sender, from the caller's counter."""
    if kind not in CONTROL_KINDS:
        raise ValueError("control() got a non-control kind: {0}".format(kind))
    return Packet(
        origin_id=sender_id,
        sequence=sequence,
        created_at=created_at,
        expires_at=created_at + params.OBSERVATION_LIFETIME_S,
        kind=kind,
        dest_id=dest_id,
        hop_count=0,
        path=[sender_id],
        payload=payload,
    )
