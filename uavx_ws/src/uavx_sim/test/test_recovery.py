"""Chunk 4.2: the numbers a run with a fault in it reports.

Every one of them is a time, and every one has a version that reads better
than the truth:

  a reconnect measured from the moment the runner asked for the kill rather
  than the moment two witnesses agreed it had landed;

  a reconnect that stops when a route first appears, which counts the mesh
  flapping as a recovery;

  a post recovery delivery ratio computed over the whole run, which is two
  minutes of an intact chain and would read above 0.9 for a swarm that
  reconnected and then delivered nothing;

  and a reconnect time of zero, which is the best number the gate can read
  and means the fault did nothing at all.

The clock is the other half. Nodes stamp their files in simulated seconds
since the simulator came up and the record counts from the moment the run
began, so every function here takes the offset and the tests drive it with a
bring-up in front of the run.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_recovery.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_sim.recovery import (COMMAND_TOLERANCE_S, DELIVERY_GAP_S,
                               RecoveryError, commanded_window, delivery_gaps,
                               destroyed_by, fault_at, handback_block,
                               lost_route, outage_block, outage_window,
                               outages_after, radio_confirms, ratio_after,
                               recovery_block, reconnect_s, relay_slot,
                               route_restored, route_return_s,
                               safety_from_payload, targets_of)

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

# The simulator has been up for this long when the scenario starts.
EPOCH = 1000.0

# relay_kill: the kill lands at 120.4 and the far pair are cut off. The route
# comes back when uav_3 reaches the slot, and the recovery is confirmed one
# stability window later.
KILL_AT = 120.4
RETURNED = {NEAR: 148.0, FAR: 150.2}
CONFIRMED = {NEAR: 151.0, FAR: 153.2}

SLOT = {"commanded": [317.3, -36.8, 75.0], "clearance_m": 52.4,
        "band_reserved": True, "status": "RELAY_FEASIBLE"}


def ident(node, sequence):
    return f"{node}:{sequence}"


def router(node, minted=(), returned=None, confirmed=None, drained=None,
           slot=None, **extra):
    """One router's file, in the node's own clock.

    The episodes are the history and the three scalars are the latest of
    each. Both are written, because a ledger from before chunk 4.3 has only
    the scalars and the arithmetic still has to read it.
    """
    episodes = []
    if returned is not None:
        episodes.append({
            "returned_at": EPOCH + returned,
            "recovered_at": None if confirmed is None else EPOCH + confirmed,
            "drained_at": None if drained is None else EPOCH + drained,
            "lost_at": None})
    row = {
        "node": node,
        "generated_ids": [i for i, _ in minted],
        "generated_at": {i: EPOCH + when for i, when in minted},
        "route_returned_at": None if returned is None else EPOCH + returned,
        "recovered_at": None if confirmed is None else EPOCH + confirmed,
        "drain_end_at": None if drained is None else EPOCH + drained,
        "route_episodes": episodes,
        "relay_slot": slot,
        "unacknowledged_ids": [],
        "route_status": "route_up",
        "outages": [],
        "handback": {},
    }
    row.update(extra)
    return row


def routers(**overrides):
    """Four vehicles: the near pair connected throughout, the far pair cut off."""
    out = [
        router(ANCHOR, [(ident(ANCHOR, 1), 200.0)], returned=3.0,
               confirmed=5.0, drained=3.1),
        router(RELAY, [(ident(RELAY, 1), 100.0)], returned=3.0, confirmed=5.0,
               drained=3.1),
        router(NEAR, [(ident(NEAR, 1), 200.0)], returned=RETURNED[NEAR],
               confirmed=CONFIRMED[NEAR], drained=RETURNED[NEAR] + 1.2,
               slot=dict(SLOT)),
        router(FAR, [(ident(FAR, 1), 200.0)], returned=RETURNED[FAR],
               confirmed=CONFIRMED[FAR], drained=RETURNED[FAR] + 0.9),
    ]
    by_node = {row["node"]: row for row in out}
    for node, changes in overrides.items():
        row = by_node[node]
        row.update(changes)
        # A test that moves one of the scalars means the history moved with
        # it. Leaving the episode behind would make the fixture describe a
        # node whose own two records of the same event disagree, which is a
        # state no run produces.
        if "route_episodes" not in changes and (
                set(changes) & {"route_returned_at", "recovered_at",
                                "drain_end_at"}):
            row["route_episodes"] = [] if row["route_returned_at"] is None else [
                {"returned_at": row["route_returned_at"],
                 "recovered_at": row["recovered_at"],
                 "drained_at": row["drain_end_at"],
                 "lost_at": None}]
    return out


def gcs(delivered, created=None):
    """The destination's file, in the node's own clock.

    `created` is its copy of each creation stamp, which is what scopes the
    delivered set to the run. Unset it stands at the delivery time: a packet
    was created no later than it arrived.
    """
    created = created or {}
    return {"node": "gcs", "duplicated": 0,
            "ledger": [{"id": i,
                        "created_at": EPOCH + created.get(i, at),
                        "delivered_at": EPOCH + at}
                       for i, at in sorted(delivered.items())]}


def role(node, moved=False, **extra):
    row = {"node": node, "home_role": "survey", "epoch": 1 if moved else 0,
           "grants": 1 if moved else 0, "renewals": 0,
           "slot_commanded": list(SLOT["commanded"]) if moved else None,
           "granted_at": EPOCH + 145.0 if moved else None,
           "arrived_at": EPOCH + 148.0 if moved else None,
           "released_at": None, "lapsed_at": None, "returned_at": None,
           "moved": moved, "released": False, "returned_to_station": False}
    row.update(extra)
    return row


def roles():
    return [role(ANCHOR), role(NEAR, moved=True), role(FAR)]


def events(**overrides):
    row = {"type": "kill", "target": RELAY, "requested_t": 120.0,
           "observed_t": KILL_AT}
    row.update(overrides)
    return [row]


# ---------------------------------------------------------------- the safety
def test_the_four_fields_come_off_the_collector():
    payload = {"min_pairwise_separation_m": 16.0, "separation_violations": 0,
               "collision_contacts": 0, "contact_monitor_samples": 2475,
               "coverage_fraction": 0.9}
    assert safety_from_payload(payload) == {
        "min_pairwise_separation_m": 16.0, "separation_violations": 0,
        "collision_contacts": 0, "contact_monitor_samples": 2475}


def test_a_payload_without_the_sample_count_is_refused():
    with pytest.raises(RecoveryError) as caught:
        safety_from_payload({"min_pairwise_separation_m": 16.0,
                             "separation_violations": 0,
                             "collision_contacts": 0})
    assert "contact_monitor_samples" in str(caught.value)


def test_a_run_with_no_payload_has_nothing_to_say():
    with pytest.raises(RecoveryError):
        safety_from_payload(None)


# ----------------------------------------------------------------- the fault
def test_the_fault_is_timed_from_when_it_was_seen():
    assert fault_at(events()) == KILL_AT


def test_an_event_nobody_saw_land_is_not_a_fault():
    with pytest.raises(RecoveryError) as caught:
        fault_at(events(observed_t=None))
    assert "observed to land" in str(caught.value)


def test_the_earliest_observed_event_starts_the_outage():
    two = events() + [{"type": "kill", "target": FAR, "requested_t": 200.0,
                       "observed_t": 201.2}]
    assert fault_at(two) == KILL_AT


def test_a_killed_vehicle_is_named_only_once_it_has_been_seen_to_stop():
    assert destroyed_by(events()) == (RELAY,)
    assert destroyed_by(events(observed_t=None)) == ()


def test_a_blackout_destroys_nothing():
    # link_loss silences uav_2 and restores it. The vehicle is still flying
    # and its observations are still deliverable.
    assert destroyed_by([{"type": "comms_blackout", "target": RELAY,
                          "observed_t": 120.4}]) == ()


# ---------------------------------------------------------------- the outage
def test_only_the_nodes_that_lost_a_route_are_counted():
    lost = lost_route(routers(), KILL_AT, EPOCH)
    assert sorted(lost) == [NEAR, FAR]


def test_the_window_ends_when_the_last_cut_off_node_had_a_route():
    start, end = outage_window(routers(), KILL_AT, EPOCH)
    assert start == KILL_AT
    assert end == pytest.approx(RETURNED[FAR])


def test_a_swarm_that_never_lost_its_route_has_no_window():
    intact = routers(**{NEAR: {"route_returned_at": EPOCH + 3.0},
                        FAR: {"route_returned_at": EPOCH + 3.0}})
    with pytest.raises(RecoveryError) as caught:
        outage_window(intact, KILL_AT, EPOCH)
    assert "no router reports losing its route" in str(caught.value)


def test_without_the_offset_every_node_looks_like_it_lost_its_route():
    """Why the offset is not optional, and why it does not fail loudly.

    Every route_returned_at is a four figure number in the node's clock, so
    the anchor that held its route for the whole run passes the comparison
    against a 120 s fault alongside the two that were actually cut off. The
    window then ends after the run did.
    """
    assert sorted(lost_route(routers(), KILL_AT, epoch_s=0.0)) == sorted(VEHICLES)
    _, end = outage_window(routers(), KILL_AT, epoch_s=0.0)
    assert end > EPOCH


# ------------------------------------------------------------- the reconnect
def test_the_reconnect_runs_to_the_last_confirmed_route():
    got = reconnect_s(routers(), KILL_AT, EPOCH)
    assert got == pytest.approx(CONFIRMED[FAR] - KILL_AT)
    assert got <= 45.0


def test_a_route_that_came_back_and_was_never_confirmed_is_refused():
    flapping = routers(**{FAR: {"recovered_at": None}})
    with pytest.raises(RecoveryError) as caught:
        reconnect_s(flapping, KILL_AT, EPOCH)
    assert "still flapping" in str(caught.value)


def test_a_swarm_that_never_disconnected_reports_no_reconnect():
    intact = routers(**{NEAR: {"route_returned_at": EPOCH + 3.0},
                        FAR: {"route_returned_at": EPOCH + 3.0}})
    with pytest.raises(RecoveryError) as caught:
        reconnect_s(intact, KILL_AT, EPOCH)
    assert "the fault did nothing" in str(caught.value)


# ------------------------------------------------------------------ the slot
def test_the_slot_comes_from_the_node_that_decided_it():
    got = relay_slot(routers())
    assert got["commanded"] == SLOT["commanded"]
    assert got["decided_by"] == NEAR


def test_a_run_where_nobody_decided_a_slot_is_refused():
    with pytest.raises(RecoveryError) as caught:
        relay_slot(routers(**{NEAR: {"relay_slot": None}}))
    assert "no component ever decided" in str(caught.value)


def test_two_components_sending_the_relay_to_two_places_is_refused():
    elsewhere = dict(SLOT, commanded=[300.0, 0.0, 75.0])
    with pytest.raises(RecoveryError) as caught:
        relay_slot(routers(**{FAR: {"relay_slot": elsewhere}}))
    assert "decided twice" in str(caught.value)


def test_two_nodes_agreeing_on_one_slot_is_one_slot():
    agreed = routers(**{FAR: {"relay_slot": dict(SLOT)}})
    assert relay_slot(agreed)["commanded"] == SLOT["commanded"]


def test_a_clearance_measured_against_nothing_is_refused():
    with pytest.raises(RecoveryError) as caught:
        relay_slot(routers(**{NEAR: {"relay_slot": dict(SLOT,
                                                        clearance_m=None)}}))
    assert "no other aircraft flying" in str(caught.value)


# --------------------------------------------------------------- the traffic
def test_the_ratio_covers_only_what_was_minted_after_the_repair():
    # uav_2's observation at 100 s is before the fault and is not part of
    # this claim however it ended up.
    delivered = {ident(ANCHOR, 1): 200.4, ident(NEAR, 1): 200.4,
                 ident(FAR, 1): 200.4}
    assert ratio_after(routers(), gcs(delivered), 152.0, EPOCH) == 1.0


def test_one_observation_lost_after_the_repair_shows_up():
    delivered = {ident(ANCHOR, 1): 200.4, ident(NEAR, 1): 200.4}
    got = ratio_after(routers(), gcs(delivered), 152.0, EPOCH)
    assert got == pytest.approx(2 / 3)


def test_a_run_that_ended_before_it_could_show_recovery_is_refused():
    with pytest.raises(RecoveryError) as caught:
        ratio_after(routers(), gcs({}), 300.0, EPOCH)
    assert "fraction of nothing" in str(caught.value)


# -------------------------------------------------------------- the assembly
def block(**kwargs):
    delivered = {ident(ANCHOR, 1): 200.4, ident(RELAY, 1): 100.4,
                 ident(NEAR, 1): 200.4, ident(FAR, 1): 200.4}
    body = {"router_ledgers": routers(), "role_ledgers": roles(),
            "gcs_ledger": gcs(delivered), "fault_at_s": KILL_AT,
            "epoch_s": EPOCH}
    body.update(kwargs)
    return recovery_block(**body)


def test_the_block_carries_all_six_fields_and_the_slot():
    got = block()
    for key in ("time_to_reconnect_s", "relay_role_moved", "relay_role_holder",
                "relay_role_released", "mover_returned_to_station",
                "delivery_ratio_after_recovery"):
        assert key in got, key
    assert got["relay_role_holder"] == NEAR
    assert got["relay_slot"]["clearance_m"] == 52.4


def test_the_role_times_are_on_the_scenarios_clock():
    got = block()
    assert got["relay_role_granted_at_s"] == 145.0
    assert got["relay_role_arrived_at_s"] == 148.0


def test_a_run_where_no_vehicle_wrote_a_role_file_is_refused():
    with pytest.raises(RecoveryError) as caught:
        block(role_ledgers=[])
    assert "no role manager wrote a file" in str(caught.value)


def test_two_vehicles_claiming_the_role_is_refused():
    with pytest.raises(RecoveryError) as caught:
        block(role_ledgers=[role(NEAR, moved=True), role(FAR, moved=True)])
    assert "two vehicles" in str(caught.value)


# ----------------------------------------------------------- the outage block
def test_the_block_is_measured_over_the_window_the_run_produced():
    delivered = {ident(ANCHOR, 1): 200.4, ident(RELAY, 1): 100.4,
                 ident(NEAR, 1): 200.4, ident(FAR, 1): 200.4}
    got = outage_block(routers(), gcs(delivered), KILL_AT, EPOCH)
    assert got["outage_start_s"] == KILL_AT
    assert got["outage_end_s"] == pytest.approx(RETURNED[FAR])
    assert got["generated"] == 4
    assert got["missing_ids"] == []


def test_what_the_dead_relay_still_held_is_excused_only_when_it_is_named():
    held = {RELAY: {"unacknowledged_ids": [ident(RELAY, 1)]}}
    delivered = {ident(ANCHOR, 1): 200.4, ident(NEAR, 1): 200.4,
                 ident(FAR, 1): 200.4}
    without = outage_block(routers(**held), gcs(delivered), KILL_AT, EPOCH)
    assert without["missing_ids"] == [ident(RELAY, 1)]

    with_it = outage_block(routers(**held), gcs(delivered), KILL_AT, EPOCH,
                           destroyed=(RELAY,))
    assert with_it["missing_ids"] == []
    assert with_it["lost_with_vehicle"] == {RELAY: [ident(RELAY, 1)]}


# --------------------------------------------------------------- the handback
# link_loss: uav_2's radio comes back at 240, uav_4 owns the epoch, prepares
# the relay free path, waits for the destination to acknowledge an observation
# that arrived on it, and only then releases uav_3.
PREPARED = [FAR, RELAY, ANCHOR, "gcs"]
CONFIRMED_AT, RELEASED_AT = 262.0, 262.4


def handback(**overrides):
    row = {"epoch": 1, "epoch_owner": FAR, "staying_member": FAR,
           "prepared_path": list(PREPARED), "prepared_path_computations": 2,
           "confirmed_observation_id": ident(FAR, 700),
           "confirmed_at": EPOCH + CONFIRMED_AT,
           "release_sender": FAR, "release_at": EPOCH + RELEASED_AT,
           "relay_role_holder": NEAR}
    row.update(overrides)
    return row


def steady(first=250.0, last=280.0, step=0.2):
    """Deliveries at the frozen rate across the handback window."""
    out = {}
    when = first
    index = 1
    while when <= last:
        out[ident(FAR, index)] = when
        when = round(when + step, 3)
        index += 1
    return out


def handed_back(**overrides):
    body = {"router_ledgers": routers(**{FAR: {"handback": handback()}}),
            "gcs_ledger": gcs(steady()), "epoch_s": EPOCH}
    body.update(overrides)
    return handback_block(**body)


def test_a_run_where_nothing_prepared_a_path_has_no_handback():
    # relay_kill's relay never comes back, so there is nothing to give back.
    assert handback_block(routers(), gcs(steady()), EPOCH) is None


def test_the_transaction_comes_from_the_node_that_ran_it():
    got = handed_back()
    assert got["epoch_owner"] == FAR
    assert got["reported_by"] == FAR
    assert got["prepared_path"] == PREPARED
    assert got["confirmed_at"] == CONFIRMED_AT
    assert got["release_at"] == RELEASED_AT
    assert got["confirmed_at"] < got["release_at"]


def test_a_path_prepared_and_never_collected_is_refused():
    # A swarm that parked a vehicle and did not collect it is a result. A
    # record that omitted it would read like a run that never tried.
    stalled = handback(confirmed_at=None, release_at=None)
    with pytest.raises(RecoveryError) as caught:
        handback_block(routers(**{FAR: {"handback": stalled}}),
                       gcs(steady()), EPOCH)
    assert "did not collect it" in str(caught.value)


def test_two_nodes_running_one_transaction_is_refused():
    both = routers(**{FAR: {"handback": handback()},
                      ANCHOR: {"handback": handback(epoch_owner=ANCHOR)}})
    with pytest.raises(RecoveryError) as caught:
        handback_block(both, gcs(steady()), EPOCH)
    assert "run twice" in str(caught.value)


def test_a_handback_missing_a_field_the_schema_requires_is_refused():
    thin = handback(confirmed_observation_id=None)
    with pytest.raises(RecoveryError) as caught:
        handback_block(routers(**{FAR: {"handback": thin}}), gcs(steady()),
                       EPOCH)
    assert "confirmed_observation_id" in str(caught.value)


# ------------------------------------------------------- what it cost the flow
def test_an_unbroken_stream_has_no_gaps():
    assert handed_back()["observation_gap_count"] == 0


def test_a_hole_in_the_stream_is_counted():
    # Break before make: the relay leaves, the path is not carrying yet, and
    # the destination hears nothing for four seconds.
    quiet = {i: at for i, at in steady().items()
             if not 263.0 < at < 267.0}
    got = handed_back(gcs_ledger=gcs(quiet))
    assert got["observation_gap_count"] == 1


def test_the_gap_is_the_period_the_mesh_uses_to_lose_a_neighbour():
    assert DELIVERY_GAP_S == 1.0
    assert handed_back()["observation_gap_s"] == DELIVERY_GAP_S


def test_a_window_with_nothing_in_it_is_not_a_clean_handback():
    with pytest.raises(RecoveryError) as caught:
        delivery_gaps(gcs(steady()), 300.0, 320.0, EPOCH)
    assert "carried traffic through" in str(caught.value)


# ------------------------------------------------- after the vehicle went back
def test_an_outage_after_the_release_is_counted():
    late = routers(**{NEAR: {"outages": [EPOCH + 130.0, EPOCH + 265.0]}})
    assert outages_after(late, RELEASED_AT, EPOCH) == 1


def test_the_outage_that_started_the_whole_thing_is_not_counted_again():
    late = routers(**{NEAR: {"outages": [EPOCH + 130.0]},
                      FAR: {"outages": [EPOCH + 130.2]}})
    assert outages_after(late, RELEASED_AT, EPOCH) == 0


def test_a_swarm_that_ended_with_a_route_reports_it_restored():
    assert route_restored(routers(), KILL_AT, EPOCH) is True


def test_a_node_that_ended_disconnected_did_not_restore():
    broken = routers(**{FAR: {"route_status": "disconnected"}})
    assert route_restored(broken, KILL_AT, EPOCH) is False


def test_a_swarm_that_never_lost_a_route_restored_nothing():
    intact = routers(**{NEAR: {"route_returned_at": EPOCH + 3.0},
                        FAR: {"route_returned_at": EPOCH + 3.0}})
    assert route_restored(intact, KILL_AT, EPOCH) is False


def test_the_block_carries_the_handback_when_there_was_one():
    got = block(router_ledgers=routers(**{FAR: {"handback": handback()}}),
                gcs_ledger=gcs(steady()))
    assert got["handback"]["epoch_owner"] == FAR
    assert got["outage_count_after_release"] == 0
    assert got["route_restored_after_blackout"] is True


# ------------------------------------------- the vehicle the fault happened at
# link_loss: uav_2's radio is gated at 120.4 and the radio lifts the gate at
# 240. So uav_2 loses its own route when it is gated and gets it back when the
# hold runs out, on the scenario's timetable and not by anything the swarm did.
GATED_BACK = 245.0

def blacked_out():
    """The same four vehicles, with the relay gated rather than destroyed.

    Its own route comes back when the hold runs out, which is the scenario's
    timetable rather than the swarm doing anything.
    """
    gated = {"route_returned_at": EPOCH + GATED_BACK,
             "recovered_at": EPOCH + GATED_BACK + 3.0,
             "drain_end_at": EPOCH + GATED_BACK + 3.0,
             "route_episodes": [{"returned_at": EPOCH + GATED_BACK,
                                 "recovered_at": EPOCH + GATED_BACK + 3.0,
                                 "drained_at": EPOCH + GATED_BACK + 3.0,
                                 "lost_at": None}]}
    return routers(**{RELAY: gated})


def blackout_events():
    return [{"type": "comms_blackout", "target": RELAY, "requested_t": 120.0,
             "observed_t": KILL_AT, "restore_at_s": 240.0}]


def test_every_vehicle_a_fault_was_applied_to_is_named():
    assert targets_of(blackout_events()) == (RELAY,)
    assert targets_of(events()) == (RELAY,)
    assert targets_of([]) == ()


def test_a_fault_nobody_saw_land_names_nobody():
    assert targets_of([dict(blackout_events()[0], observed_t=None)]) == ()


def test_the_gated_vehicle_is_not_counted_as_having_recovered():
    lost = lost_route(blacked_out(), KILL_AT, EPOCH, exclude=(RELAY,))
    assert sorted(lost) == [NEAR, FAR]


def test_without_the_exclusion_the_recovery_waits_for_the_stopwatch():
    """What the exclusion is for, written down.

    The gated radio comes back at 245 because the scenario said 240 and the
    hold ran out, so a reconnect measured over it is 128 s of waiting for a
    timetable rather than 24 s of a swarm electing a new relay.
    """
    slow = reconnect_s(blacked_out(), KILL_AT, EPOCH)
    quick = reconnect_s(blacked_out(), KILL_AT, EPOCH, exclude=(RELAY,))
    assert slow > 45.0
    assert quick == pytest.approx(CONFIRMED[FAR] - KILL_AT)


def test_the_window_ends_when_the_cut_off_members_are_back():
    _, end = outage_window(blacked_out(), KILL_AT, EPOCH, exclude=(RELAY,))
    assert end == pytest.approx(RETURNED[FAR])


def test_the_drain_is_the_one_the_outage_caused():
    """uav_2's store empties two minutes later when its own radio returns.

    That is a different event with a different cause, and folding the two
    together reports a two minute drain for a design that promises 2.25 s.
    """
    delivered = {ident(ANCHOR, 1): 200.4, ident(RELAY, 1): 100.4,
                 ident(NEAR, 1): 200.4, ident(FAR, 1): 200.4}
    got = outage_block(blacked_out(), gcs(delivered), KILL_AT, EPOCH,
                       exclude=(RELAY,))
    assert got["outage_end_s"] == pytest.approx(RETURNED[FAR])
    assert got["backlog_drain_s"] == pytest.approx(
        RETURNED[FAR] + 0.9 - RETURNED[FAR])
    assert got["drain_by_node"][NEAR] == pytest.approx(RETURNED[NEAR] + 1.2)
    # The gated vehicle's own store, two minutes later, is still a fact about
    # the run. It is out of the bound and in the record.
    assert got["drain_counted_for"] == sorted([NEAR, FAR])
    assert got["drain_by_node"][RELAY] == pytest.approx(GATED_BACK + 3.0)


def test_a_route_that_came_back_and_never_drained_is_refused():
    # There is no moment at which the backlog this outage built had finished,
    # and the bound the gate reads is measured to that moment.
    stuck = routers()
    for row in stuck:
        if row["node"] == FAR:
            row["route_episodes"][0]["drained_at"] = None
    with pytest.raises(RecoveryError) as caught:
        outage_block(stuck, gcs({ident(FAR, 1): 200.4}), KILL_AT, EPOCH)
    assert "never ran the store empty" in str(caught.value)


def test_a_later_flap_does_not_become_the_recovery():
    """What the episodes are for.

    uav_3 got its route back at 140 s of the first complete link_loss and
    then withdrew and recomputed once more while it was flying home at 257.
    One field holding the latest of those read as a swarm that took 137
    seconds to reconnect.
    """
    flapped = routers()
    for row in flapped:
        if row["node"] == NEAR:
            row["route_episodes"].append({
                "returned_at": EPOCH + 257.3, "recovered_at": EPOCH + 260.3,
                "drained_at": EPOCH + 257.4, "lost_at": None})
            row["route_returned_at"] = EPOCH + 257.3
            row["recovered_at"] = EPOCH + 260.3
            row["drain_end_at"] = EPOCH + 257.4
    assert reconnect_s(flapped, KILL_AT, EPOCH) == pytest.approx(
        CONFIRMED[FAR] - KILL_AT)
    _, end = outage_window(flapped, KILL_AT, EPOCH)
    assert end == pytest.approx(RETURNED[FAR])


def test_a_ledger_from_before_the_episodes_is_still_readable():
    older = routers()
    for row in older:
        del row["route_episodes"]
    lost = lost_route(older, KILL_AT, EPOCH)
    assert sorted(lost) == [NEAR, FAR]
    assert lost[FAR]["recovered_at"] == pytest.approx(CONFIRMED[FAR])


def test_the_block_measures_a_blackout_over_the_members_it_cut_off():
    delivered = {ident(ANCHOR, 1): 200.4, ident(RELAY, 1): 100.4,
                 ident(NEAR, 1): 200.4, ident(FAR, 1): 200.4}
    got = block(router_ledgers=blacked_out(), gcs_ledger=gcs(delivered),
                exclude=(RELAY,))
    assert got["time_to_reconnect_s"] <= 45.0
    assert got["route_restored_after_blackout"] is True


# ------------------------------------------------------ the commanded window
#
# queue_drain: the radio is gated at 60 and lifts its own gate at 105, which
# is the 45 seconds the 512 packet queue is sized against. Nothing shortens
# it, because the election is off, so the route comes back a moment after the
# radio does rather than a moment after a relay reaches its slot.
DRAIN_AT, DRAIN_LIFT = 60.0, 105.0
DRAIN_SEEN = 61.1
DRAIN_BACK = 106.5


def drained_routers(**overrides):
    """The four vehicles of a run nothing was allowed to rescue."""
    out = [
        router(ANCHOR, [(ident(ANCHOR, 1), 70.0)], returned=3.0, confirmed=5.0,
               drained=3.1),
        router(RELAY, [(ident(RELAY, 1), 70.0)], returned=DRAIN_BACK + 0.2,
               confirmed=DRAIN_BACK + 3.2, drained=DRAIN_BACK + 0.4),
        router(NEAR, [(ident(NEAR, 1), 70.0)], returned=DRAIN_BACK,
               confirmed=DRAIN_BACK + 3.0, drained=DRAIN_BACK + 1.3),
        router(FAR, [(ident(FAR, 1), 70.1)], returned=DRAIN_BACK,
               confirmed=DRAIN_BACK + 3.0, drained=DRAIN_BACK + 1.1),
    ]
    by_node = {row["node"]: row for row in out}
    for node, changes in overrides.items():
        by_node[node].update(changes)
    return out


def radio(gated=DRAIN_AT + 0.1, lifted=DRAIN_LIFT + 0.1):
    """The link layer's own file, in its own clock."""
    return {"node": "link_layer", "transmissions": 4000,
            "blackout_started_at": None if gated is None else EPOCH + gated,
            "blackout_restored_at": None if lifted is None else EPOCH + lifted}


