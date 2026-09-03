"""The pure Packet has to be the frozen SwarmPacket, or W3 converts a lie.

This package deliberately does not import uavx_msgs: a pure-logic test that
needs a colcon overlay before it can report is no use while the logic is being
written. The cost of that choice is a second definition of the wire type, and
the only thing that makes a second definition safe is a test that reads the
first one and fails on any difference.

So this parses uavx_ws/src/uavx_msgs/msg/SwarmPacket.msg out of the repository
and compares it, rather than restating what it is expected to contain. Field
order is part of the interface and the constant values are what let a bag be
read without importing the package that wrote it, so both are checked.
"""

import re
from dataclasses import fields
from pathlib import Path

from uavx_comms import packet as pk

MSG = (Path(__file__).resolve().parents[2] / "uavx_msgs" / "msg"
       / "SwarmPacket.msg")


def parse_msg(path):
    """Field names in order, and constants by name, from a .msg file."""
    names, constants = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        constant = re.match(r"^\S+\s+([A-Z0-9_]+)\s*=\s*(\S+)$", line)
        if constant:
            constants[constant.group(1)] = int(constant.group(2))
            continue
        parts = line.split()
        if len(parts) >= 2:
            names.append(parts[1])
    return names, constants


def test_the_frozen_message_file_is_actually_there():
    assert MSG.is_file(), (
        "SwarmPacket.msg is missing at " + str(MSG) + ". Without it this suite "
        "compares the pure Packet with nothing and passes.")


def test_field_names_and_order_match_the_frozen_message():
    names, _ = parse_msg(MSG)
    assert tuple(names) == pk.PACKET_FIELDS
    assert tuple(f.name for f in fields(pk.Packet)) == pk.PACKET_FIELDS


def test_kind_constants_carry_the_frozen_numeric_values():
    _, constants = parse_msg(MSG)
    assert constants == {
        "OBSERVATION": pk.KIND_OBSERVATION,
        "HELLO": pk.KIND_HELLO,
        "LSA": pk.KIND_LSA,
        "ROLE": pk.KIND_ROLE,
        "ACK": pk.KIND_ACK,
    }


def test_identity_is_origin_and_sequence_and_nothing_else():
    a = pk.observation("uav_4", 7, 100.0)
    b = pk.observation("uav_4", 7, 900.0)
    b.path = ["uav_4", "uav_3", "uav_1", "gcs"]
    b.hop_count = 2
    assert a.identity() == b.identity()
    assert a.identity_str() == "uav_4:7"

    # A different origin with the same sequence is a different observation.
    # Getting this wrong makes two vehicles' data collide at the ground station
    # and reports half of it as duplicates.
    assert pk.observation("uav_3", 7, 100.0).identity() != a.identity()


def test_an_observation_carries_the_frozen_payload_size():
    obs = pk.observation("uav_3", 1, 0.0)
    assert obs.wire_size() == pk.params.OBSERVATION_BYTES
    assert obs.expires_at == obs.created_at + pk.params.OBSERVATION_LIFETIME_S


def test_a_copy_does_not_share_its_path_with_the_original():
    # Each forwarder appends to `path`, and the delivered path is the evidence
    # behind the handback trace. A shared list would let one node's forwarding
    # decision rewrite the history another node already transmitted.
    original = pk.observation("uav_4", 1, 0.0)
    forwarded = original.copy(hop_count=1)
    forwarded.path.append("uav_3")
    assert original.path == ["uav_4"]


def test_control_refuses_to_build_an_observation():
    try:
        pk.control("uav_1", pk.KIND_OBSERVATION, 0.0, {})
    except ValueError:
        return
    raise AssertionError("control() built an OBSERVATION, so the two queues "
                         "could be fed from the same call site")
