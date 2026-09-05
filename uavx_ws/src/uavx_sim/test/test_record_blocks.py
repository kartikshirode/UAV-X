"""Chunk 4.2: the three blocks week 4 adds, checked against themselves.

The schema types every field of these and can say nothing about whether they
agree. That is the gap this file covers, and it is where a record that passes
every gate can still be describing a run that did not happen:

  zero separation violations from a monitor that watched no frames, which is
  round 3 finding 8 in a different place;

  a count of generated observations that disagrees with the list it was
  counted from, so the drain arithmetic divides by a number nothing produced;

  a drain that finished before it started, or a backlog_drain_s that is not
  drain_end_s minus drain_start_s, which is the bound the gate reads;

  and a vehicle that flew home while still holding the relay role, which is a
  swarm abandoning a link rather than handing it back.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_record_blocks.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_sim.run_record import RecordError, build_record, validate_record

# The complete set of keyword arguments for one valid record already exists
# next door, and a second copy here would drift from the schema the moment
# either changed. conftest.py puts the package root on sys.path, so the test
# package imports by its own name.
from test.test_run_record import fields

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES


def safety(**overrides):
    base = {"min_pairwise_separation_m": 16.0, "separation_violations": 0,
            "collision_contacts": 0, "contact_monitor_samples": 2475}
    base.update(overrides)
    return base


def block(**overrides):
    """One outage: two observations minted inside it, both delivered after."""
    ids = [f"{NEAR}:1", f"{FAR}:1"]
    base = {
        "generated_ids": list(ids),
        "delivered_ids": list(ids),
        "generated": 2,
        "unique_delivered": 2,
        "duplicated": 0,
        "expired": 0,
        "evicted": 0,
        "peak_queue_depth": 2,
        "control_queue_max_delay_s": 0.0,
        "outage_start_s": 60.0,
        "outage_end_s": 105.0,
        "generated_during_outage": 2,
        "delivered_after_restore": 2,
        "drain_start_s": 105.0,
        "drain_end_s": 106.5,
        "backlog_drain_s": 1.5,
        "delivery_complete_s": 106.8,
        "missing_ids": [],
        "unexpected_ids": [],
        "ledger": [{"id": i, "created_at_s": 61.0, "delivered_at_s": 106.0}
                   for i in ids],
    }
    base.update(overrides)
    return base


def recovery(**overrides):
    base = {"time_to_reconnect_s": 31.4, "relay_role_moved": True,
            "relay_role_holder": NEAR, "relay_role_released": False,
            "mover_returned_to_station": False,
            "delivery_ratio_after_recovery": 0.998}
    base.update(overrides)
    return base


def built(**kwargs):
    return build_record(**fields(**kwargs))


def refused(**kwargs):
    with pytest.raises(RecordError) as caught:
        built(**kwargs)
    return str(caught.value)


# ----------------------------------------------------------------- safety
def test_a_run_with_no_safety_block_is_still_a_record():
    # survey_baseline and the relay pair carry none of this.
    assert "separation_violations" not in built()


def test_the_four_safety_fields_land_at_the_top_level():
    got = built(safety=safety())
    assert got["min_pairwise_separation_m"] == 16.0
    assert got["contact_monitor_samples"] == 2475


def test_three_of_the_four_safety_fields_is_refused():
    partial = safety()
    del partial["contact_monitor_samples"]
    assert "cannot be told from a monitor that never ran" in refused(
        safety=partial)


def test_zero_frames_watched_is_not_a_clean_run():
    assert "statements about nothing" in refused(
        safety=safety(contact_monitor_samples=0))


def test_more_contacts_than_violations_is_refused():
    # Two airframes inside a metre are inside the ten metre floor, so every
    # contact is also a violation.
    assert "drifted apart" in refused(
        safety=safety(collision_contacts=3, separation_violations=1))


@pytest.mark.parametrize("bad", [-1, 1.5, True, None])
def test_a_violation_count_that_is_not_a_count_is_refused(bad):
    assert "not a count" in refused(safety=safety(separation_violations=bad))


def test_a_closest_approach_that_is_not_a_distance_is_refused():
    assert "not a distance" in refused(
        safety=safety(min_pairwise_separation_m=float("nan")))


# ----------------------------------------------------------- observations
def test_the_outage_block_lands_whole():
    got = built(observations=block())
    assert got["observations"]["generated"] == 2
    assert got["observations_set_equal"] is True


def test_the_equality_flag_is_the_two_lists_being_empty():
    got = built(observations=block(missing_ids=[f"{NEAR}:9"]))
    assert got["observations_set_equal"] is False


def test_a_count_that_disagrees_with_its_list_is_refused():
    assert "the same claim written twice" in refused(
        observations=block(generated=7))


def test_a_delivered_count_that_disagrees_with_its_list_is_refused():
    assert "distinct ids" in refused(observations=block(unique_delivered=9))


def test_a_ledger_short_of_the_generated_observations_is_refused():
    # The rows are what says which ids fell inside the outage.
    assert "rows against" in refused(observations=block(ledger=[]))


def test_a_drain_that_finished_before_it_started_is_refused():
    assert "before the drain started" in refused(
        observations=block(drain_end_s=100.0, backlog_drain_s=-5.0))


def test_a_drain_starting_before_the_route_came_back_is_refused():
    assert "somewhere to drain to" in refused(
        observations=block(drain_start_s=90.0, backlog_drain_s=16.5))


def test_a_drain_bound_that_is_not_the_difference_is_refused():
    # backlog_drain_s is the number the gate reads against 2.25 s, and this
    # is the only place it is compared with the two times it comes from.
    assert "measured and the other was typed" in refused(
        observations=block(backlog_drain_s=0.5))


def test_an_outage_that_ends_before_it_starts_is_refused():
    assert "starts at" in refused(
        observations=block(outage_start_s=120.0, outage_end_s=60.0,
                           drain_start_s=105.0))


def test_more_generated_during_the_outage_than_in_the_run_is_refused():
    assert "in the whole run" in refused(
        observations=block(generated_during_outage=99))


def test_more_delivered_after_restore_than_generated_during_is_refused():
    assert "contains the first" in refused(
        observations=block(generated_during_outage=1,
                           delivered_after_restore=2))


def test_the_outage_duration_is_promoted_for_the_gate_to_read():
    """queue_drain has no recovery block to carry it.

    Elections are off in that scenario, so no role manager moves and nothing
    else in the record is about the outage having lasted the 45 s the queue
    is sized against.
    """
    got = built(observations=block())
    assert got["outage_duration_s"] == pytest.approx(45.0)


def test_a_duration_that_is_not_the_window_is_refused():
    record = built(observations=block())
    record["outage_duration_s"] = 90.0
    with pytest.raises(RecordError) as caught:
        validate_record(record)
    assert "the window in the observations block runs" in str(caught.value)


def test_a_block_with_no_times_is_refused():
    body = block()
    del body["drain_end_s"]
    assert "is not in the record" in refused(observations=body)


def test_a_block_that_is_not_a_block_is_refused():
    with pytest.raises(RecordError):
        built(observations=[1, 2, 3])


# -------------------------------------------------------------- recovery
def test_the_recovery_fields_land_at_the_top_level():
    got = built(recovery=recovery())
    assert got["relay_role_holder"] == NEAR
    assert got["time_to_reconnect_s"] == 31.4


def test_five_of_the_six_recovery_fields_is_refused():
    partial = recovery()
    del partial["time_to_reconnect_s"]
    assert "a record of something else" in refused(recovery=partial)


def test_a_role_that_moved_with_nobody_holding_it_is_refused():
    assert "no vehicle is named" in refused(
        recovery=recovery(relay_role_holder=""))


def test_a_holder_named_on_a_run_where_nothing_moved_is_refused():
    assert "relay_role_moved is false" in refused(
        recovery=recovery(relay_role_moved=False))


def test_a_mover_that_went_home_still_holding_the_role_is_refused():
    # A vehicle that flew home while it was still the relay has abandoned the
    # link rather than handed it back.
    assert "abandoned the link" in refused(
        recovery=recovery(mover_returned_to_station=True,
                          relay_role_released=False))


def test_a_reconnect_time_that_is_not_a_duration_is_refused():
    assert "not a duration" in refused(
        recovery=recovery(time_to_reconnect_s=-1.0))


def test_a_post_recovery_ratio_outside_zero_to_one_is_refused():
    assert "not a ratio" in refused(
        recovery=recovery(delivery_ratio_after_recovery=1.4))


# ----------------------------------------------------------- the handback
def hand(**overrides):
    base = {"epoch": 1, "epoch_owner": FAR, "staying_member": FAR,
            "prepared_path": [FAR, RELAY, ANCHOR, "gcs"],
            "confirmed_observation_id": f"{FAR}:700",
            "release_sender": FAR, "confirmed_at": 262.0,
            "release_at": 262.4, "observation_gap_count": 0}
    base.update(overrides)
    return base


def test_the_handback_lands_whole():
    got = built(recovery=recovery(handback=hand()))
    assert got["handback"]["prepared_path"][0] == FAR


def test_a_relay_released_before_the_path_was_confirmed_is_refused():
    assert "let go and then looked" in refused(
        recovery=recovery(handback=hand(confirmed_at=263.0)))


def test_the_vehicle_being_released_cannot_own_the_transaction():
    # Round 6 finding 7. The owner is the member that stays.
    assert "round 6 finding 7" in refused(
        recovery=recovery(relay_role_holder=FAR, handback=hand()))


def test_a_path_that_runs_through_the_relay_is_not_a_handback_path():
    through = hand(prepared_path=[FAR, NEAR, ANCHOR, "gcs"])
    assert "needs the relay" in refused(recovery=recovery(handback=through))


def test_a_handback_with_no_times_is_refused():
    assert "came before the second" in refused(
        recovery=recovery(handback=hand(confirmed_at=None)))


def test_a_gap_count_that_is_not_a_count_is_refused():
    assert "not a count" in refused(
        recovery=recovery(handback=hand(observation_gap_count=-1)))


def test_the_two_link_loss_flags_ride_with_the_recovery():
    got = built(recovery=recovery(route_restored_after_blackout=True,
                                  outage_count_after_release=0))
    assert got["route_restored_after_blackout"] is True
    assert got["outage_count_after_release"] == 0


def test_a_restoration_flag_that_is_not_a_flag_is_refused():
    assert "not a flag" in refused(
        recovery=recovery(route_restored_after_blackout="yes"))


def test_an_outage_count_that_is_not_a_count_is_refused():
    assert "not a count" in refused(
        recovery=recovery(outage_count_after_release=-2))


def test_the_relay_slot_and_the_handback_ride_with_the_recovery():
    slot = {"commanded": [317.3, -36.8, 75.0], "clearance_m": 52.4,
            "band_reserved": True}
    got = built(recovery=recovery(relay_slot=slot))
    assert got["relay_slot"]["clearance_m"] == 52.4


def test_the_three_blocks_compose():
    got = built(safety=safety(), observations=block(), recovery=recovery())
    assert got["separation_violations"] == 0
    assert got["observations_set_equal"] is True
    assert got["relay_role_moved"] is True


# ------------------------------------------------- against the real checker
def test_a_week_four_record_satisfies_the_checker_the_gate_runs(tmp_path):
    """Not my opinion of the schema. The checker itself, on real bytes, with
    the expressions relay_kill actually asserts."""
    import subprocess
    import sys

    from test.test_run_record import RUN_ID, VALIDATE_RECORD
    from uavx_sim.run_record import write_record

    got = built(safety=safety(), observations=block(), recovery=recovery())
    path = tmp_path / f"{RUN_ID}.jsonl"
    write_record(path, got)

    requires = (
        "observations_set_equal==true",
        "observations.evicted==0",
        "observations.expired==0",
        "time_to_reconnect_s<=45",
        "relay_role_moved==true",
        "min_pairwise_separation_m>=10",
        "separation_violations==0",
        "collision_contacts==0",
        "contact_monitor_samples>0",
        "delivery_ratio_after_recovery>=0.90",
        "observations.backlog_drain_s<=2.25",
        "observations.drain_start_s>=observations.outage_end_s",
        "observations.delivery_complete_s>=observations.drain_end_s",
    )
    arguments = [sys.executable, VALIDATE_RECORD, str(path)]
    for expression in requires:
        arguments += ["--require", expression]
    done = subprocess.run(arguments, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    # Every requirement reported, not merely a zero exit. A checker that
    # silently skipped an expression would also exit zero.
    for expression in requires:
        assert f"ok    {expression}" in done.stdout