def drain_events(**overrides):
    row = {"type": "comms_blackout", "target": RELAY, "requested_t": DRAIN_AT,
           "observed_t": DRAIN_SEEN, "restore_at_s": DRAIN_LIFT}
    row.update(overrides)
    return [row]


def test_a_kill_commands_no_window():
    # It has no end. The vehicle is gone, and the run's own arithmetic is the
    # only thing that says when the swarm got over it.
    assert commanded_window(events()) is None
    assert commanded_window([]) is None


def test_a_blackout_commands_the_window_it_was_given():
    assert commanded_window(drain_events()) == (DRAIN_AT, DRAIN_LIFT)
    assert commanded_window(blackout_events()) == (120.0, 240.0)


def test_a_blackout_nobody_saw_land_commands_nothing():
    assert commanded_window(drain_events(observed_t=None)) is None


def test_a_blackout_with_no_end_commands_nothing():
    assert commanded_window(drain_events(restore_at_s=None)) is None


def test_the_radio_has_to_say_it_gated_itself_when_it_was_told_to():
    got = radio_confirms(radio(), (DRAIN_AT, DRAIN_LIFT), EPOCH, DRAIN_BACK)
    assert got["radio_gated_at_s"] == pytest.approx(DRAIN_AT + 0.1)
    assert got["radio_restored_at_s"] == pytest.approx(DRAIN_LIFT + 0.1)


