"""The state that flies a survey plan, and hands it over when it has to.

The executor is deliberately not a controller. It holds a plan, a position
that somebody else measured, and the answer to one question: which waypoint
is this vehicle flying to right now. Everything the gates ask about a survey
comes out of that answer, and none of it needs a simulator to be true or
false.

## Why it never picks the nearest waypoint

Advancing to whichever waypoint is closest looks equivalent and is not. A
vehicle blown across its strip, or one that starts a lane from the wrong end,
is nearer the far end of the next lane than the near end of the one it is on,
and a nearest-waypoint executor skips the rest of that lane. The ground under
it is never flown, coverage drops by the width of one swath, and the plan the
run reports is the plan that was never flown. So the executor advances only
when the vehicle actually arrives at the waypoint it was flying to, one
waypoint at a time, in order.

## Why progress is a distance and not a waypoint count

architecture.md freezes the claim that each surveyor is 58% of the way
through its strip when the relay dies in `mission_integrated`, and the run
record carries a coverage figure at the kill beside it. Waypoints on a
lawnmower are not evenly spaced: a lane is the height of the box and a turn
is one lane spacing, so counting waypoints reports the same progress for a
200 m lane and a 17 m turn. Progress here is distance flown over distance
planned, including the part of the current leg already behind the vehicle.

## Handover

architecture.md says the elected relay's survey strip is handed to the
members that stay rather than abandoned. That is two operations and they are
kept separate, because the role layer in W4 decides who takes the work and
the executor must not: `hand_over` empties this vehicle's remaining plan and
returns it, `inherit` appends somebody else's remaining plan behind this
vehicle's own. A vehicle finishes its own strip first and then flies what it
inherited, which is the order architecture.md freezes.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from uavx_mission.boustrophedon import Waypoint, path_length
from uavx_mission.partition import Strip

DEFAULT_ACCEPTANCE_RADIUS_M = 2.0


class ExecutorError(ValueError):
    """A mission the executor refuses to fly.

    A plan that leaves its own strip is the one worth naming. It means the
    partition and the planner disagree, and the vehicle would fly over
    another vehicle's ground at its own altitude while both of them reported
    a healthy survey.
    """


class MissionState(Enum):
    """Where the executor is in its own plan."""

    SURVEY = "survey"
    RELAY = "relay"
    COMPLETE = "complete"


class MissionExecutor:
    """One vehicle's survey, as state rather than as motion."""

    def __init__(self, vehicle_id: str, strip: Strip,
                 path: Sequence[Waypoint],
                 acceptance_radius_m: float = DEFAULT_ACCEPTANCE_RADIUS_M):
        if acceptance_radius_m <= 0:
            raise ExecutorError(
                f"acceptance radius must be positive, got {acceptance_radius_m}")
        if not path:
            raise ExecutorError(
                f"{vehicle_id} was handed an empty plan, which is a survey "
                f"that reports complete before it starts")
        for x, y, _z in path:
            if not strip.contains(x, y):
                raise ExecutorError(
                    f"{vehicle_id}'s plan leaves its strip at ({x}, {y}); the "
                    f"strip runs x {strip.x_min} to {strip.x_max}, y "
                    f"{strip.y_min} to {strip.y_max}")

        self.vehicle_id = vehicle_id
        self.strip = strip
        self.acceptance_radius_m = float(acceptance_radius_m)

        self._plan: List[Waypoint] = [tuple(w) for w in path]
        self._index = 0
        self._state = MissionState.SURVEY
        self._relay_slot: Optional[Waypoint] = None
        self._position: Optional[Waypoint] = None
        self._visited: List[Waypoint] = []
        self._planned_length = path_length(self._plan)

    # ------------------------------------------------------------- reading
    @property
    def state(self) -> MissionState:
        return self._state

    @property
    def complete(self) -> bool:
        """Whether every waypoint in the plan has been reached."""
        return self._state is MissionState.COMPLETE

    @property
    def visited(self) -> Tuple[Waypoint, ...]:
        """The waypoints this vehicle actually arrived at, in order.

        Kept so a handover can be checked for the two ways it goes wrong:
        work that nobody flies, and work that two vehicles fly.
        """
        return tuple(self._visited)

    @property
    def remaining(self) -> Tuple[Waypoint, ...]:
        """The waypoints still to be flown, starting with the current target."""
        return tuple(self._plan[self._index:])

    def target(self) -> Optional[Waypoint]:
        """The setpoint to fly to now, or None when there is nothing left.

        While the vehicle holds the relay role the target is the relay slot.
        The survey index does not move, so a release puts the vehicle back on
        the waypoint it was flying to and not on the one after it.
        """
        if self._state is MissionState.RELAY:
            return self._relay_slot
        if self._index >= len(self._plan):
            return None
        return self._plan[self._index]

    def progress_fraction(self) -> float:
        """Distance flown over distance planned, between 0 and 1.

        Counts the part of the current leg already behind the vehicle, by
        projecting the last reported position onto it. A vehicle that has
        not reported a position yet has flown nothing.
        """
        if self._planned_length <= 0:
            return 1.0
        if self._index >= len(self._plan):
            return 1.0
        done = path_length(self._plan[:self._index])
        if self._position is not None and self._index > 0:
            done += _along_leg(self._plan[self._index - 1],
                               self._plan[self._index], self._position)
        return min(1.0, max(0.0, done / self._planned_length))

    # -------------------------------------------------------------- flying
    def update(self, position: Waypoint) -> Optional[Waypoint]:
        """Report where the vehicle is, and get the setpoint to fly next.

        At most one waypoint is retired per report. A vehicle that is inside
        the acceptance radius of two waypoints at once has either been given
        a degenerate plan or is not where it says it is, and retiring both
        would skip the leg between them without ever flying it.

        A report made while the vehicle holds the relay role moves nothing.
        It is flying to the slot rather than along its strip, and its
        progress through the strip is the same when it gets back as it was
        when it left, whatever ground it crossed on the way.
        """
        if self._state is MissionState.RELAY:
            return self._relay_slot
        self._position = tuple(position)
        if self._index >= len(self._plan):
            self._state = MissionState.COMPLETE
            return None

        if math.dist(position, self._plan[self._index]) <= self.acceptance_radius_m:
            self._visited.append(self._plan[self._index])
            self._index += 1
            if self._index >= len(self._plan):
                self._state = MissionState.COMPLETE
                return None
        return self._plan[self._index]

    # ------------------------------------------------------------ handover
    def hand_over(self) -> Tuple[Waypoint, ...]:
        """Give up the unflown part of this plan and return it.

        The vehicle keeps what it has already flown, so the two halves of a
        handover cover the strip exactly once between them. Calling this
        twice returns nothing the second time rather than the same work
        again.
        """
        work = tuple(self._plan[self._index:])
        self._plan = self._plan[:self._index]
        self._planned_length = path_length(self._plan)
        if self._state is MissionState.SURVEY:
            self._state = MissionState.COMPLETE
        return work

    def inherit(self, work: Sequence[Waypoint]) -> None:
        """Take on another vehicle's unflown plan, behind this vehicle's own.

        Appended rather than merged. The inheriting vehicle finishes its own
        strip and then flies the handover, which is what architecture.md
        freezes for `mission_integrated`, and it is also the only order that
        does not cross the two strips at one altitude.
        """
        if not work:
            return
        self._plan.extend(tuple(w) for w in work)
        self._planned_length = path_length(self._plan)
        if self._state is MissionState.COMPLETE and self._index < len(self._plan):
            self._state = MissionState.SURVEY

    # ---------------------------------------------------------------- role
    def assign_relay(self, slot: Waypoint) -> None:
        """Take the relay role and fly to the slot, keeping the survey place.

        The survey is suspended, not cancelled. Whether the unflown work is
        also handed to somebody else is the role layer's decision and it
        calls `hand_over` for that; an executor that threw the work away
        here would make the two outcomes indistinguishable.
        """
        self._relay_slot = tuple(slot)
        self._state = MissionState.RELAY

    def release(self) -> None:
        """Give the relay role back and resume the survey where it stopped."""
        if self._state is not MissionState.RELAY:
            return
        self._relay_slot = None
        self._state = (MissionState.COMPLETE if self._index >= len(self._plan)
                       else MissionState.SURVEY)


def _along_leg(a: Waypoint, b: Waypoint, p: Waypoint) -> float:
    """How far along the leg a to b the point p has got, clamped to the leg.

    The projection, not the distance from a. A vehicle pushed sideways off
    its lane has not made progress along it, and counting the straight line
    from the last waypoint would say it had.
    """
    leg = math.dist(a, b)
    if leg == 0.0:
        return 0.0
    dot = sum((b[i] - a[i]) * (p[i] - a[i]) for i in range(3))
    return min(leg, max(0.0, dot / leg))
