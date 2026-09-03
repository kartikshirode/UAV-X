"""What the partitioner has to be true of, rather than how it computes it.

A test that divides 200 by 4 and asserts the partitioner also divided 200 by
4 agrees with the code forever, including on the day the code is wrong. The
week 1 audit rated two of that week's suites behavioural and one a mirror,
and said what the difference is: assert the property the rest of the system
leans on, and get it from somewhere the implementation cannot reach.

So nothing below recomputes a strip edge. Each test states something that has
to hold of any correct partition of a rectangle:

* the strips tile the box, so their areas add up to it exactly
* no two of them overlap
* every point in the box, and every cell centre the coverage metric scores,
  belongs to exactly one strip
* the four strips are within a tolerance of equal area
* the answer does not depend on the order the vehicle ids arrived in

The seam cases are the ones worth the most. A point sitting exactly on a cut
is where a partition usually goes wrong, and it goes wrong quietly: closed
intervals on both sides give the seam to two vehicles and the coverage
fraction still reads full.
"""

import math
import random

import pytest

from uavx_mission.partition import (PartitionError, Strip, partition, sort_key,
                                    strip_for, strip_of)
from uavx_mission.survey_area import (BASELINE_STRIP_COUNT, SurveyArea,
                                      SurveyAreaError)

VEHICLES = ("uav_1", "uav_2", "uav_3", "uav_4")

# The strips exist to divide the work evenly, so the only allowance is the
# floating point error of computing an edge. Anything larger is a partition
# that gives one vehicle more ground than another and calls it equal.
EQUAL_AREA_TOLERANCE = 1e-9


def baseline_strips():
    return partition(SurveyArea.baseline(), VEHICLES)


def test_the_frozen_box_gives_one_strip_to_each_of_the_four_vehicles():
    strips = baseline_strips()
    assert len(strips) == BASELINE_STRIP_COUNT
    assert [s.vehicle_id for s in strips] == list(VEHICLES)
    assert [s.index for s in strips] == list(range(BASELINE_STRIP_COUNT))
    # West to east, which is what makes "the strip east of the cut" a
    # statement anybody can check on a map.
    assert all(a.x_min < b.x_min for a, b in zip(strips, strips[1:]))


def test_the_strips_tile_the_box_with_no_seam_and_no_gap():
    area = SurveyArea.baseline()
    strips = partition(area, VEHICLES)

    assert strips[0].x_min == area.x_min
    assert strips[-1].x_max == area.x_max
    # Exact float equality, not an approximation. A shared edge computed two
    # different ways leaves a gap narrower than a millimetre, which is wide
    # enough to lose a point that lands on it and far too narrow to see.
    for west, east in zip(strips, strips[1:]):
        assert west.x_max == east.x_min

    assert all(s.y_min == area.y_min and s.y_max == area.y_max for s in strips)
    assert math.isclose(sum(s.area_m2 for s in strips), area.area_m2,
                        rel_tol=EQUAL_AREA_TOLERANCE)


def test_no_two_strips_overlap():
    strips = baseline_strips()
    for i, a in enumerate(strips):
        for b in strips[i + 1:]:
            overlap = min(a.x_max, b.x_max) - max(a.x_min, b.x_min)
            assert overlap <= 0, (
                f"{a.vehicle_id} and {b.vehicle_id} share {overlap} m of x, so "
                f"two vehicles are assigned the same ground")


def test_every_point_in_the_box_belongs_to_exactly_one_strip():
    """Including the cuts, the corners and the edges.

    The interesting points are not the interior ones. They are the four
    values of x where two strips meet and the two where the box ends, so the
    sample deliberately contains those exactly rather than hoping a grid
    lands on them.
    """
    area = SurveyArea.baseline()
    strips = partition(area, VEHICLES)

    xs = sorted({area.x_min, area.x_max}
                | {s.x_min for s in strips} | {s.x_max for s in strips}
                | {area.x_min + area.width * k / 37.0 for k in range(38)})
    ys = sorted({area.y_min, area.y_max}
                | {area.y_min + area.height * k / 11.0 for k in range(12)})

    for x in xs:
        for y in ys:
            owners = [s.vehicle_id for s in strips if s.contains(x, y)]
            assert len(owners) == 1, (
                f"({x}, {y}) is inside the box and belongs to {owners}")


def test_a_point_on_a_cut_belongs_to_the_strip_east_of_it():
    """The convention, stated once so it cannot drift into the other one."""
    strips = baseline_strips()
    for west, east in zip(strips, strips[1:]):
        cut = west.x_max
        assert not west.contains(cut, 0.0)
        assert east.contains(cut, 0.0)


def test_the_eastmost_strip_owns_the_eastern_edge_of_the_box():
    area = SurveyArea.baseline()
    strips = partition(area, VEHICLES)
    assert strips[-1].contains(area.x_max, 0.0)
    assert sum(1 for s in strips if s.owns_east_edge) == 1


def test_points_outside_the_box_belong_to_no_strip():
    area = SurveyArea.baseline()
    strips = partition(area, VEHICLES)
    outside = [
        (area.x_min - 0.001, 0.0),
        (area.x_max + 0.001, 0.0),
        (area.x_min + 1.0, area.y_min - 0.001),
        (area.x_min + 1.0, area.y_max + 0.001),
    ]
    for x, y in outside:
        assert strip_for(strips, x, y) is None, f"({x}, {y}) is outside the box"


