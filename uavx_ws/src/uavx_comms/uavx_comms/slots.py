"""Where a relay parks, and when there is nowhere to park.

stage-1/architecture.md section 4. The component has work it still has to do:
for a station-keeping member that work is a single point, its own position; for
a member with a survey assignment it is the assigned area at that member's
cruise altitude. The slot sits on the segment from the attachment node to the
centroid of that work, at the point where the hop back to the attachment node
equals the longest hop forward to any point of the work. The relay parks where
it balances the two links it has to carry.

Three corrections are baked in here and all three were found the hard way.

Round 4 finding 2. The old rule took the midpoint to wherever the survivor
happened to be standing at election time. That was written against a component
whose surviving member stood still, and the integrated mission has one that
keeps flying a survey box, which puts the far corner of that box past the
usable link limit for an area the design elsewhere claims is in range.
Balancing against the whole work holds for the rest of the mission wherever the
survivor goes.

Round 5 finding 4. Raising the slot only when it happened to collide was a rule
no accepted scenario ever exercised, so nothing proved an implementation would
call it at all. Separation is taken vertically instead: every slot sits in a
band reserved for relays, clear of the highest mission corridor, and that holds
however lost a silent vehicle is as long as it holds altitude, which is the
last thing a radio failure touches. PX4 keeps flying whatever the link does.

And the reason the collision was invisible in the first place is worth keeping
in view: the vehicle whose airspace the slot lands in is dead in every scenario
that computes a slot, so the collision cannot happen. That is a property of the
scenario list, not of the rule. A vehicle that loses its radio is still flying.

If either hop exceeds the usable link limit, or the raise runs past the
ceiling, the component reports RELAY_INFEASIBLE rather than sending someone to
an impossible place.
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from . import params

Point = Tuple[float, float, float]

RELAY_INFEASIBLE = "RELAY_INFEASIBLE"
RELAY_FEASIBLE = "RELAY_FEASIBLE"

# Bisection depth. The interval is one segment of at most a few hundred metres
# and 200 halvings take it far below any distance this design can express, so
# the answer is exact to the printed precision and does not depend on a
# tolerance somebody chose.
_BISECTIONS = 200


def banded_slot(anchor: Point, work: Sequence[Point],
                band_altitude: float = params.RELAY_BAND_M) -> Point:
    """The balance point, solved on the horizontal with the altitude fixed.

    Same minimax rule as the unbanded version, which is why relay_kill's answer
    does not move: when the work is one stationary point the balance point is
    the midpoint of the segment. Fixing the altitude costs a little link budget
    and buys separation that does not depend on knowing where a silent vehicle
    went.
    """
    if not work:
        raise ValueError("a relay slot needs at least one point of work to balance")
    centre = tuple(sum(p[i] for p in work) / len(work) for i in range(3))
    lo, hi = 0.0, 1.0
    for _ in range(_BISECTIONS):
        mid = (lo + hi) / 2.0
        candidate = (anchor[0] + mid * (centre[0] - anchor[0]),
                     anchor[1] + mid * (centre[1] - anchor[1]),
                     band_altitude)
        back = math.dist(anchor, candidate)
        forward = max(math.dist(candidate, p) for p in work)
        if back < forward:
            lo = mid
        else:
            hi = mid
    mid = (lo + hi) / 2.0
    return (anchor[0] + mid * (centre[0] - anchor[0]),
            anchor[1] + mid * (centre[1] - anchor[1]),
            band_altitude)


def clear_slot(slot: Point, live: Sequence[Point]) -> Optional[Point]:
    """Raise the slot until it clears every vehicle that is still flying.

    Raising rather than sliding along the segment: the hops are around 170 m
    horizontally, so ten metres of altitude costs almost nothing in link
    budget, while sliding costs it directly. The alternative was tried and
    rejected because on the integrated geometry it needs an anchor hop past the
    usable limit, which is not a worse answer, it is not an answer.

    None means the ceiling was reached, and the caller reports RELAY_INFEASIBLE.
    """
    out = slot
    while any(math.dist(out, p) < params.SLOT_CLEARANCE_M for p in live):
        out = (out[0], out[1], out[2] + params.SLOT_RAISE_STEP_M)
        if out[2] > params.SLOT_CEILING_M:
            return None
    return out


@dataclass
class SlotDecision:
    """What the component decided, and every number behind it.

    The hops are carried out so the caller can put them in the run record
    rather than recomputing them somewhere else and getting a third answer.
    """

    status: str
    slot: Optional[Point]
    anchor_hop_m: Optional[float]
    work_hop_m: Optional[float]
    clearance_m: Optional[float]
    band_reserved: bool
    reason: str = ""

    def feasible(self) -> bool:
        return self.status == RELAY_FEASIBLE


def solve(anchor: Point, work: Sequence[Point],
          live: Sequence[Point] = ()) -> SlotDecision:
    """The whole rule: balance, clear the airspace, then check both hops.

    `work` is what the component still has to do, as points. `live` is every
    vehicle known to be still flying, which is not the same set: a killed
    vehicle is not in it and a silenced one is.
    """
    balanced = banded_slot(anchor, work)
    cleared = clear_slot(balanced, live)
    if cleared is None:
        return SlotDecision(
            status=RELAY_INFEASIBLE,
            slot=None,
            anchor_hop_m=None,
            work_hop_m=None,
            clearance_m=None,
            band_reserved=True,
            reason="no altitude at or below the ceiling clears every flying "
                   "vehicle by the required separation")

    anchor_hop = math.dist(anchor, cleared)
    work_hop = max(math.dist(cleared, p) for p in work)
    clearance = (min(math.dist(cleared, p) for p in live)
                 if live else float("inf"))

    if anchor_hop > params.USED_LINK_MAX_M or work_hop > params.USED_LINK_MAX_M:
        return SlotDecision(
            status=RELAY_INFEASIBLE,
            slot=cleared,
            anchor_hop_m=anchor_hop,
            work_hop_m=work_hop,
            clearance_m=clearance,
            band_reserved=True,
            reason="a hop the routing would have to depend on is outside the "
                   "usable link limit, so the chain would not hold once the "
                   "mover arrived")

    return SlotDecision(
        status=RELAY_FEASIBLE,
        slot=cleared,
        anchor_hop_m=anchor_hop,
        work_hop_m=work_hop,
        clearance_m=clearance,
        band_reserved=cleared[2] >= params.RELAY_BAND_M - 1e-9,
        reason="")


def band_clears(mission_altitudes: Sequence[float]) -> bool:
    """The reserved band must clear the highest mission corridor.

    No mission corridor may enter the band. This is the design-time predicate,
    available at runtime so a scenario that raises a mission altitude fails
    here rather than by putting a relay on top of a surveyor.
    """
    if not mission_altitudes:
        return True
    return (params.RELAY_BAND_M - max(mission_altitudes)
            >= params.SLOT_CLEARANCE_M)


def flight_time_s(origin: Point, slot: Point) -> float:
    """How long the mover needs, at cruise speed. Used to derive the budget."""
    return math.dist(origin, slot) / params.CRUISE_SPEED_MPS


def reconnect_budget_s(origin: Point, slot: Point) -> float:
    """The derived reconnect budget for one flight, term by term.

    Round 2 finding 6 is right that a flat 30 s was asserted rather than
    derived. Every term comes out of the geometry and the frozen periods, and
    the gate's own limit is not written here: scripts/gate.sh owns it, and a
    scenario compares its measurement with this number rather than the other
    way round.
    """
    return (params.NEIGHBOUR_TIMEOUT_S
            + params.LSA_PERIOD_S
            + params.ELECTION_WINDOW_S
            + flight_time_s(origin, slot)
            + params.SETTLE_ALLOWANCE_S
            + params.STABILITY_WINDOW_S)


def work_points(assignment: Sequence[Point]) -> List[Point]:
    """Normalise a member's work into points the balance rule can chew on.

    A station-keeping member contributes its own position. A member with a
    survey area contributes the corners of that area at its cruise altitude,
    because the balance rule needs the longest hop forward and for a convex
    region that is always attained at a corner.
    """
    return [tuple(p) for p in assignment]
