"""Chunk 4.1: what the run record says a role did, and what it may not say.

Four gate requirements sit on this module and each of them can be satisfied
by something that never happened:

    relay_role_moved            true because a message was sent
    relay_role_holder           named from an election result
    relay_role_released         true because a RELEASE was decoded
    mover_returned_to_station   assumed from the release

Here `moved` needs an arrival, `returned_to_station` needs the aircraft back,
and a lapse is recorded apart from a release because they mean opposite
things: one is the swarm deciding it is finished with the relay, the other is
the swarm having stopped talking.

    python3 -m pytest -q uavx_ws/src/uavx_roles/test/test_trace.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_roles.trace import RoleTrace, swarm_trace

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

SLOT = (317.3, -36.8, 75.0)


def a_trace(node=NEAR):
    return RoleTrace(node)


def flown(node=NEAR, epoch=1, released=True, returned=True):
    trace = a_trace(node)
    trace.granted(epoch, SLOT, 126.0)
    trace.reached_slot(146.0)
    if released:
        trace.released(250.0)
    if returned:
        trace.returned_home(270.0)
    return trace.as_record()


# ------------------------------------------------------------ one vehicle
def test_a_vehicle_that_was_never_granted_anything_did_nothing():
    row = a_trace().as_record()
    assert row["moved"] is False
    assert row["released"] is False
    assert row["returned_to_station"] is False
    assert row["epoch"] is None


def test_a_grant_alone_is_not_a_move():
    # The vehicle was told to go. It has not gone.
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    assert trace.moved is False
    assert trace.as_record()["slot_commanded"] == list(SLOT)


def test_the_move_needs_an_arrival():
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    assert trace.moved is True
    assert trace.as_record()["arrived_at"] == 146.0


def test_the_first_arrival_is_the_one_recorded():
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    trace.reached_slot(160.0)
    assert trace.as_record()["arrived_at"] == 146.0


def test_an_arrival_with_no_grant_is_not_recorded():
    trace = a_trace()
    trace.reached_slot(146.0)
    assert trace.moved is False


def test_a_second_epoch_starts_the_story_again():
    # Carrying the first epoch's arrival forward would report a vehicle as
    # having reached a slot it was never sent to.
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    trace.released(200.0)
    trace.returned_home(220.0)
    trace.granted(2, (1.0, 2.0, 75.0), 300.0)
    row = trace.as_record()
    assert row["epoch"] == 2
    assert row["arrived_at"] is None
    assert row["released_at"] is None
    assert row["returned_at"] is None
    assert row["grants"] == 2


def test_a_repeated_grant_for_one_epoch_is_one_grant():
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    trace.granted(1, SLOT, 150.0)
    row = trace.as_record()
    assert row["grants"] == 1
    assert row["arrived_at"] == 146.0


def test_the_return_needs_a_release_first():
    # A vehicle that drifted back onto its station while it still held the
    # relay role has not been given back.
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    trace.returned_home(200.0)
    assert trace.came_home is False


def test_a_lapse_is_recorded_apart_from_a_release():
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    trace.reached_slot(146.0)
    trace.lapsed(200.0)
    row = trace.as_record()
    assert row["lapsed_at"] == 200.0
    assert row["released"] is False


def test_renewals_are_counted():
    trace = a_trace()
    trace.granted(1, SLOT, 126.0)
    for _ in range(3):
        trace.renewed()
    assert trace.as_record()["renewals"] == 3


# ------------------------------------------------------------- the swarm
def test_a_swarm_where_nobody_moved():
    rows = [a_trace(v).as_record() for v in VEHICLES]
    got = swarm_trace(rows)
    assert got["relay_role_moved"] is False
    assert got["relay_role_holder"] is None
    assert got["relay_role_released"] is False
    assert got["mover_returned_to_station"] is False


def test_the_holder_is_read_off_the_vehicle_that_flew():
    rows = [a_trace(ANCHOR).as_record(), flown(NEAR), a_trace(FAR).as_record()]
    got = swarm_trace(rows)
    assert got["relay_role_moved"] is True
    assert got["relay_role_holder"] == NEAR
    assert got["relay_role_released"] is True
    assert got["mover_returned_to_station"] is True
    assert got["relay_role_arrived_at_s"] == 146.0


def test_a_relay_that_never_came_back_says_so():
    got = swarm_trace([flown(NEAR, released=False, returned=False)])
    assert got["relay_role_moved"] is True
    assert got["relay_role_released"] is False
    assert got["mover_returned_to_station"] is False


def test_a_relay_released_but_still_out_there_says_so():
    got = swarm_trace([flown(NEAR, returned=False)])
    assert got["relay_role_released"] is True
    assert got["mover_returned_to_station"] is False


def test_two_vehicles_reporting_the_role_is_refused():
    # One slot was computed and one vehicle was assigned to it, so a second
    # holder means an epoch was decided twice.
    with pytest.raises(ValueError) as caught:
        swarm_trace([flown(NEAR), flown(FAR)])
    assert NEAR in str(caught.value) and FAR in str(caught.value)


def test_a_vehicle_granted_the_role_without_flying_is_not_the_holder():
    trace = a_trace(NEAR)
    trace.granted(1, SLOT, 126.0)
    got = swarm_trace([trace.as_record()])
    assert got["relay_role_moved"] is False
    assert got["relay_role_holder"] is None
