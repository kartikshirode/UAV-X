"""The link model, driven at its boundaries and over the frozen topology.

The interesting failures here are not "the formula is wrong". They are:

  - a band boundary off by one comparison, which moves a frozen pair across a
    band and turns a deterministic result into a coin toss
  - a fade-band link somewhere in the frozen geometry, which is round 3
    finding 1 and round 4 finding 2, the two defects that made killing the
    relay a no-op and then made it seed-dependent
  - determinism that depends on how much other traffic happened first, which
    looks like a working seeded replay until the traffic changes
"""

import math

import pytest

from uavx_comms import link, params

import check_geometry as oracle


def test_band_boundaries_are_closed_on_the_left():
    assert link.band(params.R_FULL_M) == link.FULL
    assert link.band(params.R_FULL_M + 1e-9) == link.FADE
    assert link.band(params.R_MAX_M) == link.FADE
    assert link.band(params.R_MAX_M + 1e-9) == link.OUT


def test_the_band_agrees_with_the_geometry_oracle_across_the_whole_range():
    step = 0.5
    distance = 0.0
    while distance <= params.R_MAX_M + 50.0:
        assert link.band(distance) == oracle.band(distance), distance
        distance += step


def test_the_fade_curve_runs_from_one_to_zero_and_is_monotonic():
    assert link.delivery_probability(params.R_FULL_M) == 1.0
    assert link.delivery_probability(params.R_MAX_M) == 0.0
    assert link.delivery_probability(params.R_MAX_M + 1.0) == 0.0
    midpoint = (params.R_FULL_M + params.R_MAX_M) / 2.0
    assert abs(link.delivery_probability(midpoint) - 0.5) < 1e-12

    previous = 1.0
    distance = params.R_FULL_M
    while distance <= params.R_MAX_M:
        current = link.delivery_probability(distance)
        assert current <= previous + 1e-12
        previous = current
        distance += 0.5


def test_no_frozen_pair_sits_in_the_fade_band():
    """Round 3 finding 1 and round 4 finding 2, asked of the link model itself.

    Every pair in the frozen topology has to be decisively deliverable or
    decisively absent. A "must not exist" pair inside r_max makes the relay
    kill a coin toss, and a "must be used" pair in the fade band makes a
    correct implementation fail on an unlucky seed. The oracle enumerates all
    ten pairs; asserting over a handful is how this was missed twice.
    """
    undecided = []
    for a in sorted(oracle.START):
        for b in sorted(oracle.START):
            if a >= b:
                continue
            distance = math.dist(oracle.START[a], oracle.START[b])
            probability = link.delivery_probability(distance)
            if probability not in (0.0, 1.0):
                undecided.append((a, b, distance, probability))
            assert link.placement_holds(distance), (a, b, distance)
    assert not undecided, ("these frozen pairs deliver probabilistically: "
                           + repr(undecided))


def test_the_frozen_chain_is_the_only_thing_that_delivers():
    """The links that must exist do, the ones that must not do not."""
    model = link.LinkModel(seed=1)
    must = [("gcs", "uav_1"), ("uav_1", "uav_2"), ("uav_2", "uav_3"),
            ("uav_2", "uav_4"), ("uav_3", "uav_4")]
    must_not = [("gcs", "uav_2"), ("uav_1", "uav_3"), ("uav_1", "uav_4"),
                ("gcs", "uav_3"), ("gcs", "uav_4")]
    for a, b in must:
        distance = math.dist(oracle.START[a], oracle.START[b])
        assert all(model.deliver(a, b, distance) for _ in range(200))
        assert all(model.deliver(b, a, distance) for _ in range(200))
    for a, b in must_not:
        distance = math.dist(oracle.START[a], oracle.START[b])
        assert not any(model.deliver(a, b, distance) for _ in range(200))
        assert not any(model.deliver(b, a, distance) for _ in range(200))


def test_the_same_seed_replays_and_a_different_seed_does_not():
    fade = (params.R_FULL_M + params.R_MAX_M) / 2.0
    a = link.LinkModel(seed=42)
    b = link.LinkModel(seed=42)
    c = link.LinkModel(seed=43)
    left = [a.deliver("a", "b", fade) for _ in range(400)]
    right = [b.deliver("a", "b", fade) for _ in range(400)]
    other = [c.deliver("a", "b", fade) for _ in range(400)]
    assert left == right, "the same seed did not replay"
    assert left != other, ("two different seeds produced the same stream, so "
                           "the seed is not reaching the generator")


