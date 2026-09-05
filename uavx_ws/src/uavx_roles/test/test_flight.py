"""Chunk 4.1: where a grant puts the aircraft, and where the absence of one does.

The rule worth testing is the one that is easy to get wrong in the safe
direction: a vehicle always has somewhere to be. A lapsed grant that left an
aircraft with no destination would leave it in the reserved relay band with
nothing commanding it, and the run record would still say the role came back.

    python3 -m pytest -q uavx_ws/src/uavx_roles/test/test_flight.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_comms import election as el
from uavx_roles.flight import ARRIVAL_M, FlightError, arrived, gap_m, where_to
from uavx_roles.grant import Grant

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

SLOT = (317.3, -36.8, 75.0)
STATION = (475.0, 75.0, 50.0)


def grant(role=el.ROLE_RELAY, slot=SLOT, expires=30.0, epoch=1):
    return Grant(epoch=epoch, node_id=NEAR, role=role, slot=slot,
                 lease_expires_at=expires, sender_id=FAR)


# --------------------------------------------------------------- where to be
def test_a_live_relay_grant_sends_the_vehicle_to_the_slot():
    assert where_to(grant(), STATION, 10.0) == SLOT


def test_no_grant_leaves_the_vehicle_on_its_own_station():
    assert where_to(None, STATION, 10.0) == STATION


def test_a_lapsed_grant_gives_the_station_back():
    # The whole recovery from a dead epoch owner. Nothing detects the death.
    assert where_to(grant(expires=30.0), STATION, 30.1) == STATION


def test_a_grant_that_is_not_the_relay_role_changes_nothing():
    assert where_to(grant(role=el.ROLE_SURVEY), STATION, 1.0) == STATION


def test_a_surveyor_with_no_station_keeps_its_own_plan():
    # None is a real answer: the mission executor owns the strip and nothing
    # here overrides it.
    assert where_to(None, None, 1.0) is None


def test_a_surveyor_still_flies_to_a_slot_it_was_granted():
    assert where_to(grant(), None, 1.0) == SLOT


def test_a_relay_grant_with_no_slot_is_refused():
    with pytest.raises(FlightError) as caught:
        where_to(grant(slot=None), STATION, 1.0)
    assert "names no slot" in str(caught.value)


@pytest.mark.parametrize("bad", [(1.0, 2.0), "here", (1.0, 2.0, None),
                                 (1.0, 2.0, float("nan")), (1, 2, True)])
def test_a_station_that_is_not_three_numbers_is_refused(bad):
    with pytest.raises(FlightError):
        where_to(None, bad, 1.0)


# ----------------------------------------------------------------- arriving
def test_a_vehicle_on_its_point_has_arrived():
    assert arrived(SLOT, SLOT) is True


def test_a_vehicle_inside_the_radius_has_arrived():
    near = (SLOT[0] + ARRIVAL_M - 0.1, SLOT[1], SLOT[2])
    assert arrived(near, SLOT) is True


def test_a_vehicle_outside_the_radius_has_not():
    far = (SLOT[0] + ARRIVAL_M + 0.1, SLOT[1], SLOT[2])
    assert arrived(far, SLOT) is False


def test_a_vehicle_asked_for_nothing_has_arrived():
    assert arrived(STATION, None) is True


def test_the_radius_can_be_tightened_by_a_caller():
    near = (SLOT[0] + 1.0, SLOT[1], SLOT[2])
    assert arrived(near, SLOT, tolerance=0.5) is False


def test_the_gap_is_the_distance_left_to_fly():
    away = (SLOT[0] + 3.0, SLOT[1] + 4.0, SLOT[2])
    assert gap_m(away, SLOT) == pytest.approx(5.0)
    assert gap_m(away, None) is None
