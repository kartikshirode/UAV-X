"""Chunk 4.1: one job per vehicle, and the ways a scenario forgets to give one.

Weeks 2 and 3 never needed this. Every vehicle in `survey_baseline` flies a
strip and every vehicle in `relay_required` holds a point, so the question
"what is this vehicle for" had one answer per file.

Week 4 asks it per vehicle. `mission_integrated` has two surveying while two
hold the chain up, and the encounter pair has both of its vehicles flying
lines that cross. Two failures follow from that and neither one fails loudly
on its own:

  a vehicle with no work takes off, hovers wherever the spawn manifest put
  it, and then appears in a graph the run reports delivery numbers over;

  a vehicle with two has two places to be, and which one it flies to is
  decided by the order the runner happens to read the blocks in.

The last group reads the frozen files themselves, because the encounter pair
is an argument that only holds while both tracks are the same length.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_work.py

Runs on a clean checkout with nothing built.
"""

import math
from pathlib import Path

import pytest

from uavx_sim import scenario
from uavx_sim.work import (HOVER, STATION, SURVEY, TRACK, Track, WorkError,
                           assignments, claims_topology, stationed_of,
                           survey_vehicles_of, tracks_of, yield_enabled)

# Built per index, never written out. scripts/check_seam.sh counts distinct
# vehicle endpoint literals per file and that rule covers tests.
VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

LAYERS = {ANCHOR: 30.0, RELAY: 40.0, NEAR: 50.0, FAR: 60.0}

REPO = Path(__file__).resolve().parents[4]
SCENARIOS = REPO / "scenarios"


def a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0), start_s=20.0,
            speed=10.0):
    return {"start_enu": list(start), "end_enu": list(end),
            "start_s": start_s, "speed_mps": speed}


def with_radio(**extra):
    """A scenario body whose radio is on, so it claims a topology."""
    body = {"comms": {"enabled": True, "forwarding": True,
                      "elections_enabled": True, "stations": {}}}
    body.update(extra)
    return body


# ------------------------------------------------------------ the assignment
def test_station_keeping_vehicles_hold_a_point():
    raw = with_radio()
    raw["comms"]["stations"] = {v: [0.0, 0.0, LAYERS[v]] for v in VEHICLES}
    assert assignments(raw, VEHICLES, LAYERS) == {v: STATION for v in VEHICLES}


def test_a_track_is_work():
    raw = with_radio(tracks={NEAR: a_track(start=(0.0, 0.0, 50.0),
                                           end=(100.0, 0.0, 50.0))})
    raw["comms"]["stations"] = {FAR: [0.0, 0.0, 60.0]}
    assert assignments(raw, (NEAR, FAR), LAYERS) == {NEAR: TRACK,
                                                     FAR: STATION}


def test_a_survey_strip_is_work():
    raw = with_radio(survey={"vehicles": [NEAR, FAR]})
    raw["comms"]["stations"] = {ANCHOR: [0.0, 0.0, 30.0],
                                RELAY: [0.0, 0.0, 40.0]}
    assert assignments(raw, VEHICLES, LAYERS) == {
        ANCHOR: STATION, RELAY: STATION, NEAR: SURVEY, FAR: SURVEY}


def test_a_vehicle_with_no_work_is_refused_when_the_radio_is_on():
    raw = with_radio()
    raw["comms"]["stations"] = {v: [0.0, 0.0, LAYERS[v]] for v in VEHICLES[:3]}
    with pytest.raises(WorkError) as caught:
        assignments(raw, VEHICLES, LAYERS)
    assert FAR in str(caught.value)
    assert "no work" in str(caught.value)


def test_a_vehicle_with_no_work_is_allowed_when_nothing_claims_a_topology():
    # harness_check, and only harness_check. Four vehicles up, holding, down,
    # and the file is never cited as evidence for anything.
    assert assignments({}, VEHICLES, LAYERS) == {v: HOVER for v in VEHICLES}


def test_a_disabled_radio_claims_no_topology():
    raw = {"comms": {"enabled": False}}
    assert claims_topology(raw) is False
    assert assignments(raw, (NEAR,), LAYERS) == {NEAR: HOVER}


def test_a_vehicle_with_two_jobs_is_refused():
    raw = with_radio(tracks={NEAR: a_track(start=(0.0, 0.0, 50.0),
                                           end=(100.0, 0.0, 50.0))})
    raw["comms"]["stations"] = {NEAR: [0.0, 0.0, 50.0]}
    with pytest.raises(WorkError) as caught:
        assignments(raw, (NEAR,), LAYERS)
    assert "station and track" in str(caught.value)


def test_survey_and_station_together_is_refused():
    raw = with_radio(survey={"vehicles": [NEAR]})
    raw["comms"]["stations"] = {NEAR: [0.0, 0.0, 50.0]}
    with pytest.raises(WorkError) as caught:
        assignments(raw, (NEAR,), LAYERS)
    assert "two jobs" in str(caught.value)