def test_full_band_traffic_does_not_move_the_random_stream():
    """A replay that depends on traffic volume is not a replay.

    A link at or inside r_full is delivered by definition, so drawing there
    would make a fade-band result depend on how much unrelated full-band
    traffic ran first. That is invisible until the traffic changes, and then
    the seeded run stops reproducing.
    """
    fade = params.R_FULL_M + 20.0
    quiet = link.LinkModel(seed=5)
    busy = link.LinkModel(seed=5)
    for _ in range(1000):
        busy.deliver("a", "b", 10.0)
    assert quiet.draws == 0 and busy.draws == 0
    assert ([quiet.deliver("a", "b", fade) for _ in range(200)]
            == [busy.deliver("a", "b", fade) for _ in range(200)])


def test_the_fade_band_delivers_at_about_the_stated_rate():
    fade = params.R_FULL_M + 20.0
    expected = link.delivery_probability(fade)
    model = link.LinkModel(seed=11)
    trials = 20000
    hits = sum(1 for _ in range(trials) if model.deliver("a", "b", fade))
    assert abs(hits / trials - expected) < 0.02


def test_a_gated_radio_stops_both_directions_and_leaves_the_geometry_alone():
    """comms_blackout, which is not the same fault as a kill.

    The vehicle is still flying and every pair it belongs to goes silent, in
    both directions, while every other pair keeps working. A model that gated
    by distance could not express this at all.
    """
    close = 10.0
    model = link.LinkModel(seed=3)
    assert model.deliver("uav_2", "uav_1", close)
    model.gate_radio("uav_2")
    assert not model.deliver("uav_2", "uav_1", close)
    assert not model.deliver("uav_1", "uav_2", close)
    assert model.deliver("uav_1", "uav_3", close), (
        "gating one vehicle's radio took down a pair it is not part of")
    model.restore_radio("uav_2")
    assert model.deliver("uav_2", "uav_1", close)


def test_a_killed_vehicle_never_comes_back():
    model = link.LinkModel(seed=3)
    model.kill("uav_2")
    assert not model.deliver("uav_2", "uav_1", 1.0)
    model.restore_radio("uav_2")
    assert not model.deliver("uav_2", "uav_1", 1.0), (
        "restoring a radio brought a dead vehicle back, so the two faults the "
        "challenge names are the same fault here")


def test_the_placement_rule_rejects_the_gap_between_the_two_limits():
    middle = (params.USED_LINK_MAX_M + params.UNUSED_LINK_MIN_M) / 2.0
    assert not link.placement_holds(middle)
    assert link.placement_holds(params.USED_LINK_MAX_M)
    assert link.placement_holds(params.UNUSED_LINK_MIN_M)


# --------------------------------------------------------------- the blackout
def test_a_gate_that_nothing_lifts_is_a_hold_of_zero():
    """A scenario that starts in a blackout, rather than injecting one."""
    gate = link.Blackout()
    assert gate.start(["uav_2"], 10.0) == ("uav_2",)
    assert gate.restore_due(1e6) == ()
    assert gate.live is True


def test_the_radio_comes_back_on_its_own_timetable():
    gate = link.Blackout(120.0)
    gate.start(["uav_2"], 500.0)
    assert gate.restore_due(619.9) == ()
    assert gate.restore_due(620.0) == ("uav_2",)
    assert gate.live is False


def test_it_comes_back_once():
    gate = link.Blackout(120.0)
    gate.start(["uav_2"], 500.0)
    gate.restore_due(620.0)
    assert gate.restore_due(700.0) == ()
    assert gate.restored_at == 620.0


def test_gating_the_same_vehicle_twice_does_not_restart_the_clock():
    # The run describes one outage. A second request that moved the start
    # would shorten it, and the reconnect time is measured from that start.
    gate = link.Blackout(120.0)
    gate.start(["uav_2"], 500.0)
    assert gate.start(["uav_2"], 560.0) == ()
    assert gate.started_at == 500.0
    assert gate.restore_due(620.0) == ("uav_2",)


def test_the_record_says_when_it_went_and_when_it_came_back():
    gate = link.Blackout(120.0)
    row = gate.as_record()
    assert row["blackout_started_at"] is None
    assert row["blackout_restored_at"] is None
    gate.start(["uav_2"], 500.0)
    gate.restore_due(620.0)
    row = gate.as_record()
    assert row["blackout_started_at"] == 500.0
    assert row["blackout_restored_at"] == 620.0
    assert row["blackout_nodes"] == []


@pytest.mark.parametrize("bad", [-1.0, float("nan"), None, True, "later"])
def test_a_hold_that_is_not_a_length_of_time_is_refused(bad):
    with pytest.raises(ValueError):
        link.Blackout(bad)


def test_a_gated_radio_is_not_an_absent_one():
    # The two faults the organisers name, and they are not the same. A gated
    # vehicle is still flying and still occupies airspace.
    model = link.LinkModel(seed=7)
    model.gate_radio("uav_2")
    assert model.is_live("uav_2") is False
    assert "uav_2" not in model.absent
    model.restore_radio("uav_2")
    assert model.is_live("uav_2") is True
