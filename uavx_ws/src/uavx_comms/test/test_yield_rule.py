"""Chunk 4.5: does the yield rule act, and does it act on the right vehicle.

The gate asks `encounter.yaml` for at least one yield event naming `uav_4` and
a hold longer than zero, and asks its control for a separation violation. Both
of those are claims about this module, so the cases that matter here are the
ones a live run cannot show cheaply: a rule that fires too early, a rule that
holds against its own beacon, a rule that trusts a sighting old enough to be
30 m wrong, and a disabled rule that reports nothing rather than reporting a
zero hold.

The encounter geometry is used directly rather than paraphrased. Both vehicles
fly 240 m at 10 m/s from t = 20 s, `uav_3` north along x = 250 and `uav_4` east
along y = 0, and they meet at (250, 0, 45) at t = 32 s.

    python3 -m pytest -q uavx_ws/src/uavx_comms/test/test_yield_rule.py
"""

import math

import pytest

from uavx_comms import params
from uavx_comms.yield_rule import (Sighting, YieldRule, closest_approach,
                                   gives_way, system_id)

ALTITUDE = 45.0
CROSSING = (250.0, 0.0, ALTITUDE)
START_S = 20.0
SPEED = 10.0


def uav_3_at(t):
    """North along x = 250, from y = -120."""
    return (250.0, -120.0 + max(0.0, t - START_S) * SPEED, ALTITUDE)


def uav_4_at(t):
    """East along y = 0, from x = 130."""
    return (130.0 + max(0.0, t - START_S) * SPEED, 0.0, ALTITUDE)


UAV_3_VELOCITY = (0.0, SPEED, 0.0)
UAV_4_VELOCITY = (SPEED, 0.0, 0.0)


def rule_for(node_id, **kwargs):
    return YieldRule(node_id, **kwargs)


# ------------------------------------------------------------ the arithmetic
def test_two_vehicles_flying_apart_are_nearest_now():
    when, gap = closest_approach((30.0, 0.0, 0.0), (10.0, 0.0, 0.0), 4.0)
    assert when == 0.0
    assert gap == pytest.approx(30.0)


def test_two_vehicles_closing_head_on_meet_inside_the_window():
    when, gap = closest_approach((40.0, 0.0, 0.0), (-10.0, 0.0, 0.0), 4.0)
    assert when == pytest.approx(4.0)
    assert gap == pytest.approx(0.0)


def test_a_conflict_beyond_the_horizon_is_only_predicted_as_far_as_it():
    """Past the horizon neither velocity is worth anything.

    A pair 200 m apart and closing at 10 m/s meets in 20 s, and a rule that
    reported that as the closest approach would hold for something two survey
    legs away that either vehicle will have turned out of.
    """
    when, gap = closest_approach((200.0, 0.0, 0.0), (-10.0, 0.0, 0.0), 4.0)
    assert when == pytest.approx(4.0)
    assert gap == pytest.approx(160.0)


def test_two_vehicles_holding_station_are_nearest_where_they_are():
    when, gap = closest_approach((12.0, 5.0, 0.0), (0.0, 0.0, 0.0), 4.0)
    assert when == 0.0
    assert gap == pytest.approx(13.0)


def test_a_horizon_that_looks_backwards_is_refused():
    with pytest.raises(ValueError):
        closest_approach((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), -1.0)


# ------------------------------------------------------------ who gives way
def test_the_higher_system_id_holds():
    assert gives_way("uav_4", "uav_3") is True
    assert gives_way("uav_3", "uav_4") is False


def test_a_vehicle_never_gives_way_to_itself():
    """A radio that echoed a beacon back would otherwise stop it dead."""
    assert gives_way("uav_4", "uav_4") is False


def test_ids_order_by_number_and_not_by_spelling():
    assert system_id("uav_10") > system_id("uav_9")
    assert gives_way("uav_10", "uav_9") is True


def test_the_ground_station_sorts_below_everything_that_flies():
    assert system_id("gcs") < system_id("uav_1")


# ------------------------------------------------- the encounter, both sides
def test_the_rule_is_quiet_eight_seconds_before_the_crossing():
    """At t = 24 the predicted gap at the horizon is 56.6 m.

    A rule that fired here would hold for most of the leg and the run would
    not show a yield, it would show a vehicle that stopped.
    """
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(24.0), UAV_3_VELOCITY, 24.0)
    assert rule.update(24.0, uav_4_at(24.0), UAV_4_VELOCITY) is False
    assert rule.events == 0


