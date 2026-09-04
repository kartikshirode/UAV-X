"""Chunk 1.1: do the five types resolve, and does SwarmPacket carry identity.

That is the question plan.md asks of this chunk, word for word, and until
4 September nothing in this package answered it. uavx_msgs is an interface
package, so colcon built it, generated the bindings and reported zero tests,
and gate_test called that a pass. The week 2 fix to gate_test refuses a
package that reports no tests, and the first full run of the week 1 gate
failed here, on the very first chunk, for exactly that reason.

scripts/check_message_contract.py compares the generated interfaces against
the fenced blocks in architecture.md section 1b line for line, and that is
where the field order and the constants are held to the document. This file
does not repeat that. It asks two narrower things of the Python bindings
themselves, which are what every node in this repository actually imports:

1. all five types import from uavx_msgs.msg, so a node that names one of
   them will start;
2. SwarmPacket carries (origin_id, sequence) with the frozen types, since
   the GCS deduplicates on that pair and nothing else, and a widened,
   renamed or reordered field would silently change what delivered once
   means.

    colcon test --packages-select uavx_msgs

Needs the overlay built, because the bindings do not exist until rosidl
generates them. There is no source tree fallback for a generated module.
"""

import pytest

from uavx_msgs.msg import Hello, LinkState, RoleAssignment, RunMetrics, SwarmPacket

# The order is the contract. rosidl's Python bindings report float64 as
# double and an unbounded array as sequence<T>, so this is what the frozen
# block in architecture.md section 1b looks like from the importing side.
SWARM_PACKET_FIELDS = {
    "origin_id": "string",
    "sequence": "uint32",
    "created_at": "double",
    "expires_at": "double",
    "kind": "uint8",
    "dest_id": "string",
    "hop_count": "uint8",
    "path": "sequence<string>",
    "payload": "sequence<uint8>",
}

KINDS = {"OBSERVATION": 0, "HELLO": 1, "LSA": 2, "ROLE": 3, "ACK": 4}


def test_all_five_types_resolve():
    for message in (Hello, LinkState, RoleAssignment, RunMetrics, SwarmPacket):
        instance = message()
        assert instance is not None
        assert message.get_fields_and_field_types()


def test_swarm_packet_fields_in_frozen_order():
    actual = SwarmPacket.get_fields_and_field_types()
    assert list(actual) == list(SWARM_PACKET_FIELDS)
    assert actual == SWARM_PACKET_FIELDS


def test_identity_pair_is_origin_and_sequence():
    packet = SwarmPacket(origin_id="uav_3", sequence=7)
    assert (packet.origin_id, packet.sequence) == ("uav_3", 7)
    types = SwarmPacket.get_fields_and_field_types()
    assert types["origin_id"] == "string"
    assert types["sequence"] == "uint32"


def test_sequence_is_unsigned_32_bit():
    # A gap in the sequence has to be visible, and a signed or narrower
    # field would let a wrapped or negative counter pass as a valid id.
    SwarmPacket(sequence=2**32 - 1)
    with pytest.raises(AssertionError):
        SwarmPacket(sequence=-1)
    with pytest.raises(AssertionError):
        SwarmPacket(sequence=2**32)


def test_kind_constants_match_the_frozen_values():
    for name, value in KINDS.items():
        assert getattr(SwarmPacket, name) == value
    assert len({getattr(SwarmPacket, n) for n in KINDS}) == len(KINDS)


def test_relayed_packet_keeps_its_origin_and_grows_its_path():
    # The field semantics the routing layer depends on: origin is the first
    # producer, and path is appended by each forwarder in order.
    packet = SwarmPacket(origin_id="uav_4", sequence=1, path=["uav_4"])
    packet.path.append("uav_3")
    packet.hop_count += 1
    assert packet.origin_id == "uav_4"
    assert list(packet.path) == ["uav_4", "uav_3"]
    assert packet.hop_count == 1
