"""The third safety layer: who gives way, and until when.

architecture.md section 5 puts three things between vehicles. Distinct cruise
altitudes keep them in separate layers, a monitor samples separation at 20 Hz
and says whether the floor was ever crossed, and this decides what to do about
a crossing before it happens. The first two are passive. Only this one changes
where an aircraft goes.

Round 2 finding 9 is why `encounter.yaml` exists at all: layering alone would
keep everything apart in every scenario and leave this file as code nothing
runs. That scenario commands two vehicles into one layer on crossing paths, so
the rule has to act or the run records a violation, and `encounter_noyield.yaml`
is the same flight with the rule switched off and a violation required.

**The decision.** When the predicted separation inside `YIELD_HORIZON_S` drops
under `MIN_SEPARATION_M`, the vehicle with the higher system id holds until
the prediction clears. Fixed by id and never negotiated. Two vehicles that both
decide to give way stop in front of each other and neither ever moves; two that
both decide to carry on arrive together. A total order settles it with no
message exchange, which matters because the only information either of them has
is already late.

**How late.** Position and velocity come out of `HELLO`, so a sighting is as
old as the beacon that carried it: up to one HELLO period, plus the hop latency,
plus whatever the radio's fade band did to it. `Sighting.state_at` extrapolates
from the timestamp inside the message rather than from when it arrived, so a
beacon the radio held onto is used at the age it actually has instead of being
treated as current. A sighting older than `NEIGHBOUR_TIMEOUT_S` is dropped
rather than extrapolated: at cruise that is 30 m of guessing, which is three
times the separation floor this is protecting.

Safety therefore degrades when the link degrades. That is the real behaviour of
a swarm whose only source of neighbour state is its radio, and the proposal says
so rather than claiming the vehicles share a world model.

Nothing here reads a clock, subscribes to anything or knows what a packet is.
The caller supplies the time, its own state and the sightings, so the whole rule
is testable with no ROS graph and no simulator.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from . import params

Vec3 = Tuple[float, float, float]


def system_id(node_id: str) -> Tuple[int, str]:
    """The order the rule gives way in. Higher holds.

    The digits in the id, which is the system id PX4 and the scenario both
    count vehicles by, and the whole string after it so two ids that carry the
    same number still order. A vehicle with no digits sorts below every
    numbered one, because the ground station is the only such node and it does
    not fly.
    """
    digits = "".join(ch for ch in str(node_id) if ch.isdigit())
    return (int(digits) if digits else -1, str(node_id))


def gives_way(own_id: str, other_id: str) -> bool:
    """Is this vehicle the one that holds, of the two.

    Never true against itself. A rule that let a vehicle yield to its own
    beacon would stop it dead the first time the radio echoed one back.
    """
    if str(own_id) == str(other_id):
        return False
    return system_id(own_id) > system_id(other_id)


def closest_approach(relative_position: Sequence[float],
                     relative_velocity: Sequence[float],
                     horizon_s: float) -> Tuple[float, float]:
    """When two vehicles are nearest inside the horizon, and how near.

    Returns the time and the distance. Both are clamped to the window: a pair
    already moving apart is nearest now, and a pair whose true closest point
    falls after the horizon is only predicted as far as the horizon, because
    beyond it neither vehicle's velocity is worth anything.
    """
    if horizon_s < 0:
        raise ValueError(
            f"the horizon is {horizon_s}. A rule that looks backwards would "
            f"hold for a conflict that has already happened")
    speed_squared = sum(v * v for v in relative_velocity)
    if speed_squared <= 0.0:
        when = 0.0
    else:
        closing = sum(p * v for p, v in
                      zip(relative_position, relative_velocity))
        when = min(max(-closing / speed_squared, 0.0), float(horizon_s))
    gap = [p + v * when for p, v in zip(relative_position, relative_velocity)]
    return when, math.sqrt(sum(g * g for g in gap))


@dataclass(frozen=True)
class Sighting:
    """One neighbour's motion, as its last beacon reported it.

    Frozen, because a sighting is a record of what arrived. Editing one in
    place would leave the rule unable to say how old its information is, and
    the age is the honest part.
    """

    node_id: str
    position: Vec3
    velocity: Vec3
    sent_at: float

    def age_at(self, now: float) -> float:
        return float(now) - self.sent_at

    def state_at(self, now: float) -> Tuple[Vec3, Vec3]:
        """Where this neighbour probably is, extrapolated from its own stamp.

        Straight line at the velocity it last reported. It is wrong the moment
        the neighbour turns, and the horizon is short enough that the error
        stays under the separation floor for a vehicle flying the cruise legs
        this design gives them.
        """
        age = self.age_at(now)
        moved = tuple(p + v * age
                      for p, v in zip(self.position, self.velocity))
        return moved, self.velocity  # type: ignore[return-value]


@dataclass
class Conflict:
    """A predicted loss of separation, and what the rule did about it."""

    other_id: str
    at_s: float
    separation_m: float
    holding: bool

    def as_record(self) -> dict:
        return {"other_id": self.other_id,
                "at_s": round(self.at_s, 3),
                "separation_m": round(self.separation_m, 3),
                "holding": self.holding}


class YieldRule:
    """One vehicle's view of who it has to give way to.

    Fed sightings as beacons arrive and asked, on every tick, whether this
    vehicle should be holding. It counts what it did, because the run record
    has to name the vehicle that yielded and for how long, and a rule that
    only changed behaviour would leave the gate asserting over a number
    nothing produced.
    """

    def __init__(self, node_id: str, enabled: bool = True,
                 horizon_s: float = params.YIELD_HORIZON_S,
                 min_separation_m: float = params.MIN_SEPARATION_M,
                 stale_after_s: float = params.NEIGHBOUR_TIMEOUT_S) -> None:
        if not node_id:
            raise ValueError(
                "the yield rule needs the id of the vehicle it is for. Who "
                "holds is decided by comparing ids, and a rule that does not "
                "know its own cannot answer")
        for name, value in (("horizon_s", horizon_s),
                            ("min_separation_m", min_separation_m),
                            ("stale_after_s", stale_after_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} is {value!r}, not a positive number")
        self.node_id = str(node_id)
        self.enabled = bool(enabled)
        self.horizon_s = float(horizon_s)
        self.min_separation_m = float(min_separation_m)
        self.stale_after_s = float(stale_after_s)

        self.sightings: Dict[str, Sighting] = {}
        self.holding = False
        self.events = 0
        self.hold_seconds = 0.0
        # The nearest approach this vehicle ever predicted, whether or not it
        # was the one that gave way. A run where the rule never saw anything
        # coming and a run where it saw everything and was the lower id look
        # the same in the event count and different here.
        self.closest_predicted_m: Optional[float] = None
        self.last_conflict: Optional[Conflict] = None
        self._last_now: Optional[float] = None
        # The velocity this vehicle was flying when it decided to hold, kept
        # for as long as the hold lasts. See `update`: a stopped vehicle has
        # no closing speed, so asking whether it is in conflict at its current
        # velocity answers no the moment it stops.
        self._resume_velocity: Optional[Vec3] = None

    # ------------------------------------------------------------ the input
    def sighting(self, node_id: str, position: Sequence[float],
                 velocity: Sequence[float], sent_at: float) -> None:
        """One neighbour's beacon. Newer wins, older is dropped.

        Out of order arrival is normal: the radio delays every packet by the
        hop latency and the fade band drops some of them, so a beacon can
        arrive behind one that was sent after it. Keeping the newer stamp
        means a late arrival cannot rewind what this vehicle knows.
        """
        if str(node_id) == self.node_id:
            return
        when = float(sent_at)
        if not math.isfinite(when):
            raise ValueError(
                f"a sighting of {node_id} is stamped {sent_at!r}, and the age "
                f"of a sighting is what decides whether it may be used")
        known = self.sightings.get(str(node_id))
        if known is not None and known.sent_at >= when:
            return
        self.sightings[str(node_id)] = Sighting(
            node_id=str(node_id),
            position=(float(position[0]), float(position[1]),
                      float(position[2])),
            velocity=(float(velocity[0]), float(velocity[1]),
                      float(velocity[2])),
            sent_at=when)

    def forget(self, node_id: str) -> None:
        """Drop a neighbour, for a vehicle that has gone rather than gone quiet."""
        self.sightings.pop(str(node_id), None)

    # ----------------------------------------------------------- the decision
    def conflicts(self, now: float, position: Sequence[float],
                  velocity: Sequence[float]) -> Tuple[Conflict, ...]:
        """Every predicted loss of separation, whoever has to act on it.

        Reported for all of them and not only the ones this vehicle gives way
        to, because the control run needs to show the rule saw the crossing
        and did nothing, rather than showing nothing at all.
        """
        out = []
        for other in sorted(self.sightings.values(), key=lambda s: s.node_id):
            if other.age_at(now) > self.stale_after_s:
                continue
            where, moving = other.state_at(now)
            when, gap = closest_approach(
                [w - p for w, p in zip(where, position)],
                [m - v for m, v in zip(moving, velocity)],
                self.horizon_s)
            if gap >= self.min_separation_m:
                continue
            out.append(Conflict(other_id=other.node_id, at_s=when,
                                separation_m=gap,
                                holding=gives_way(self.node_id, other.node_id)))
        return tuple(out)

    def update(self, now: float, position: Sequence[float],
               velocity: Sequence[float]) -> bool:
        """Should this vehicle be holding right now.

        Call it every tick. The time between calls is what `hold_seconds`
        accumulates, so a caller that skips ticks under-reports the hold
        rather than inventing one.

        While the vehicle is holding, the prediction is run against the
        velocity it was flying when it decided to, and not against the one it
        has now. A held vehicle is stopped, a stopped vehicle has no closing
        speed, and a rule that asked about the present would clear its own
        conflict the instant it acted on it: hold, see nothing, resume, move
        back into the conflict, hold again. That is a vehicle chattering its
        way into the crossing at a fraction of cruise, and it satisfies a gate
        asking for one yield event and a hold above zero while doing the one
        thing the rule exists to prevent. The question a held vehicle has to
        keep asking is whether resuming would still be unsafe, and that is a
        question about the velocity it would resume with.
        """
        moment = float(now)
        elapsed = 0.0 if self._last_now is None else moment - self._last_now
        if elapsed < 0:
            raise ValueError(
                f"the yield rule was asked about t={moment} after being asked "
                f"about t={self._last_now}. Simulated time went backwards")
        if self.holding:
            self.hold_seconds += elapsed
        self._last_now = moment

        if not self.enabled:
            # The control run. Everything above still happens so a disabled
            # rule reports a zero hold rather than no hold at all, and the two
            # runs differ in the flight rather than in what was measured.
            self.holding = False
            return False

        flying = (self._resume_velocity if self.holding
                  and self._resume_velocity is not None else tuple(velocity))
        found = self.conflicts(moment, position, flying)
        for conflict in found:
            if self.closest_predicted_m is None \
                    or conflict.separation_m < self.closest_predicted_m:
                self.closest_predicted_m = conflict.separation_m
        mine = [c for c in found if c.holding]
        hold = bool(mine)
        if hold:
            self.last_conflict = min(mine, key=lambda c: c.separation_m)
        if hold and not self.holding:
            self.events += 1
            self._resume_velocity = tuple(velocity)  # type: ignore[assignment]
        if not hold:
            self._resume_velocity = None
        self.holding = hold
        return hold

    # ------------------------------------------------------------ the record
    def as_record(self) -> dict:
        """What this vehicle did about separation, for the run record.

        `yield_events` is zero on a vehicle that never had to give way, which
        is every vehicle in eight of the nine scenarios and the lower id in
        the ninth. Zero here is a real answer and not a missing one, which is
        why `closest_predicted_m` is beside it: a rule that saw nothing all
        run and a rule that saw a crossing and was not the one to act both
        report no events and disagree about that.
        """
        return {
            "yield_enabled": self.enabled,
            "yield_events": self.events,
            "yield_hold_s": round(self.hold_seconds, 3),
            "yield_holding": self.holding,
            "yield_horizon_s": self.horizon_s,
            "yield_closest_predicted_m": (
                None if self.closest_predicted_m is None
                else round(self.closest_predicted_m, 3)),
            "yield_last_conflict": (None if self.last_conflict is None
                                    else self.last_conflict.as_record()),
        }