def test_no_vehicles_is_refused():
    with pytest.raises(WorkError):
        assignments(with_radio(), (), LAYERS)


def test_stationed_reports_only_what_the_block_names():
    raw = {"comms": {"stations": {FAR: [0.0, 0.0, 60.0]}}}
    assert stationed_of(raw, VEHICLES) == (FAR,)
    assert stationed_of({}, VEHICLES) == ()
    assert stationed_of({"comms": {"stations": None}}, VEHICLES) == ()


# ----------------------------------------------------------------- the track
def test_a_track_is_a_line_at_a_speed():
    track = Track(start=(0.0, 0.0, 45.0), end=(240.0, 0.0, 45.0),
                  start_s=20.0, speed_mps=10.0)
    assert track.length_m == pytest.approx(240.0)
    assert track.duration_s == pytest.approx(24.0)
    assert track.arrival_s == pytest.approx(44.0)


def test_a_track_holds_its_ends_outside_its_window():
    track = Track(start=(0.0, 0.0, 45.0), end=(240.0, 0.0, 45.0),
                  start_s=20.0, speed_mps=10.0)
    assert track.position_at(0.0) == (0.0, 0.0, 45.0)
    assert track.position_at(20.0) == (0.0, 0.0, 45.0)
    assert track.position_at(1000.0) == (240.0, 0.0, 45.0)


def test_a_track_is_halfway_at_half_its_flight():
    track = Track(start=(0.0, 0.0, 45.0), end=(240.0, 0.0, 45.0),
                  start_s=20.0, speed_mps=10.0)
    assert track.position_at(32.0)[0] == pytest.approx(120.0)


def test_a_track_records_what_it_was_asked_to_fly():
    track = Track(start=(0.0, 0.0, 45.0), end=(240.0, 0.0, 45.0),
                  start_s=20.0, speed_mps=10.0)
    row = track.as_record()
    assert row["length_m"] == pytest.approx(240.0)
    assert row["arrival_s"] == pytest.approx(44.0)
    assert row["start_enu"] == [0.0, 0.0, 45.0]


def test_absent_tracks_is_an_empty_mapping():
    assert tracks_of({}, VEHICLES, LAYERS) == {}


def test_tracks_must_be_a_mapping():
    with pytest.raises(WorkError):
        tracks_of({"tracks": [1, 2]}, VEHICLES, LAYERS)


def test_a_track_for_an_absent_vehicle_is_refused():
    raw = {"tracks": {FAR: a_track()}}
    with pytest.raises(WorkError) as caught:
        tracks_of(raw, (NEAR,), LAYERS)
    assert FAR in str(caught.value)


@pytest.mark.parametrize("missing",
                         ["start_enu", "end_enu", "start_s", "speed_mps"])
def test_a_track_missing_a_field_is_refused(missing):
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0))
    del line[missing]
    with pytest.raises(WorkError) as caught:
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)
    assert missing in str(caught.value)


@pytest.mark.parametrize("bad", [[0.0, 0.0], "here", [0.0, 0.0, None],
                                 [0.0, 0.0, float("nan")], [0, 0, True]])
def test_a_track_end_that_is_not_three_numbers_is_refused(bad):
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0))
    line["end_enu"] = bad
    with pytest.raises(WorkError):
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)


def test_a_track_starting_before_zero_is_refused():
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0),
                   start_s=-1.0)
    with pytest.raises(WorkError):
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)


@pytest.mark.parametrize("speed", [0.0, -3.0])
def test_a_track_without_a_positive_speed_is_refused(speed):
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0),
                   speed=speed)
    with pytest.raises(WorkError):
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)


def test_a_track_that_goes_nowhere_is_refused():
    line = a_track(start=(0.0, 0.0, 50.0), end=(0.2, 0.0, 50.0))
    with pytest.raises(WorkError) as caught:
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)
    assert "point with two spellings" in str(caught.value)


def test_a_track_at_the_wrong_altitude_is_refused():
    # The layer the runner climbs to and the height the track flies at are two
    # spellings of one number, and a vehicle flying the other one is in
    # somebody else's band.
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 55.0))
    with pytest.raises(WorkError) as caught:
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), LAYERS)
    assert "end_enu" in str(caught.value)


def test_a_track_without_a_hover_altitude_is_refused():
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0))
    with pytest.raises(WorkError):
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), {})


def test_a_non_numeric_hover_altitude_is_refused():
    line = a_track(start=(0.0, 0.0, 50.0), end=(100.0, 0.0, 50.0))
    with pytest.raises(WorkError):
        tracks_of({"tracks": {NEAR: line}}, (NEAR,), {NEAR: "fifty"})


