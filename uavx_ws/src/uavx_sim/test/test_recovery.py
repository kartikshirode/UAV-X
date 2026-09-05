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

from uavx_sim.recovery import (RecoveryError, destroyed_by, fault_at,
                               lost_route, outage_block, outage_window,
                               ratio_after, recovery_block, reconnect_s,
                               relay_slot, safety_from_payload)

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


def router(node, minted=(), returned=None, confirmed=None, slot=None,
           **extra):
    """One router's file, in the node's own clock."""
    row = {
        "node": node,
        "generated_ids": [i for i, _ in minted],
        "generated_at": {i: EPOCH + when for i, when in minted},
        "route_returned_at": None if returned is None else EPOCH + returned,
        "recovered_at": None if confirmed is None else EPOCH + confirmed,
        "relay_slot": slot,
        "unacknowledged_ids": [],
    }
    row.update(extra)
    return row


def routers(**overrides):
    """Four vehicles: the near pair connected throughout, the far pair cut off."""
    out = [
        router(ANCHOR, [(ident(ANCHOR, 1), 200.0)], returned=3.0,
               confirmed=5.0),
        router(RELAY, [(ident(RELAY, 1), 100.0)], returned=3.0, confirmed=5.0),
        router(NEAR, [(ident(NEAR, 1), 200.0)], returned=RETURNED[NEAR],
               confirmed=CONFIRMED[NEAR], slot=dict(SLOT)),
        router(FAR, [(ident(FAR, 1), 200.0)], returned=RETURNED[FAR],
               confirmed=CONFIRMED[FAR]),
    ]
    by_node = {row["node"]: row for row in out}
    for node, changes in overrides.items():
        by_node[node].update(changes)
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