def test_a_radio_that_gated_itself_somewhere_else_is_refused():
    late = radio(gated=DRAIN_AT + COMMAND_TOLERANCE_S + 1.0)
    with pytest.raises(RecoveryError) as caught:
        radio_confirms(late, (DRAIN_AT, DRAIN_LIFT), EPOCH, DRAIN_BACK)
    assert "describing different faults" in str(caught.value)


def test_a_radio_that_never_gated_anything_is_refused():
    with pytest.raises(RecoveryError) as caught:
        radio_confirms(radio(gated=None), (DRAIN_AT, DRAIN_LIFT), EPOCH,
                       DRAIN_BACK)
    assert "no record of gating" in str(caught.value)


def test_a_commanded_window_with_no_radio_file_is_refused():
    """Otherwise the two numbers in the record are the two somebody typed."""
    with pytest.raises(RecoveryError) as caught:
        radio_confirms(None, (DRAIN_AT, DRAIN_LIFT), EPOCH, DRAIN_BACK)
    assert "no radio ledger" in str(caught.value)


def test_a_gate_that_never_lifted_and_nothing_outlasted_is_refused():
    # The run ended inside its own outage. There is no window to report.
    with pytest.raises(RecoveryError) as caught:
        radio_confirms(radio(lifted=None), (DRAIN_AT, DRAIN_LIFT), EPOCH,
                       None)
    assert "never ended" in str(caught.value)


