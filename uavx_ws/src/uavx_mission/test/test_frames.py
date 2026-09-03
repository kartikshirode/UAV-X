"""The two frame errors that produce a plausible looking survey.

Week 1 audit finding 11: the record carries a home altitude per vehicle and no
home x or y, position arrives in PX4's local frame whose origin is each
vehicle's own spawn point, and the design fixes every coordinate in one frame
with the ground station at the origin. The week that converts poses into that
frame needs the offsets and this is that week.

Nothing here restates the conversion. Each test states something that has to
be true of any correct one and is false for one of the two ways it usually
goes wrong: forgetting the origin, and reordering the axes on the wrong side
of the subtraction. Both failures return a survey box of the right shape in
the wrong place, which is why neither shows up in a plot of the plan.
"""

import math

import pytest

from uavx_mission.frames import (enu_to_ned, frozen_to_px4, ned_to_enu,
                                 px4_to_frozen)

# Deliberately asymmetric in all three axes. A home like (10, 10, 0) makes the
# axis swap invisible, and a home at the origin makes the origin invisible.
HOME = (165.0, -37.5, 30.0)
ORIGIN = (0.0, 0.0, 0.0)


def test_a_vehicle_at_its_own_home_is_at_the_origin_of_its_local_frame():
    """The property that catches subtracting the home after the axis swap.

    The wrong order returns (home_north - home_east, home_east - home_north,
    ...) here, which is zero only when the home happens to be on the diagonal
    or at the origin. One of the four vehicles spawns at the origin, so this
    is a bug that works on the vehicle anybody would test first.
    """
    assert frozen_to_px4(HOME, HOME) == pytest.approx((0.0, 0.0, 0.0))
    assert px4_to_frozen((0.0, 0.0, 0.0), HOME) == pytest.approx(HOME)


def test_a_step_north_in_the_frozen_frame_is_a_step_along_px4_x():
    north = (HOME[0], HOME[1] + 1.0, HOME[2])
    assert frozen_to_px4(north, HOME) == pytest.approx((1.0, 0.0, 0.0))


def test_a_step_east_in_the_frozen_frame_is_a_step_along_px4_y():
    east = (HOME[0] + 1.0, HOME[1], HOME[2])
    assert frozen_to_px4(east, HOME) == pytest.approx((0.0, 1.0, 0.0))


def test_climbing_in_the_frozen_frame_is_descending_in_px4():
    """z is up here and down there, and the sign is the whole of the bug."""
    up = (HOME[0], HOME[1], HOME[2] + 5.0)
    assert frozen_to_px4(up, HOME) == pytest.approx((0.0, 0.0, -5.0))


def test_px4_reporting_a_metre_of_x_reads_as_a_metre_north():
    """The direction the coverage metric needs, stated on its own.

    A pair of conversions can be wrong in the same way and still round trip
    perfectly, so the inverse has to be checked against the world rather than
    against its own partner.
    """
    assert px4_to_frozen((1.0, 0.0, 0.0), HOME) == pytest.approx(
        (HOME[0], HOME[1] + 1.0, HOME[2]))
    assert px4_to_frozen((0.0, 1.0, 0.0), HOME) == pytest.approx(
        (HOME[0] + 1.0, HOME[1], HOME[2]))
    assert px4_to_frozen((0.0, 0.0, -1.0), HOME) == pytest.approx(
        (HOME[0], HOME[1], HOME[2] + 1.0))


def test_the_conversion_round_trips_for_points_all_over_the_survey_box():
    for x in (375.0, 431.7, 575.0):
        for y in (-100.0, 3.5, 100.0):
            for z in (30.0, 60.0):
                point = (x, y, z)
                there = frozen_to_px4(point, HOME)
                assert px4_to_frozen(there, HOME) == pytest.approx(point)


def test_distances_survive_the_conversion():
    """Whatever else the conversion does, it must not stretch the world.

    A separation floor, a link range and a survey lane are all distances, and
    every one of them is asserted in the frozen frame and flown in the local
    one.
    """
    a, b = (375.0, -100.0, 30.0), (575.0, 100.0, 60.0)
    assert math.dist(frozen_to_px4(a, HOME), frozen_to_px4(b, HOME)) == \
        pytest.approx(math.dist(a, b))


def test_two_vehicles_with_different_homes_are_sent_to_the_same_ground():
    """The finding, stated as what it costs.

    The same frozen waypoint has to be the same place in the world for every
    vehicle. Handing a plan straight to PX4 as a setpoint makes each vehicle
    fly the box relative to wherever it spawned, and four vehicles then fly
    four different boxes that each look correct in their own log.
    """
    waypoint = (400.0, 25.0, 50.0)
    other_home = (165.0, 37.5, 30.0)
    here = frozen_to_px4(waypoint, HOME)
    there = frozen_to_px4(waypoint, other_home)
    assert here != there
    assert px4_to_frozen(here, HOME) == pytest.approx(
        px4_to_frozen(there, other_home))


def test_the_axis_swap_is_its_own_inverse():
    v = (1.0, 2.0, 3.0)
    assert ned_to_enu(enu_to_ned(v)) == pytest.approx(v)
    assert enu_to_ned(enu_to_ned(v)) == pytest.approx(v)
