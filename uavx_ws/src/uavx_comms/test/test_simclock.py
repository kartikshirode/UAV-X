"""Chunk 4.2: the gate that stops a node stamping a packet at the origin of time.

Found in an accepted run rather than in a test, which is why the docstring in
`simclock.py` names it. The ground station reported a control queue delay of
113.3 s in a scenario with no outage. Nothing was queued: 113.3 s is the
simulated time at which the radio came up, and the packet reporting it had
been stamped 0 because it arrived before the node's first `/clock` message.

Two behaviours are worth holding here. A reading of zero is not a time, and a
packet that arrives before there is a time is held rather than dropped: a
dropped observation is a delivery ratio wrong in the flattering direction,
and this project has spent three weeks removing those.

    python3 -m pytest -q uavx_ws/src/uavx_comms/test/test_simclock.py

Runs on a clean checkout with nothing built.
"""

import math

import pytest

from uavx_comms.simclock import (DROPPED_FULL, HELD, HOLD_CAPACITY, ClockGate)


def test_a_zero_reading_is_not_a_clock():
    gate = ClockGate()
    assert gate.sample(0.0) is False
    assert gate.live is False


def test_the_first_positive_reading_makes_it_live():
    gate = ClockGate()
    gate.sample(0.0)
    assert gate.sample(113.3) is True
    assert gate.live is True
    assert gate.live_at == pytest.approx(113.3)


def test_once_live_it_stays_live():
    # A clock going back to zero is a simulator restarting under a node that
    # still holds the previous run's state. Two runs must not share a timeline.
    gate = ClockGate()
    gate.sample(113.3)
    assert gate.sample(0.0) is True
    assert gate.live_at == pytest.approx(113.3)


@pytest.mark.parametrize("bad", [None, "12.0", float("nan"), float("inf"),
                                 True, -1.0])
def test_a_reading_that_is_not_a_time_leaves_it_shut(bad):
    gate = ClockGate()
    assert gate.sample(bad) is False
    assert gate.live is False


def test_readings_taken_before_the_clock_are_counted():
    gate = ClockGate()
    for _ in range(4):
        gate.sample(0.0)
    gate.sample(1.0)
    assert gate.as_record()["readings_before_clock"] == 4


def test_wall_time_is_live_immediately():
    # Seconds since 1970. A node not on simulated time is never held up.
    assert ClockGate().sample(1_757_000_000.0) is True


# -------------------------------------------------------------- the holding
def test_what_arrives_early_is_held_and_given_back_in_order():
    gate = ClockGate()
    for item in ("hello", "lsa", "observation"):
        assert gate.hold(item) == HELD
    assert gate.release() == ["hello", "lsa", "observation"]


def test_releasing_twice_gives_nothing_the_second_time():
    gate = ClockGate()
    gate.hold("hello")
    assert gate.release() == ["hello"]
    assert gate.release() == []


def test_a_full_buffer_drops_and_says_so():
    # A node with no clock at all fills a bounded buffer rather than growing
    # until the run dies of memory, and the ledger carries the count.
    gate = ClockGate(capacity=3)
    for _ in range(3):
        assert gate.hold("x") == HELD
    assert gate.hold("x") == DROPPED_FULL
    row = gate.as_record()
    assert row["held_before_clock"] == 3
    assert row["dropped_waiting_for_clock"] == 1


def test_a_gate_that_can_hold_nothing_is_refused():
    with pytest.raises(ValueError):
        ClockGate(capacity=0)


def test_the_default_capacity_covers_the_settle_window():
    # The radio settles for 8 s before a scenario starts and the traffic in
    # that window is discovery. Nothing needs to be exact here; what would be
    # wrong is a capacity of a handful.
    assert HOLD_CAPACITY >= 128


# --------------------------------------------------------------- the record
def test_the_record_says_nothing_happened_when_nothing_did():
    row = ClockGate().as_record()
    assert row["clock_live"] is False
    assert row["clock_live_at_s"] is None
    assert row["held_before_clock"] == 0
    assert row["dropped_waiting_for_clock"] == 0


def test_the_record_carries_when_the_clock_arrived():
    gate = ClockGate()
    gate.hold("hello")
    gate.sample(0.0)
    gate.sample(113.3)
    row = gate.as_record()
    assert row["clock_live"] is True
    assert row["clock_live_at_s"] == 113.3
    assert row["held_before_clock"] == 1
    assert not math.isnan(gate.live_at)