def test_a_swarm_that_reconnected_before_the_hold_ran_out_keeps_its_own_end():
    """link_loss. The relay reaches the slot a hundred seconds early.

    The command is a ceiling on the window and never the window itself, or a
    run that fixed itself in 20 seconds would report a two minute outage.
    """
    start, end = outage_window(blacked_out(), KILL_AT, EPOCH, exclude=(RELAY,),
                               commanded=(120.0, 240.0))
    assert start == 120.0
    assert end == pytest.approx(RETURNED[FAR])


def test_the_hold_closes_the_window_when_nothing_else_did():
    """queue_drain. The route comes back 1.5 s after the radio does.

    Those 1.5 seconds are the mesh hearing the neighbour again and the link
    state travelling, which is the swarm noticing the fault ended rather than
    any part of the fault. A window measured to them reports 46.5 s of an
    outage that was commanded for 45.
    """
    start, end = outage_window(drained_routers(), DRAIN_SEEN, EPOCH,
                               exclude=(RELAY,),
                               commanded=(DRAIN_AT, DRAIN_LIFT))
    assert (start, end) == (DRAIN_AT, DRAIN_LIFT)
    assert route_return_s(drained_routers(), DRAIN_SEEN, EPOCH,
                          exclude=(RELAY,)) == pytest.approx(DRAIN_BACK)


