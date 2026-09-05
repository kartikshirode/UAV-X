"""Chunk 4.1: turning a role grant into a place to be.

Two lines of `architecture.md` section 4 become an aircraft moving:

    The winner becomes RELAY and flies to the slot.
    The relay reverts to SURVEY and returns to its station or its unfinished
    survey work.

Everything between those two sentences is arithmetic on points and it is here
rather than in a node, so a test can ask where a vehicle should be without
starting a simulator.

The rule the module exists to hold is that a vehicle always has somewhere to
be. A grant that lapses does not leave an aircraft with no destination in the
middle of a reserved band; it leaves it with the destination it had before,
which is the whole reason the lease is safe to expire.

    where_to      the point this vehicle should be flying to, now
    arrived       whether it is there
    ARRIVAL_M     how close counts, and why it is not tighter
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

Point = Tuple[float, float, float]

# Within this of a commanded point, the vehicle is there. The station ingress
# gate in the scenario runner uses 5 m for the same reason: PX4 position hold
# in SITL settles inside a couple of tenths of a metre, and the number that
# matters is small against the 15 m slot clearance rather than tight against
# the controller. A metre would report a vehicle as still flying while it sat
# on its point, and the run would wait for an arrival that had happened.
ARRIVAL_M = 5.0


class FlightError(ValueError):
    """A vehicle with no destination, which is a state the design forbids."""


def _point(raw, what: str) -> Point:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise FlightError(f"{what} is {raw!r} and must be three numbers")
    out = []
    for value in raw:
        if isinstance(value, bool):
            raise FlightError(f"{what} is {raw!r}; a flag is not a coordinate")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise FlightError(
                f"{what} is {raw!r} and must be three numbers") from None
        if not math.isfinite(number):
            raise FlightError(f"{what} is {raw!r} and must be finite")
        out.append(number)
    return (out[0], out[1], out[2])


def where_to(grant, home: Optional[Point], now: float) -> Optional[Point]:
    """Where this vehicle should be flying to.

    `home` is what it was doing before any of this: its station, or None for a
    vehicle whose work is a survey strip the mission executor owns. A live
    relay grant with a slot overrides it; anything else gives it back.

    Returning None is a real answer and it means the mission executor keeps
    the plan it already has. It is not the same as having nowhere to be.
    """
    if grant is not None and grant.is_relay and grant.live_at(now):
        if grant.slot is None:
            raise FlightError(
                f"{grant.node_id} holds the relay role for epoch "
                f"{grant.epoch} and the grant names no slot. A vehicle told "
                f"to be the relay and not told where would fly to wherever it "
                f"happened to be")
        return _point(grant.slot, "the granted slot")
    if home is None:
        return None
    return _point(home, "the vehicle's own station")


def arrived(position, target: Optional[Point],
            tolerance: float = ARRIVAL_M) -> bool:
    """Whether the vehicle is at the point it was sent to.

    No target means nothing was asked of it, and a vehicle that was asked for
    nothing has arrived. That is what lets a surveyor report a completed
    handback without ever having been given a point.
    """
    if target is None:
        return True
    return math.dist(_point(position, "the vehicle's position"),
                     _point(target, "the commanded point")) <= tolerance


def gap_m(position, target: Optional[Point]) -> Optional[float]:
    """How far the vehicle is from where it was sent, or None if nowhere."""
    if target is None:
        return None
    return math.dist(_point(position, "the vehicle's position"),
                     _point(target, "the commanded point"))
