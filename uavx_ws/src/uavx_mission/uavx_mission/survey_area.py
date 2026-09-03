"""The frozen survey box, and the grid that coverage is scored on.

architecture.md section 6 freezes `survey_baseline`: a 200 m by 200 m area with
its south-west corner at (375, -100), a 10 m grid so 400 cells, a 12 m radius
sensor disc centred under the vehicle, and the area split into four vertical
strips of 50 m, one per vehicle. `coverage_fraction` is the cells whose centre
fell inside the footprint of any pose sample, over 400, and it comes from
sampled poses rather than from the planned path.

Those numbers are written here once. `test/test_frozen_geometry.py` reads them
back out of stage-1/architecture.md and fails when the two disagree, so this
file cannot drift away from the design it is a copy of without something
saying so.

This module computes no coverage figure. The metric belongs to `uavx_eval`,
which reads real poses out of a run record; a second implementation living
beside the planner would be a second answer to the same question, and the
planner would be the one grading its own work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Tuple

# --- survey_baseline, frozen in architecture.md section 6 ------------------
BASELINE_SW_CORNER_M: Tuple[float, float] = (375.0, -100.0)
BASELINE_SIDE_M = 200.0
BASELINE_CELL_M = 10.0
BASELINE_SENSOR_RADIUS_M = 12.0
BASELINE_STRIP_COUNT = 4
BASELINE_DURATION_S = 420.0

Point = Tuple[float, float]


class SurveyAreaError(ValueError):
    """An area the design cannot describe.

    The message names the offending figure. A survey area with a
    non-positive side, a cell that does not divide it or a sensor with no
    radius is not a smaller mission, it is an unanswerable one, and the
    planner is entitled to refuse it rather than return a path that covers
    part of it.
    """


@dataclass(frozen=True)
class SurveyArea:
    """A rectangular survey box with the grid its coverage is scored on.

    Held as its two corners rather than as a corner and a side, because every
    consumer wants the bounds and only the constructor wants the side. The
    box is axis aligned in the frozen ENU frame: x east, y north, metres,
    origin at the GCS.
    """

    x_min: float
    y_min: float
    x_max: float
    y_max: float
    cell_m: float
    sensor_radius_m: float

    def __post_init__(self) -> None:
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise SurveyAreaError(
                f"survey area has no extent: x {self.x_min} to {self.x_max}, "
                f"y {self.y_min} to {self.y_max}")
        if self.cell_m <= 0:
            raise SurveyAreaError(f"grid cell must be positive, got {self.cell_m}")
        if self.sensor_radius_m <= 0:
            raise SurveyAreaError(
                f"sensor radius must be positive, got {self.sensor_radius_m}")
        for name, span in (("width", self.width), ("height", self.height)):
            cells = span / self.cell_m
            if abs(cells - round(cells)) > 1e-9:
                raise SurveyAreaError(
                    f"a {self.cell_m} m cell does not divide the {span} m "
                    f"{name}, so the cell count coverage is scored against "
                    f"would be a rounding")

    @classmethod
    def from_corner(cls, sw: Point, width: float, height: float,
                    cell_m: float, sensor_radius_m: float) -> "SurveyArea":
        """An area from its south-west corner and its extent."""
        return cls(sw[0], sw[1], sw[0] + width, sw[1] + height,
                   cell_m, sensor_radius_m)

    @classmethod
    def baseline(cls) -> "SurveyArea":
        """The frozen `survey_baseline` box."""
        return cls.from_corner(BASELINE_SW_CORNER_M, BASELINE_SIDE_M,
                               BASELINE_SIDE_M, BASELINE_CELL_M,
                               BASELINE_SENSOR_RADIUS_M)

    @property
    def width(self) -> float:
        """East to west extent, the axis the strips divide."""
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        """South to north extent, the axis a lane runs along."""
        return self.y_max - self.y_min

    @property
    def area_m2(self) -> float:
        return self.width * self.height

    @property
    def cell_count(self) -> int:
        """The denominator of `coverage_fraction`."""
        return int(round(self.width / self.cell_m)) * \
            int(round(self.height / self.cell_m))

    def cell_centres(self) -> Iterator[Point]:
        """Every cell centre, west to east then south to north.

        These are the points `coverage_fraction` counts. A cell is covered
        when its centre fell inside the footprint of a pose sample, so the
        centre is the whole of what the metric looks at and the rest of the
        cell is never consulted.
        """
        nx = int(round(self.width / self.cell_m))
        ny = int(round(self.height / self.cell_m))
        half = self.cell_m / 2.0
        for i in range(nx):
            x = self.x_min + half + i * self.cell_m
            for j in range(ny):
                yield (x, self.y_min + half + j * self.cell_m)

    def contains(self, x: float, y: float) -> bool:
        """Whether a point is inside the box, edges included."""
        return (self.x_min <= x <= self.x_max
                and self.y_min <= y <= self.y_max)
