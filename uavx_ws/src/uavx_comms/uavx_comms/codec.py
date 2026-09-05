"""One packet, in two forms, and the only place that knows both.

`packet.Packet` is what the routing and election logic reasons about, and it
exists as a dataclass so those 91 tests can run on a checkout with nothing
built. `uavx_msgs/msg/SwarmPacket` is what crosses the tx and rx seam. Week 3
is where the two meet, and this module is the entire meeting: nothing else in
the workspace converts between them, so there is one answer to what a field
means on the wire rather than one answer per node.

Two things here are decisions rather than transcription.

**The payload.** On the wire it is `uint8[]` and nothing else, because
`SwarmPacket.msg` is frozen and a second field for control bodies would be a
change to the message contract. An observation already carries bytes, so it
goes across as itself. A control packet carries a small mapping that the router
built, so it goes across as compact UTF-8 JSON. The kind decides which, and
the kind is on the wire, so a reader needs nothing this module has not sent it.

**A decode that fails is a dropped packet and never an exception.** The link
layer runs inside the callback that received the message, and a raise there
takes the radio down for the whole swarm because one node emitted something
malformed. `decode` returns None and the caller counts it. Round 4's finding
about checkers that had never been shown to fail applies here too, so
test_codec.py drives every one of these paths.
"""

from __future__ import annotations

import json
from typing import Optional

from uavx_msgs.msg import SwarmPacket

from . import packet as pk

# Bounds the wire type imposes, checked on the way out rather than discovered
# by rclpy as a serialization error with no packet in the message.
UINT8_MAX = 0xFF
UINT32_MAX = 0xFFFFFFFF

# JSON, with no spaces after its separators. The observation size is frozen at
# 256 bytes and control bodies are small, but a mapping that grew without
# anybody noticing would still fit inside a radio that models no bandwidth
# limit, so the encoder refuses one larger than an observation instead.
CONTROL_PAYLOAD_MAX_BYTES = 1024
_JSON_SEPARATORS = (",", ":")


class CodecError(ValueError):
    """A packet that cannot be put on the wire, naming which field and why.

    Raised only by `encode`, which is called by the node that built the packet
    and is therefore the node that can be fixed. `decode` never raises.
    """


def _payload_bytes(source: pk.Packet) -> bytes:
    if source.kind == pk.KIND_OBSERVATION:
        body = source.payload
        if not isinstance(body, (bytes, bytearray)):
            raise CodecError(
                f"observation {source.identity_str()} carries "
                f"{type(body).__name__} and an observation payload is bytes")
        return bytes(body)
    try:
        text = json.dumps(source.payload, separators=_JSON_SEPARATORS,
                          sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CodecError(
            f"the {pk.KIND_NAMES.get(source.kind, source.kind)} body from "
            f"{source.origin_id} is not JSON: {exc}") from exc
    body = text.encode("utf-8")
    if len(body) > CONTROL_PAYLOAD_MAX_BYTES:
        raise CodecError(
            f"a {pk.KIND_NAMES.get(source.kind, source.kind)} body of "
            f"{len(body)} bytes is over the {CONTROL_PAYLOAD_MAX_BYTES} byte "
            f"limit; control packets are small by design and one this size "
            f"means a structure is being sent that should be summarised")
    return body


def encode(source: pk.Packet) -> SwarmPacket:
    """A Packet as the message that crosses the seam.

    Every field the wire type constrains is checked here, because the
    alternative is rclpy refusing the publish with a message that names a
    C array and not the packet that filled it.
    """
    if source.kind not in pk.KIND_NAMES:
        raise CodecError(f"kind {source.kind!r} is not one of the five "
                         f"SwarmPacket constants")
    if not isinstance(source.sequence, int) or isinstance(source.sequence, bool):
        raise CodecError(f"sequence is {source.sequence!r}, not an integer")
    if not 0 <= source.sequence <= UINT32_MAX:
        raise CodecError(f"sequence {source.sequence} does not fit the uint32 "
                         f"the message declares")
    if not 0 <= source.hop_count <= UINT8_MAX:
        raise CodecError(f"hop_count {source.hop_count} does not fit the uint8 "
                         f"the message declares. The TTL should have dropped "
                         f"this packet long before it got here")
    if not isinstance(source.origin_id, str) or not source.origin_id:
        raise CodecError("origin_id is empty, and identity is "
                         "(origin_id, sequence)")
    if any(not isinstance(hop, str) or not hop for hop in source.path):
        raise CodecError(f"path {source.path!r} holds an empty or non-string "
                         f"hop, and the delivered path is evidence")

    message = SwarmPacket()
    message.origin_id = source.origin_id
    message.sequence = int(source.sequence)
    message.created_at = float(source.created_at)
    message.expires_at = float(source.expires_at)
    message.kind = int(source.kind)
    message.dest_id = source.dest_id or ""
    message.hop_count = int(source.hop_count)
    message.path = [str(hop) for hop in source.path]
    message.payload = list(_payload_bytes(source))
    return message


def decode(message: SwarmPacket) -> Optional[pk.Packet]:
    """The message as a Packet, or None if it is not one.

    None rather than a raise. This runs inside the link layer's receive
    callback, and one node emitting something malformed must not take the
    radio down for the swarm. The caller counts what it drops, so a decode
    that starts failing shows up as a number in the run record rather than as
    silence.
    """
    try:
        kind = int(message.kind)
        if kind not in pk.KIND_NAMES:
            return None
        origin = str(message.origin_id)
        if not origin:
            return None
        body = bytes(bytearray(message.payload))
        if kind == pk.KIND_OBSERVATION:
            payload = body
        else:
            payload = json.loads(body.decode("utf-8"))
        return pk.Packet(
            origin_id=origin,
            sequence=int(message.sequence),
            created_at=float(message.created_at),
            expires_at=float(message.expires_at),
            kind=kind,
            dest_id=str(message.dest_id),
            hop_count=int(message.hop_count),
            path=[str(hop) for hop in message.path],
            payload=payload,
        )
    except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
        return None
