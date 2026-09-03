"""The lawnmower path that covers one strip.

Boustrophedon, "as the ox turns": lanes down the long axis of the strip, each
flown in the opposite direction to the one before, joined by a turn at the
end of the strip. It is the shortest family of paths that sweeps a rectangle
with a fixed swath, and it is what architecture.md freezes for both survey
scenarios.

## Where the lane count comes from

The sensor is a disc of radius `r` centred under the vehicle, so flying a
straight lane sweeps a band `2r` wide. Two neighbouring lanes leave no gap
between them when their spacing is at most `2r`, and a lane leaves no gap
against the edge of its strip when it sits at most `r` from that edge.

Lay `n` lanes across a strip of width `w` at spacing `w / n`, with the first
and last half a spacing in from the edges. Then the inset is `w / 2n` and both
conditions collapse into the same one:

    w / 2n <= r      which is      n >= w / 2r

so `n = ceil(w / 2r)` is the fewest lanes that can cover the strip, and every
extra lane past it is distance flown for no new ground. The rule is not
invented here. architecture.md freezes `mission_integrated`'s four lanes at
x = 468.125, 474.375, 480.625 and 486.875, over 12.5 m strips with a 6 m
sensor, and that is exactly what this returns: n = ceil(12.5 / 12) = 2, a
spacing of 6.25 and an inset of 3.125. test/test_boustrophedon.py holds the
planner to those four numbers.

## What the path is not allowed to do

The lanes run the full height of the strip, so the turns sit on its northern
and southern boundaries. Stopping short would leave the end cells of every
lane uncovered, and the cells at the end of a lane are the ones a coverage
figure is least likely to miss loudly: the fraction drops by a few per cent
and nothing says which part of the box was never flown.

Every move is along one axis. A planner that cut a corner diagonally between
lanes would save a metre of flying and sweep a band that is neither lane, and
the ground under the diagonal would be covered by accident rather than by
plan.
"""

from __future__ import annotations

import math
from typing import Iterator, Sequence, Tuple

from uavx_mission.partition import Strip

Waypoint = Tuple[float, float, float]


class PlanError(ValueError):
    """A strip this planner cannot cover.

    A strip with no extent, or a sensor with no radius, has no lawnmower
    path over it. Returning an empty path would be a plan that covers
    nothing while reporting success.
    """


def lane_count(width: float, sensor_radius: float) -> int:
    """The fewest lanes that cover a strip of this width with this sensor."""
    if width <= 0:
        raise PlanError(f"a strip {width} m wide has nothing to cover")
    if sensor_radius <= 0:
        raise PlanError(f"a sensor radius of {sensor_radius} m sweeps no band")
    return max(1, math.ceil(width / (2.0 * sensor_radius) - 1e-9))


def lane_spacing(width: float, sensor_radius: float) -> float:
    """The distance between neighbouring lanes."""
    return width / lane_count(width, sensor_radius)


def lane_positions(strip: Strip, sensor_radius: float) -> Tuple[float, ...]:
    """Where the lanes sit across the strip, west to east.

    Half a spacing in from each edge, which is what makes the inset against
    the strip edge and the half gap between neighbouring lanes the same
    number. Lanes laid on the edges instead would put two vehicles' lanes on
    the same line wherever two strips meet, and the vehicles would be
    separated by nothing but their altitude layer.
    """
    n = lane_count(strip.width, sensor_radius)
    spacing = strip.width / n
    return tuple(strip.x_min + spacing * (i + 0.5) for i in range(n))


def plan_path(strip: Strip, sensor_radius: float, altitude: float,
              start_north: bool = False) -> Tuple[Waypoint, ...]:
    """The lawnmower over one strip, as the waypoints to fly in order.

    The first waypoint is the start of the first lane, which is where the
    ingress leg has to deliver the vehicle. `start_north` flies the first
    lane from the northern boundary southwards; architecture.md uses it to
    mirror two surveyors on neighbouring strips so that one of them is
    reliably nearer the attachment node when an election has to pick between
    them.
    """
    if altitude <= 0:
        raise PlanError(f"survey altitude must be above the ground, got {altitude}")
    lanes = lane_positions(strip, sensor_radius)
    ends = (strip.y_max, strip.y_min) if start_north else (strip.y_min, strip.y_max)

    path: list = []
    for i, x in enumerate(lanes):
        first, second = ends if i % 2 == 0 else (ends[1], ends[0])
        path.append((x, first, altitude))
        path.append((x, second, altitude))
    return tuple(path)


def path_length(path: Sequence[Waypoint]) -> float:
    """The distance flown along a plan, ignoring how it was reached."""
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def minimum_sweep_length(area_m2: float, sensor_radius: float) -> float:
    """The shortest any path could be and still sweep this much ground.

    A disc of radius `r` dragged along a path of length `L` sweeps at most
    `2rL` of new ground, so covering an area needs at least `area / 2r` of
    flying whatever shape the path takes. It is a floor rather than an
    achievable figure: it assumes no turn, no overlap and no ingress, and no
    real lawnmower reaches it. A plan below it has not covered the area, and
    a plan far above it is flying ground twice.
    """
    if sensor_radius <= 0:
        raise PlanError(f"a sensor radius of {sensor_radius} m sweeps no band")
    return area_m2 / (2.0 * sensor_radius)


def sample_path(path: Sequence[Waypoint], step: float) -> Iterator[Waypoint]:
    """Points along the plan, `step` metres apart, both ends included.

    This is what a pose stream would look like if the vehicle tracked the
    plan exactly, and it is the only honest way to ask a planner whether its
    spacing covers the ground: a plan judged at its waypoints alone is judged
    at the two ends of each lane and nowhere along it.

    Resampled off the cumulative distance rather than by carrying a leftover
    from leg to leg. The leftover version drifts by a rounding per leg, and a
    lawnmower is nearly all legs.
    """
    if step <= 0:
        raise PlanError(f"a sample step of {step} m does not advance")
    if not path:
        return
    if len(path) == 1:
        yield path[0]
        return

    legs = [math.dist(a, b) for a, b in zip(path, path[1:])]
    total = sum(legs)
    ends = [0.0]
    for leg in legs:
        ends.append(ends[-1] + leg)

    seg = 0
    count = int(math.floor(total / step)) + 1
    for k in range(count):
        d = k * step
        while seg < len(legs) - 1 and d > ends[seg + 1]:
            seg += 1
        a, b = path[seg], path[seg + 1]
        leg = legs[seg]
        f = 0.0 if leg == 0.0 else (d - ends[seg]) / leg
        yield (a[0] + (b[0] - a[0]) * f,
               a[1] + (b[1] - a[1]) * f,
               a[2] + (b[2] - a[2]) * f)
    if (count - 1) * step < total:
        yield path[-1]