def test_uav_4_holds_four_seconds_before_the_crossing():
    """At t = 28 the two are 4 s from passing within 0 m of each other."""
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    assert rule.update(28.0, uav_4_at(28.0), UAV_4_VELOCITY) is True
    assert rule.events == 1
    assert rule.last_conflict.other_id == "uav_3"
    assert rule.last_conflict.separation_m == pytest.approx(0.0, abs=1e-6)


def test_uav_3_sees_the_same_crossing_and_does_not_hold():
    """The lower id carries on, and the record still shows it saw the crossing.

    Both vehicles predict the same conflict. If both held, neither would ever
    move again; if the record only counted holds, a run where the rule was
    never fed anything would look the same on uav_3 as this one.
    """
    rule = rule_for("uav_3")
    rule.sighting("uav_4", uav_4_at(28.0), UAV_4_VELOCITY, 28.0)
    assert rule.update(28.0, uav_3_at(28.0), UAV_3_VELOCITY) is False
    assert rule.events == 0
    assert rule.closest_predicted_m == pytest.approx(0.0, abs=1e-6)


def test_a_held_vehicle_resumes_once_the_other_is_past():
    """uav_4 stopped, uav_3 crossed and kept going north."""
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    assert rule.update(28.0, uav_4_at(28.0), UAV_4_VELOCITY) is True
    # Held at (210, 0), while uav_3 has crossed and is 15 m north of the point.
    rule.sighting("uav_3", (250.0, 15.0, ALTITUDE), UAV_3_VELOCITY, 35.5)
    assert rule.update(35.5, (210.0, 0.0, ALTITUDE), (0.0, 0.0, 0.0)) is False


# ------------------------------------------------------- what gets counted
def test_the_hold_is_counted_in_seconds_and_not_in_ticks():
    """Twenty ticks at 20 Hz, and the vehicle stops after the first one.

    The first tick is at cruise, because that is the state the rule decides
    in. Every later one is a stopped vehicle, which is what makes this the
    case that catches a rule predicting from the present.
    """
    rule = rule_for("uav_4")
    for tick in range(20):
        now = 28.0 + tick * 0.05
        rule.sighting("uav_3", uav_3_at(now), UAV_3_VELOCITY, now)
        moving = UAV_4_VELOCITY if tick == 0 else (0.0, 0.0, 0.0)
        assert rule.update(now, uav_4_at(28.0), moving) is True
    assert rule.hold_seconds == pytest.approx(0.95, abs=1e-6)
    assert rule.events == 1, "one hold, however many ticks it lasted"


def test_a_stopped_vehicle_does_not_clear_its_own_conflict():
    """The chattering case, written down on its own.

    A rule that predicts from the vehicle's current velocity sees no closing
    speed the instant the vehicle stops, releases, moves back into the
    conflict and holds again. It creeps into the crossing at a fraction of
    cruise while reporting a yield event and a hold above zero, so the gate
    would pass on the one behaviour the rule exists to prevent.
    """
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    assert rule.update(28.0, uav_4_at(28.0), UAV_4_VELOCITY) is True
    rule.sighting("uav_3", uav_3_at(28.05), UAV_3_VELOCITY, 28.05)
    assert rule.update(28.05, uav_4_at(28.0), (0.0, 0.0, 0.0)) is True
    assert rule.events == 1


def test_a_second_hold_is_a_second_event():
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    rule.update(28.0, uav_4_at(28.0), UAV_4_VELOCITY)
    rule.sighting("uav_3", (250.0, 200.0, ALTITUDE), UAV_3_VELOCITY, 29.0)
    rule.update(29.0, uav_4_at(28.0), (0.0, 0.0, 0.0))
    assert rule.holding is False
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 30.0)
    rule.update(30.0, uav_4_at(28.0), UAV_4_VELOCITY)
    assert rule.events == 2


def test_a_clock_that_ran_backwards_is_refused():
    rule = rule_for("uav_4")
    rule.update(30.0, uav_4_at(30.0), UAV_4_VELOCITY)
    with pytest.raises(ValueError):
        rule.update(29.0, uav_4_at(29.0), UAV_4_VELOCITY)


