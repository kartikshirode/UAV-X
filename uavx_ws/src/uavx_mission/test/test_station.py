"""Chunk 3.4: the ways a station can be wrong before a vehicle is sent to one.

Every one of these produces a run where the vehicles fly, the radio works and
the geometry the whole relay claim rests on is not the geometry in
architecture.md section 6. That is the expensive kind of wrong, because
nothing downstream notices: a swarm station-keeping 10 m off its layer still
delivers packets and still writes a record.

    python3 -m pytest -q uavx_ws/src/uavx_mission/test/test_station.py

Runs on a clean checkout with nothing built.
"""

import math
from fractions import Fraction

import pytest

from uavx_mission.station import ALTITUDE_TOLERANCE_M, StationError, station_of, unset

# The far surveyor's row of the frozen table. Built as numbers rather than
# read from a scenario on purpose: this file is the thing the scenario is
# checked against, so reading the scenario would make it agree with itself.
FAR = (475.0, -75.0, 60.0)


def test_a_station_survives_unchanged():
    assert station_of(list(FAR), altitude_m=60.0) == FAR


def test_integers_come_back_as_floats():
    """YAML writes 30 and the node declares a double array."""
    got = station_of([165, 0, 30], altitude_m=30.0)
    assert got == (165.0, 0.0, 30.0)
    assert all(isinstance(v, float) for v in got)


def test_the_default_means_no_station():
    assert station_of(unset()) is None
    assert station_of(None) is None


def test_all_three_unset_is_a_survey_and_not_an_error():
    assert station_of([math.nan] * 3, altitude_m=30.0) is None


def test_a_half_set_station_is_refused():
    with pytest.raises(StationError, match="two of three"):
        station_of([475.0, math.nan, 60.0])


def test_a_station_that_is_not_three_numbers_is_refused():
    for values in ([], [1.0], [1.0, 2.0], [1.0, 2.0, 3.0, 4.0]):
        with pytest.raises(StationError, match="three numbers"):
            station_of(values)


@pytest.mark.parametrize("junk", ["475", None, True, [1, 2]])
def test_a_component_that_is_not_a_number_is_not_a_position(junk):
    values = [475.0, -75.0, junk]
    if junk is None:
        # A None in the list is unset, and the other two are set.
        with pytest.raises(StationError, match="two of three"):
            station_of(values)
        return
    with pytest.raises(StationError, match="two of three"):
        station_of(values)


def test_a_boolean_is_never_a_coordinate():
    """True is an int in Python and would read as 1 m."""
    with pytest.raises(StationError):
        station_of([475.0, -75.0, True])


# ------------------------------------------------- the altitude cross-check
def test_a_station_disagreeing_with_the_climb_is_refused():
    with pytest.raises(StationError, match="what the run flies"):
        station_of(list(FAR), altitude_m=50.0)


def test_the_disagreement_names_both_numbers():
    """Whoever hits this has two files open and needs to know which to edit."""
    with pytest.raises(StationError) as caught:
        station_of(list(FAR), altitude_m=50.0)
    assert "60.0" in str(caught.value)
    assert "50.0" in str(caught.value)


def test_float_noise_between_yaml_and_a_ros_double_is_not_a_disagreement():
    assert station_of([475.0, -75.0, 60.0 + 1e-9], altitude_m=60.0) is not None


def test_the_tolerance_is_far_under_the_layer_spacing():
    """A whole layer apart must never pass, whatever the tolerance becomes.

    The layers are 10 m apart and the separation floor is the same 10 m, so a
    tolerance that ever grew past half of one layer would let a vehicle hold
    station in its neighbour's band and still be accepted here.
    """
    assert 0 < ALTITUDE_TOLERANCE_M < 5.0
    with pytest.raises(StationError):
        station_of([475.0, -75.0, 60.0], altitude_m=60.0 - 10.0)


def test_an_unset_station_ignores_the_altitude_entirely():
    """A surveying vehicle has a layer altitude and no station to check."""
    assert station_of(unset(), altitude_m=30.0) is None


def test_a_station_with_no_altitude_to_check_against_is_refused():
    with pytest.raises(StationError, match="nothing to be checked against"):
        station_of(list(FAR), altitude_m=math.nan)


# ------------------------------------- a coordinate is a real number, not a float
def test_a_real_number_that_is_not_a_float_is_still_a_coordinate():
    """Chunk 4.2, and it cost a whole run to find.

    `RoleAssignment.slot` is a float32[3] and rclpy hands a subscriber a
    numpy array of float32, which is not a Python float. Every component of a
    perfectly good relay slot was read as unusable, three unusable numbers
    are "no station", and the mission executor treated a command to fly 195 m
    as "as you were". Nothing raised and nothing was logged. Fraction stands
    in for numpy here so the test needs no dependency.
    """
    got = station_of([Fraction(475), Fraction(-75), Fraction(60)],
                     altitude_m=60.0)
    assert got == FAR


def test_the_message_type_the_role_manager_actually_sends():
    numpy = pytest.importorskip("numpy")
    got = station_of(list(numpy.array(FAR, dtype=numpy.float32)),
                     altitude_m=60.0)
    assert got == pytest.approx(FAR)
    assert all(isinstance(v, float) for v in got)


def test_flags_are_still_not_a_position():
    # bool is an int and an int is a real number, so the guard against it has
    # to be explicit.
    assert station_of([True, True, True]) is None
