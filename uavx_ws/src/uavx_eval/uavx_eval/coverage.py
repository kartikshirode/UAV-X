"""Coverage of a frozen box, from sampled vehicle poses and never from a plan.

stage-1/plan.md, week 2: "`coverage_fraction` comes from sampled vehicle poses,
never the planned path." A planner that reports its own intentions scores the
same whether the vehicles flew or sat on the pavement, which is the reason the
rubric's mission row is worth 25% and the reason this file exists.

The rule from stage-1/architecture.md section 6, stated once: a cell counts
when its centre fell inside the sensor footprint of at least one pose sample.
The footprint is a horizontal disc centred under the vehicle, so altitude does
not enter it, and the denominator is the number of cells the box tiles into.

Nothing here imports rclpy, and nothing here knows what a scenario file looks
like. Every dimension arrives as an argument, because the two survey boxes have
different sizes, cells and footprints and a constant here would quietly serve
one of them.
"""

from __future__ import annotations

import math

# The box has to tile exactly into cells. 200 by 200 in 10 m cells is the 400
# the design counts against, and 25 by 120 in 5 m cells is 120. A box that
# leaves a strip over has a denominator nobody wrote down, and the fraction
# then means whatever the remainder happened to be.
TILING_TOLERANCE_M = 1e-6


class GridError(ValueError):
    """The grid cannot be built as described, so no fraction of it is meaningful."""


class CoverageGrid:
    """Cells of a frozen box, and which of them a vehicle has actually seen."""

    def __init__(self, origin_x, origin_y, width_m, height_m, cell_m,
                 footprint_m):
        for name, value in (("width_m", width_m), ("height_m", height_m),
                            ("cell_m", cell_m), ("footprint_m", footprint_m)):
            if not (isinstance(value, (int, float)) and math.isfinite(value)
                    and value > 0):
                raise GridError(f"{name} is {value!r}; every grid dimension is "
                                f"a positive finite number")
        columns = width_m / cell_m
        rows = height_m / cell_m
        if (abs(columns - round(columns)) > TILING_TOLERANCE_M
                or abs(rows - round(rows)) > TILING_TOLERANCE_M):
            raise GridError(
                f"a {width_m} by {height_m} box does not tile into {cell_m} m "
                f"cells. The cell count is the denominator of every coverage "
                f"number, so a box with a strip left over has no denominator.")
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self.cell_m = float(cell_m)
        self.footprint_m = float(footprint_m)
        self.columns = int(round(columns))
        self.rows = int(round(rows))
        self._seen = set()

    @property
    def cell_count(self) -> int:
        return self.columns * self.rows

    def centre(self, column: int, row: int):
        return (self.origin_x + (column + 0.5) * self.cell_m,
                self.origin_y + (row + 0.5) * self.cell_m)

    def mark(self, x, y) -> int:
        """Record one pose sample. Returns how many new cells it covered.

        Only the cells whose index range the footprint can reach are tested.
        A survey run offers tens of thousands of samples and the box holds
        hundreds of cells, so the naive product is the difference between a
        checker that finishes and one nobody runs.
        """
        if not (math.isfinite(x) and math.isfinite(y)):
            return 0
        reach = self.footprint_m
        low_col = int(math.floor((x - reach - self.origin_x) / self.cell_m))
        high_col = int(math.ceil((x + reach - self.origin_x) / self.cell_m))
        low_row = int(math.floor((y - reach - self.origin_y) / self.cell_m))
        high_row = int(math.ceil((y + reach - self.origin_y) / self.cell_m))
        added = 0
        for column in range(max(0, low_col), min(self.columns, high_col + 1)):
            for row in range(max(0, low_row), min(self.rows, high_row + 1)):
                if (column, row) in self._seen:
                    continue
                cx, cy = self.centre(column, row)
                if math.hypot(cx - x, cy - y) <= reach:
                    self._seen.add((column, row))
                    added += 1
        return added

    @property
    def covered(self) -> int:
        return len(self._seen)

    def fraction(self) -> float:
        return self.covered / self.cell_count


def grid_from_spec(spec) -> CoverageGrid:
    """Build a grid from the block a scenario file carries.

    Keys are the names architecture.md section 6 uses for the frozen boxes.
    A missing key raises rather than defaulting, because a default here is a
    survey area nobody chose being scored as though somebody had.
    """
    if not isinstance(spec, dict):
        raise GridError("the coverage block is not an object")
    wanted = ("origin_x", "origin_y", "width_m", "height_m", "cell_m",
              "footprint_m")
    missing = [key for key in wanted if key not in spec]
    if missing:
        raise GridError(
            f"the coverage block is missing {', '.join(missing)}. Every "
            f"dimension of the box is frozen in architecture.md section 6 and "
            f"none of them has a sensible default.")
    return CoverageGrid(*(spec[key] for key in wanted))
