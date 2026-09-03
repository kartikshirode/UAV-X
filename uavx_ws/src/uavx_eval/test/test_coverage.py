"""The coverage grid, which is the 25% mission row.

Every dimension here is made up on purpose. The two frozen survey boxes live in
architecture.md section 6 and arrive in the scenario file, and a test that
wrote them out again would agree with itself forever while the design moved on.
What is tested is the arithmetic: a box that does not tile is refused, a cell
counts when its centre is inside the footprint and not before, and the same
sample twice adds nothing.
"""

import pytest

from uavx_eval.coverage import CoverageGrid, GridError, grid_from_spec


def square(cell=1.0, footprint=0.6, side=4.0):
    return CoverageGrid(origin_x=0.0, origin_y=0.0, width_m=side,
                        height_m=side, cell_m=cell, footprint_m=footprint)


def test_the_cell_count_is_the_denominator():
    grid = square()
    assert (grid.columns, grid.rows) == (4, 4)
    assert grid.cell_count == 16
    assert grid.fraction() == 0.0


def test_a_box_that_does_not_tile_is_refused():
    """The cell count is the denominator, so a strip left over has no answer."""
    with pytest.raises(GridError):
        CoverageGrid(0.0, 0.0, width_m=25.0, height_m=10.0, cell_m=4.0,
                     footprint_m=1.0)


@pytest.mark.parametrize("bad", ["width_m", "height_m", "cell_m", "footprint_m"])
def test_a_dimension_that_is_not_a_positive_number_is_refused(bad):
    spec = {"origin_x": 0.0, "origin_y": 0.0, "width_m": 4.0, "height_m": 4.0,
            "cell_m": 1.0, "footprint_m": 1.0}
    spec[bad] = 0.0
    with pytest.raises(GridError):
        grid_from_spec(spec)


def test_a_missing_dimension_is_refused_rather_than_defaulted():
    """A default here is a survey area nobody chose being scored as if chosen."""
    spec = {"origin_x": 0.0, "origin_y": 0.0, "width_m": 4.0, "height_m": 4.0,
            "cell_m": 1.0}
    with pytest.raises(GridError) as raised:
        grid_from_spec(spec)
    assert "footprint_m" in str(raised.value)


def test_a_cell_counts_only_when_its_centre_is_inside_the_footprint():
    """The rule from architecture.md section 6, and its boundary.

    One cell, so the distance being tested is the distance to that centre and
    not to whichever neighbour happened to be nearer.
    """
    def one_cell():
        return CoverageGrid(0.0, 0.0, width_m=1.0, height_m=1.0, cell_m=1.0,
                            footprint_m=0.5)

    assert one_cell().centre(0, 0) == (0.5, 0.5)
    # Every distance here is exact in binary, so the boundary case is testing
    # the comparison rather than the last bit of a subtraction.
    assert one_cell().mark(0.5, 1.0) == 1          # exactly on the edge
    assert one_cell().mark(0.5, 1.25) == 0         # just outside it


def test_a_sample_reaching_two_centres_covers_both():
    """The footprint is a disc, not the cell the vehicle happens to be over."""
    grid = square(cell=1.0, footprint=0.6)
    assert grid.mark(0.5, 1.0) == 2
    assert grid.covered == 2


def test_the_footprint_ignores_altitude():
    """It is a disc on the ground, so only x and y are passed in at all."""
    grid = square()
    first = grid.mark(0.5, 0.5)
    again = grid.mark(0.5, 0.5)
    assert (first, again) == (1, 0)
    assert grid.covered == 1


def test_a_sample_outside_the_box_covers_nothing():
    grid = square()
    assert grid.mark(-50.0, -50.0) == 0
    assert grid.mark(float("nan"), 0.0) == 0
    assert grid.fraction() == 0.0


def test_flying_every_cell_centre_reaches_one():
    grid = square(cell=1.0, footprint=0.4)
    for column in range(grid.columns):
        for row in range(grid.rows):
            grid.mark(*grid.centre(column, row))
    assert grid.covered == grid.cell_count
    assert grid.fraction() == 1.0


def test_a_partial_sweep_is_a_partial_fraction():
    """A fraction that cannot fall short is not measuring anything."""
    grid = square(cell=1.0, footprint=0.4)
    for column in range(grid.columns):
        grid.mark(*grid.centre(column, 0))
    assert grid.covered == grid.columns
    assert grid.fraction() == pytest.approx(grid.columns / grid.cell_count)