# --------------------------------------------------------- late information
def test_a_sighting_is_extrapolated_from_its_own_stamp():
    """Not from when it arrived. A beacon the radio held onto is old.

    architecture.md section 5: the predictor uses the timestamp in the
    message. Half a second of hop latency at cruise is 5 m, which is half the
    separation floor being protected.
    """
    seen = Sighting("uav_3", (250.0, -40.0, ALTITUDE), UAV_3_VELOCITY, 28.0)
    where, moving = seen.state_at(28.5)
    assert where[1] == pytest.approx(-35.0)
    assert moving == UAV_3_VELOCITY


def test_a_sighting_older_than_the_neighbour_timeout_is_dropped():
    """Three seconds at cruise is 30 m of guessing.

    That is three times the floor the rule protects, so past the timeout the
    honest answer is that this vehicle does not know where its neighbour is.
    """
    rule = rule_for("uav_4")
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    late = 28.0 + params.NEIGHBOUR_TIMEOUT_S + 0.1
    assert rule.update(late, uav_4_at(28.0), UAV_4_VELOCITY) is False
    assert rule.conflicts(late, uav_4_at(28.0), UAV_4_VELOCITY) == ()


def test_a_beacon_that_arrives_behind_a_newer_one_does_not_rewind():
    """The radio delays every packet, so out of order arrival is normal."""
    rule = rule_for("uav_4")
    rule.sighting("uav_3", (250.0, 0.0, ALTITUDE), UAV_3_VELOCITY, 32.0)
    rule.sighting("uav_3", (250.0, -120.0, ALTITUDE), UAV_3_VELOCITY, 20.0)
    assert rule.sightings["uav_3"].sent_at == 32.0


def test_a_vehicle_ignores_a_sighting_of_itself():
    rule = rule_for("uav_4")
    rule.sighting("uav_4", uav_4_at(28.0), UAV_4_VELOCITY, 28.0)
    assert rule.sightings == {}


# ------------------------------------------------------------- the control
def test_a_disabled_rule_never_holds():
    """encounter_noyield.yaml, which is the run that has to fail."""
    rule = rule_for("uav_4", enabled=False)
    rule.sighting("uav_3", uav_3_at(28.0), UAV_3_VELOCITY, 28.0)
    assert rule.update(28.0, uav_4_at(28.0), UAV_4_VELOCITY) is False
    assert rule.events == 0
    assert rule.hold_seconds == 0.0


def test_a_disabled_rule_still_reports_a_zero_rather_than_nothing():
    """The pair differ in the flight and not in what was measured.

    A control whose record simply lacked the fields would make the comparison
    between the two runs a comparison of two different records.
    """
    row = rule_for("uav_4", enabled=False).as_record()
    assert row["yield_enabled"] is False
    assert row["yield_events"] == 0
    assert row["yield_hold_s"] == 0.0


def test_the_record_separates_seeing_nothing_from_not_being_the_one_to_act():
    quiet = rule_for("uav_3")
    quiet.update(28.0, uav_3_at(28.0), UAV_3_VELOCITY)
    assert quiet.as_record()["yield_closest_predicted_m"] is None

    watching = rule_for("uav_3")
    watching.sighting("uav_4", uav_4_at(28.0), UAV_4_VELOCITY, 28.0)
    watching.update(28.0, uav_3_at(28.0), UAV_3_VELOCITY)
    row = watching.as_record()
    assert row["yield_events"] == 0
    assert row["yield_closest_predicted_m"] == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------- refusals
def test_a_rule_with_no_vehicle_id_is_refused():
    with pytest.raises(ValueError):
        YieldRule("")


@pytest.mark.parametrize("bad", [-1.0, float("nan"), None, True, "soon"])
def test_a_horizon_that_is_not_a_length_of_time_is_refused(bad):
    with pytest.raises(ValueError):
        YieldRule("uav_4", horizon_s=bad)


def test_the_defaults_are_the_frozen_ones():
    """Not written down a second time here. architecture.md section 5."""
    rule = YieldRule("uav_4")
    assert rule.horizon_s == params.YIELD_HORIZON_S
    assert rule.min_separation_m == params.MIN_SEPARATION_M
    assert math.isclose(rule.stale_after_s, params.NEIGHBOUR_TIMEOUT_S)