def test_the_strips_are_within_tolerance_of_equal_area():
    areas = [s.area_m2 for s in baseline_strips()]
    spread = (max(areas) - min(areas)) / max(areas)
    assert spread <= EQUAL_AREA_TOLERANCE, (
        f"strip areas {areas} differ by {spread:.3e} of the largest, so the "
        f"survey is not shared evenly")


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 7])
def test_an_uneven_division_still_tiles_exactly_and_shares_evenly(count):
    """The case that catches an edge computed by walking east one width at a time.

    Three strips over a 200 m box is 66.666... m each, and adding that to
    itself three times does not land on 200. The last strip then stops short
    of the box by a rounding, which loses every point on the eastern edge and
    is invisible in any figure printed to a metre.
    """
    area = SurveyArea.baseline()
    vehicles = [f"uav_{i + 1}" for i in range(count)]
    strips = partition(area, vehicles)

    assert len(strips) == count
    assert strips[0].x_min == area.x_min
    assert strips[-1].x_max == area.x_max
    for west, east in zip(strips, strips[1:]):
        assert west.x_max == east.x_min

    areas = [s.area_m2 for s in strips]
    assert (max(areas) - min(areas)) / max(areas) <= EQUAL_AREA_TOLERANCE
    assert math.isclose(sum(areas), area.area_m2, rel_tol=EQUAL_AREA_TOLERANCE)

    # And the tiling still holds where it is hardest, on the cuts themselves.
    for s in strips:
        for x in (s.x_min, s.x_max):
            owners = [t.vehicle_id for t in strips if t.contains(x, 0.0)]
            assert len(owners) == 1, f"x = {x} belongs to {owners}"


def test_every_cell_the_coverage_metric_scores_has_exactly_one_owner():
    """The property stated in the metric's own terms.

    coverage_fraction counts cell centres, so the partition has to be a
    partition of the cell centres and not only of the plane. A cut that falls
    on a cell centre would be the case that breaks it, which is why this is
    asserted rather than inferred from the tiling above.
    """
    area = SurveyArea.baseline()
    strips = partition(area, VEHICLES)
    counted = 0
    for x, y in area.cell_centres():
        owners = [s.vehicle_id for s in strips if s.contains(x, y)]
        assert len(owners) == 1, f"cell centre ({x}, {y}) belongs to {owners}"
        counted += 1
    assert counted == area.cell_count


def test_the_partition_does_not_depend_on_the_order_the_ids_arrive_in():
    """Standing rule 3: a run has to replay exactly.

    The runner reads its vehicle list out of a scenario file, and anything
    between that file and here is free to reorder it. A partitioner that
    handed out strips in arrival order would give a different survey per run
    and the coverage figures would not be comparable between them.
    """
    area = SurveyArea.baseline()
    reference = partition(area, VEHICLES)
    shuffled = list(VEHICLES)
    rng = random.Random(17)
    for _ in range(20):
        rng.shuffle(shuffled)
        assert partition(area, shuffled) == reference


def test_ids_are_ordered_on_their_number_and_not_as_text():
    """`uav_10` follows `uav_9`, and a plain string sort does not do that."""
    ids = ["uav_10", "uav_2", "uav_1", "uav_9"]
    assert sorted(ids, key=sort_key) == ["uav_1", "uav_2", "uav_9", "uav_10"]
    strips = partition(SurveyArea.baseline(), ids)
    assert [s.vehicle_id for s in strips] == ["uav_1", "uav_2", "uav_9", "uav_10"]


def test_an_id_with_no_number_still_orders_deterministically():
    ids = ["gcs", "uav_2", "uav_1"]
    once = partition(SurveyArea.baseline(), ids)
    twice = partition(SurveyArea.baseline(), list(reversed(ids)))
    assert once == twice


def test_an_empty_or_repeating_vehicle_list_is_refused():
    area = SurveyArea.baseline()
    with pytest.raises(PartitionError):
        partition(area, [])
    with pytest.raises(PartitionError) as excinfo:
        partition(area, ["uav_1", "uav_2", "uav_1"])
    assert "uav_1" in str(excinfo.value)


def test_asking_for_a_vehicle_that_holds_no_strip_names_it():
    strips = baseline_strips()
    assert strip_of(strips, "uav_3").vehicle_id == "uav_3"
    with pytest.raises(PartitionError) as excinfo:
        strip_of(strips, "uav_9")
    assert "uav_9" in str(excinfo.value)


def test_an_area_the_grid_does_not_divide_is_refused():
    """A cell count that is a rounding is a denominator nobody agreed to."""
    with pytest.raises(SurveyAreaError):
        SurveyArea.from_corner((0.0, 0.0), 205.0, 200.0, 10.0, 12.0)
    with pytest.raises(SurveyAreaError):
        SurveyArea.from_corner((0.0, 0.0), 200.0, 200.0, 10.0, 0.0)
    with pytest.raises(SurveyAreaError):
        SurveyArea.from_corner((0.0, 0.0), 0.0, 200.0, 10.0, 12.0)


def test_a_closed_seam_on_both_sides_would_be_caught():
    """The mutation the seam tests exist for, run against a hand-built strip.

    Without this the two seam tests above could both be passing because the
    partitioner never puts a point on a cut, rather than because it resolves
    one correctly. This builds the wrong convention directly and requires the
    same assertions to fail on it.
    """
    area = SurveyArea.baseline()
    cut = area.x_min + area.width / 2
    both_closed = (
        Strip("uav_1", 0, area.x_min, cut, area.y_min, area.y_max, True),
        Strip("uav_2", 1, cut, area.x_max, area.y_min, area.y_max, True),
    )
    owners = [s.vehicle_id for s in both_closed if s.contains(cut, 0.0)]
    assert len(owners) == 2, (
        "the mutation did not reproduce a shared seam, so the seam assertions "
        "above are not being exercised")
