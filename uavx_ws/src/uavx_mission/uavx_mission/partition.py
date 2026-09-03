"""Split the survey box into one strip per vehicle.

architecture.md section 6 freezes the split for `survey_baseline`: four
vertical strips of 50 m across a 200 m box, one vehicle each. Vertical means
the cut runs along x and each strip keeps the full height of the box, so a
lane inside a strip is as long as the box is tall and the number of turns is
as small as the shape allows.

Two properties carry everything downstream and neither is obvious from the
arithmetic:

* The strips tile the box. Their union is the box exactly and no two of them
  overlap, so every cell the coverage metric scores belongs to exactly one
  vehicle and no cell belongs to none. A partition that double counts a seam
  gives two vehicles the same work while the gate still reads full coverage,
  and a partition that leaves a seam uncovered cannot reach it at all.
* The seam belongs to one side. Interior boundaries are half open, so a point
  sitting exactly on a cut is inside the strip to its east and outside the one
  to its west. The eastern edge of the whole box is the one closed edge,
  because there is no further strip to own it.

The assignment is by sorted vehicle id, west to east, and the sort is
numeric on the trailing index so `uav_10` follows `uav_9` rather than
`uav_1`. A caller handing the ids in whatever order a set iterated them gets
the same partition every time, which is what standing rule 3 asks of a run
that has to replay exactly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence, Tuple

from uavx_mission.survey_area import SurveyArea

VEHICLE_ID = re.compile(r"^([A-Za-z_]+)([0-9]+)$")


class PartitionError(ValueError):
    """A partition the design cannot produce.

    Named faults rather than a silently smaller answer: an empty vehicle
    list, a repeated id, or an id the sort cannot order. Each of those makes
    the strips stop tiling the box, and a coverage figure taken over a box
    that was never fully assigned is measuring something else.
    """


def sort_key(vehicle_id: str) -> Tuple[str, int, str]:
    """Order vehicle ids numerically on their trailing index.

    A plain string sort puts `uav_10` between `uav_1` and `uav_2`, which
    would hand out strips in an order that changes the moment a fifth
    vehicle appears. Ids with no trailing number sort after the ones that
    have it, by name, so an unexpected id is still ordered deterministically
    rather than raising inside a sort.
    """
    m = VEHICLE_ID.match(vehicle_id)
    if not m:
        return (vehicle_id, 0, vehicle_id)
    return ("", int(m.group(2)), m.group(1))


@dataclass(frozen=True)
class Strip:
    """One vehicle's share of the survey box.

    `owns_east_edge` is set on the eastmost strip alone. Without it the cut
    between two strips would either belong to both, which double counts the
    seam, or the box's own eastern edge would belong to none.
    """

    vehicle_id: str
    index: int
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    owns_east_edge: bool

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    @property
    def area_m2(self) -> float:
        return self.width * self.height

    @property
    def centre(self) -> Tuple[float, float]:
        return ((self.x_min + self.x_max) / 2.0,
                (self.y_min + self.y_max) / 2.0)

    def contains(self, x: float, y: float) -> bool:
        """Whether a point belongs to this strip and to no other.

        Half open in x on every strip but the eastmost, closed in y because
        the split does not cut that axis at all.
        """
        if not (self.y_min <= y <= self.y_max):
            return False
        if x < self.x_min:
            return False
        return x <= self.x_max if self.owns_east_edge else x < self.x_max


def partition(area: SurveyArea, vehicles: Sequence[str]) -> Tuple[Strip, ...]:
    """The box divided into one equal width strip per vehicle, west to east.

    Every edge is computed from the box corners and the strip index rather
    than by walking east one width at a time. Accumulating a width n times
    leaves the last edge a few floating point ulps away from the box, and
    the strips then fail to tile it by an amount too small to see and large
    enough to lose a point that sits exactly on the edge.
    """
    if not vehicles:
        raise PartitionError("no vehicles to partition the survey box between")
    if len(set(vehicles)) != len(vehicles):
        repeated = sorted({v for v in vehicles if list(vehicles).count(v) > 1})
        raise PartitionError(
            f"vehicle ids repeat: {repeated}. Two strips with the same owner "
            f"is a box that is not fully assigned.")

    ordered = sorted(vehicles, key=sort_key)
    n = len(ordered)
    strips = []
    for i, vehicle_id in enumerate(ordered):
        x_min = area.x_min + area.width * i / n
        x_max = area.x_min + area.width * (i + 1) / n
        strips.append(Strip(
            vehicle_id=vehicle_id,
            index=i,
            x_min=x_min,
            x_max=area.x_max if i == n - 1 else x_max,
            y_min=area.y_min,
            y_max=area.y_max,
            owns_east_edge=(i == n - 1),
        ))
    return tuple(strips)


def strip_for(strips: Sequence[Strip], x: float, y: float):
    """The one strip a point belongs to, or None when it is outside the box."""
    for strip in strips:
        if strip.contains(x, y):
            return strip
    return None


def strip_of(strips: Sequence[Strip], vehicle_id: str) -> Strip:
    """The strip assigned to one vehicle."""
    for strip in strips:
        if strip.vehicle_id == vehicle_id:
            return strip
    raise PartitionError(
        f"{vehicle_id} has no strip in this partition; it holds "
        f"{[s.vehicle_id for s in strips]}")