# ---------------------------------------------------------------- the survey
def test_no_survey_block_means_nobody_surveys():
    assert survey_vehicles_of({}, VEHICLES) == ()


def test_a_survey_without_a_vehicle_list_is_flown_by_everybody():
    assert survey_vehicles_of({"survey": {"cell_m": 10.0}},
                              VEHICLES) == VEHICLES


def test_a_named_survey_list_is_returned_in_vehicle_order():
    raw = {"survey": {"vehicles": [FAR, NEAR]}}
    assert survey_vehicles_of(raw, VEHICLES) == (NEAR, FAR)


def test_an_empty_survey_list_is_refused():
    with pytest.raises(WorkError):
        survey_vehicles_of({"survey": {"vehicles": []}}, VEHICLES)


def test_a_survey_naming_an_absent_vehicle_is_refused():
    with pytest.raises(WorkError) as caught:
        survey_vehicles_of({"survey": {"vehicles": [FAR]}}, (NEAR,))
    assert FAR in str(caught.value)


def test_a_survey_naming_one_vehicle_twice_is_refused():
    with pytest.raises(WorkError) as caught:
        survey_vehicles_of({"survey": {"vehicles": [NEAR, NEAR]}}, VEHICLES)
    assert "repeats" in str(caught.value)


def test_a_survey_that_is_not_a_mapping_is_refused():
    with pytest.raises(WorkError):
        survey_vehicles_of({"survey": [NEAR]}, VEHICLES)


# ------------------------------------------------------------- the yield rule
def test_the_yield_rule_is_on_when_nothing_says_otherwise():
    assert yield_enabled({}) is True
    assert yield_enabled({"safety": {}}) is True


def test_the_yield_rule_reads_the_flag_both_ways():
    assert yield_enabled({"safety": {"yield_enabled": True}}) is True
    assert yield_enabled({"safety": {"yield_enabled": False}}) is False


@pytest.mark.parametrize("bad", ["false", 0, None, []])
def test_a_yield_flag_that_is_not_a_boolean_is_refused(bad):
    with pytest.raises(WorkError):
        yield_enabled({"safety": {"yield_enabled": bad}})


def test_a_safety_block_that_is_not_a_mapping_is_refused():
    with pytest.raises(WorkError):
        yield_enabled({"safety": True})


# ------------------------------------------------------ the files themselves
def load(name):
    return scenario.load(SCENARIOS / f"{name}.yaml")


def jobs_of(name):
    run = load(name)
    return assignments(run.raw, list(run.vehicles),
                       run.raw.get("hover_altitudes_m") or {})


@pytest.mark.parametrize("name", ["relay_required", "direct_only",
                                  "relay_kill", "link_loss", "queue_drain"])
def test_the_station_keeping_scenarios_give_every_vehicle_a_point(name):
    assert set(jobs_of(name).values()) == {STATION}


def test_the_integrated_mission_splits_the_swarm_two_and_two():
    counts = {}
    for kind in jobs_of("mission_integrated").values():
        counts[kind] = counts.get(kind, 0) + 1
    assert counts == {STATION: 2, SURVEY: 2}


@pytest.mark.parametrize("name", ["encounter", "encounter_noyield"])
def test_the_encounter_pair_flies_tracks_and_holds_nothing(name):
    assert set(jobs_of(name).values()) == {TRACK}


def test_the_encounter_pair_differs_in_the_yield_flag_and_nothing_else():
    # The whole argument of the control rests on this. A pair that also
    # differed in a speed or a start time would compare two flights rather
    # than one rule.
    run, control = load("encounter"), load("encounter_noyield")
    assert yield_enabled(run.raw) is True
    assert yield_enabled(control.raw) is False
    assert run.seed == control.seed
    assert run.duration_s == control.duration_s
    assert run.vehicles == control.vehicles
    assert run.raw["tracks"] == control.raw["tracks"]
    assert run.raw["hover_altitudes_m"] == control.raw["hover_altitudes_m"]
    assert run.raw["comms"] == control.raw["comms"]


def test_neither_encounter_vehicle_reaches_the_crossing_point_first():
    # Both paths are 240 m and both start together, so an unimpeded pair
    # arrives at the same instant and passes within 0 m. If one were shorter,
    # a run could come out safe because one vehicle was late.
    run = load("encounter")
    tracks = tracks_of(run.raw, list(run.vehicles),
                       run.raw["hover_altitudes_m"])
    lengths = {v: t.length_m for v, t in tracks.items()}
    assert len(set(round(v, 6) for v in lengths.values())) == 1
    arrivals = {v: t.arrival_s for v, t in tracks.items()}
    assert len(set(round(v, 6) for v in arrivals.values())) == 1

    crossing = [t.position_at(32.0) for t in tracks.values()]
    assert math.dist(crossing[0], crossing[1]) == pytest.approx(0.0, abs=1e-9)