def test_without_a_command_both_ends_are_still_measured():
    start, end = outage_window(routers(), KILL_AT, EPOCH)
    assert (start, end) == (KILL_AT, pytest.approx(RETURNED[FAR]))


def drain_block(**kwargs):
    delivered = {ident(ANCHOR, 1): 70.4, ident(RELAY, 1): 108.0,
                 ident(NEAR, 1): 108.0, ident(FAR, 1): 108.0}
    body = {"router_ledgers": drained_routers(), "gcs_ledger": gcs(delivered),
            "fault_at_s": DRAIN_SEEN, "epoch_s": EPOCH, "exclude": (RELAY,),
            "commanded": (DRAIN_AT, DRAIN_LIFT), "radio_ledger": radio()}
    body.update(kwargs)
    return outage_block(**body)


def test_the_drain_is_measured_from_the_route_coming_back():
    """The outage ends when the fault lifts and the queue empties later.

    A queue has somewhere to empty into when the route returns, so the bound
    the gate reads against 2.25 s is measured from there. Measuring it from
    the end of the outage would charge the mesh's reconvergence to the
    forward rate.
    """
    got = drain_block()
    assert got["outage_start_s"] == DRAIN_AT
    assert got["outage_end_s"] == DRAIN_LIFT
    assert got["drain_start_s"] == pytest.approx(DRAIN_BACK)
    assert got["backlog_drain_s"] == pytest.approx(1.3)
    assert got["generated_during_outage"] == 4


def test_the_block_says_which_of_the_two_ended_the_outage():
    assert drain_block()["outage_end_source"] == "fault_lifted"

    delivered = {ident(ANCHOR, 1): 200.4, ident(RELAY, 1): 100.4,
                 ident(NEAR, 1): 200.4, ident(FAR, 1): 200.4}
    early = outage_block(blacked_out(), gcs(delivered), KILL_AT, EPOCH,
                         exclude=(RELAY,), commanded=(120.0, 240.0),
                         radio_ledger=radio(gated=120.1, lifted=240.1))
    assert early["outage_end_source"] == "route_returned"


def test_the_block_reports_the_moments_the_window_is_not_taken_from():
    got = drain_block()
    assert got["outage_observed_s"] == pytest.approx(DRAIN_SEEN)
    assert got["radio_gated_at_s"] == pytest.approx(DRAIN_AT + 0.1)
    assert got["radio_restored_at_s"] == pytest.approx(DRAIN_LIFT + 0.1)

