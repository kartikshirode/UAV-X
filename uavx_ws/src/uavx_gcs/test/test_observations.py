"""Chunk 4.2: the outage block, and the ways a passing run can be a lie.

Week 3 settled what a delivery ratio is. Week 4 adds time to it, and time is
where the flattering answers live:

  450 observations generated, all of them minted before the route went down,
  which tests nothing about a queue holding data through an outage;

  a backlog split across two nodes, each with a peak of 225, against a design
  that says one member takes custody so the depth is actually reached;

  a drain measured from the arrival times at the destination, which is a
  different quantity because link latency does not disappear when a queue
  does;

  and a delivered set that matches the generated one only because an id
  nobody minted made up for one that went missing.

Every number here is read off the files the nodes wrote. The window is the
one thing that is not: when a vehicle was killed is the runner's knowledge,
and a block that inferred it from a gap in the deliveries would be deciding
that the failure happened from the evidence that it happened.

Two of those come from the runner. The scenario's clock, because the nodes
count from the moment the simulator came up and the record counts from the
moment the run began. And which vehicles were destroyed, because an
observation still held only by the aircraft that minted it stops existing
when the aircraft does, and that is a different thing from the swarm
dropping it.

    python3 -m pytest -q uavx_ws/src/uavx_gcs/test/test_observations.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_gcs.ledger import (LedgerError, backlog_custodian, delivered_rows,
                             generated_rows, observations,
                             observations_set_equal)

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

OUT_START, OUT_END = 60.0, 105.0


def ident(node, sequence):
    return f"{node}:{sequence}"


def router(node, minted, custodied=None, **extra):
    """One router's ledger: what it minted and when, and what it held."""
    row = {
        "node": node,
        "generated_ids": sorted(minted),
        "generated_at": dict(minted),
        "custodied_ids": sorted(custodied if custodied is not None else minted),
        "expired": 0,
        "evicted": 0,
        "peak_queue_depth": len(minted),
        "control_queue_max_delay_s": 0.0,
        "drain_end_at": None,
    }
    row.update(extra)
    return row


def gcs(delivered, duplicated=0, created=None, **extra):
    """The destination's file.

    `created` is the destination's own copy of each creation stamp, which is
    what scopes the delivered set to the run. Unset it stands at the delivery
    time: a packet was created no later than it arrived, and the tests that
    care about the difference pass it.
    """
    created = created or {}
    row = {
        "node": "gcs",
        "duplicated": duplicated,
        "control_queue_max_delay_s": 0.0,
        "ledger": [{"id": i, "created_at": created.get(i, at),
                    "delivered_at": at}
                   for i, at in sorted(delivered.items())],
    }
    row.update(extra)
    return row


def a_run(**kwargs):
    """Two origins, one cut off for the outage, its backlog held by uav_3."""
    near = {ident(NEAR, n): 60.0 + n * 0.2 for n in range(1, 4)}
    far = {ident(FAR, n): 60.1 + n * 0.2 for n in range(1, 4)}
    delivered = {i: 106.0 for i in list(near) + list(far)}
    routers = [
        router(NEAR, near, custodied=sorted(list(near) + list(far)),
               drain_end_at=106.5),
        router(FAR, far),
    ]
    body = {"router_ledgers": routers, "gcs_ledger": gcs(delivered),
            "outage_start_s": OUT_START, "outage_end_s": OUT_END}
    body.update(kwargs)
    return observations(**body)


# ------------------------------------------------------------ the two sides
def test_the_origins_own_the_denominator():
    got = a_run()
    assert got["generated"] == 6
    assert got["unique_delivered"] == 6
    assert got["missing_ids"] == []
    assert got["unexpected_ids"] == []
    assert observations_set_equal(got) is True


def test_an_id_claimed_by_two_nodes_is_refused():
    # Identity is (origin, sequence) and the origin is in the id, so a
    # collision means two processes minting from one counter.
    shared = {ident(NEAR, 1): 61.0}
    with pytest.raises(LedgerError) as caught:
        generated_rows([router(NEAR, shared), router(FAR, shared)])
    assert ident(NEAR, 1) in str(caught.value)


def test_a_generated_id_with_no_time_is_refused():
    row = router(NEAR, {ident(NEAR, 1): 61.0})
    row["generated_at"] = {}
    with pytest.raises(LedgerError) as caught:
        generated_rows([row])
    assert "no time" in str(caught.value)


def test_a_ledger_with_no_node_name_is_refused():
    with pytest.raises(LedgerError):
        generated_rows([{"generated_ids": [ident(NEAR, 1)]}])


def test_the_destination_without_rows_is_refused():
    # Aggregate counts cannot say which ids arrived inside a window.
    with pytest.raises(LedgerError) as caught:
        delivered_rows({"node": "gcs", "delivered_ids": [ident(NEAR, 1)]})
    assert "per delivery rows" in str(caught.value)


def test_a_duplicate_arrival_keeps_the_first_time():
    row = gcs({})
    row["ledger"] = [{"id": ident(NEAR, 1), "delivered_at": 9.0},
                     {"id": ident(NEAR, 1), "delivered_at": 4.0}]
    assert delivered_rows(row) == {ident(NEAR, 1): 4.0}


def test_a_delivery_row_with_no_time_is_refused():
    row = gcs({})
    row["ledger"] = [{"id": ident(NEAR, 1), "delivered_at": None}]
    with pytest.raises(LedgerError):
        delivered_rows(row)


def test_a_delivered_id_nobody_generated_is_reported_not_hidden():
    near = {ident(NEAR, 1): 61.0}
    got = observations([router(NEAR, near)],
                       gcs({ident(NEAR, 1): 106.0, ident(FAR, 9): 106.0}),
                       OUT_START, OUT_END)
    assert got["unexpected_ids"] == [ident(FAR, 9)]
    assert got["unexpected_count"] == 1
    assert observations_set_equal(got) is False


def test_a_missing_id_survives_a_matching_count():
    # One lost and one invented gives a ratio of 1.0 and two non-empty lists.
    near = {ident(NEAR, 1): 61.0, ident(NEAR, 2): 61.2}
    got = observations([router(NEAR, near)],
                       gcs({ident(NEAR, 1): 106.0, ident(FAR, 9): 106.0}),
                       OUT_START, OUT_END)
    assert got["unique_delivered"] == got["generated"]
    assert got["missing_ids"] == [ident(NEAR, 2)]
    assert observations_set_equal(got) is False


# --------------------------------------------------------------- the window
def test_only_what_was_minted_inside_the_window_counts():
    # Round 7 finding 8. Producing them early would satisfy a count and prove
    # nothing about a queue holding data through an outage.
    early = {ident(NEAR, 1): OUT_START - 1.0}
    inside = {ident(NEAR, 2): OUT_START + 1.0}
    late = {ident(NEAR, 3): OUT_END + 1.0}
    minted = {**early, **inside, **late}
    got = observations([router(NEAR, minted)],
                       gcs({i: OUT_END + 2.0 for i in minted}),
                       OUT_START, OUT_END)
    assert got["generated"] == 3
    assert got["generated_during_outage"] == 1


def test_the_window_is_half_open_at_its_end():
    minted = {ident(NEAR, 1): OUT_END}
    got = observations([router(NEAR, minted)], gcs({ident(NEAR, 1): 110.0}),
                       OUT_START, OUT_END)
    assert got["generated_during_outage"] == 0


def test_a_window_that_runs_backwards_is_refused():
    with pytest.raises(LedgerError):
        a_run(outage_start_s=105.0, outage_end_s=60.0)


def test_a_window_end_that_is_not_a_time_is_refused():
    with pytest.raises(LedgerError):
        a_run(outage_end_s="soon")


# ---------------------------------------------------------------- the drain
def test_the_drain_is_measured_at_the_queue():
    got = a_run()
    assert got["drain_start_s"] == OUT_END
    assert got["drain_end_s"] == 106.5
    assert got["backlog_drain_s"] == pytest.approx(1.5)


def test_an_emptying_before_the_route_returned_is_not_the_drain():
    # Every node's store runs empty constantly in a healthy run.
    near = {ident(NEAR, 1): 61.0}
    got = observations([router(NEAR, near, drain_end_at=12.0)],
                       gcs({ident(NEAR, 1): 106.0}), OUT_START, OUT_END)
    assert got["drain_end_s"] == OUT_END
    assert got["backlog_drain_s"] == 0.0


def test_a_drain_starting_before_the_route_came_back_is_refused():
    with pytest.raises(LedgerError) as caught:
        a_run(drain_start_s=90.0)
    assert "before its route comes back" in str(caught.value)


def test_delivery_completing_is_a_different_clock_from_the_queue_emptying():
    # Link latency does not disappear when a queue does, which is why these
    # are two fields. The last packet leaves the queue and arrives later.
    near = {ident(NEAR, 1): 61.0}
    got = observations([router(NEAR, near, drain_end_at=106.2)],
                       gcs({ident(NEAR, 1): 106.4}), OUT_START, OUT_END)
    assert got["drain_end_s"] == 106.2
    assert got["delivery_complete_s"] == 106.4


def test_delivery_complete_is_the_last_outage_observation_to_arrive():
    near = {ident(NEAR, 1): 61.0, ident(NEAR, 2): 61.2}
    late = {ident(NEAR, 3): OUT_END + 30.0}
    minted = {**near, **late}
    delivered = {ident(NEAR, 1): 106.0, ident(NEAR, 2): 107.0,
                 ident(NEAR, 3): 140.0}
    got = observations([router(NEAR, minted)], gcs(delivered),
                       OUT_START, OUT_END)
    # 140.0 belongs to an observation minted after the outage ended, so it is
    # not what the backlog took to arrive.
    assert got["delivery_complete_s"] == 107.0


def test_only_what_arrived_after_the_restore_is_counted_as_drained():
    near = {ident(NEAR, 1): 61.0, ident(NEAR, 2): 61.2}
    delivered = {ident(NEAR, 1): 61.5, ident(NEAR, 2): 106.0}
    got = observations([router(NEAR, near)], gcs(delivered),
                       OUT_START, OUT_END)
    assert got["generated_during_outage"] == 2
    assert got["delivered_after_restore"] == 1


# ------------------------------------------------------------- the custodian
def test_the_custodian_is_the_node_that_held_somebody_elses_traffic():
    got = a_run()
    assert got["backlog_custodian"] == NEAR
    assert got["custodied"] == 6


def test_a_node_that_only_held_its_own_is_not_a_custodian():
    near = {ident(NEAR, 1): 61.0}
    assert backlog_custodian([router(NEAR, near)], near.keys()) is None


def test_the_lowest_id_wins_when_two_nodes_held_the_backlog():
    # A split backlog is the failure queue_drain exists to rule out, and the
    # block reports the lower id rather than picking whichever came first in
    # a directory listing.
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    both = sorted(list(near) + list(far))
    holders = [router(FAR, far, custodied=both), router(NEAR, near, custodied=both)]
    assert backlog_custodian(holders, both) == NEAR


def test_the_anchor_that_forwarded_everything_is_not_the_custodian():
    """The defect the accepted relay_kill record carries.

    uav_1 is the ground station anchor. It was never cut off from anything,
    and it holds ids it did not mint on every run there is, because that is
    what forwarding is. Custody alone names it, and it beats every vehicle on
    a lowest id tie. The custodian is the member a component named while it
    had no route, which uav_1 never was.
    """
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    both = sorted(list(near) + list(far))
    holders = [
        router(ANCHOR, {}, custodied=both),
        router(NEAR, near, custodied=both, custodian_named=NEAR),
        router(FAR, far, custodian_named=NEAR),
    ]
    assert backlog_custodian(holders, both) == NEAR


def test_a_component_of_one_names_itself_and_custodies_nothing():
    """queue_drain gates uav_2, which is then alone and holds only its own.

    It names itself, correctly, and it is not the custodian of anybody's
    backlog. Being named is not enough on its own any more than holding is.
    """
    relay = {ident(RELAY, 1): 61.0}
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    wanted = sorted(list(relay) + list(near) + list(far))
    holders = [
        router(RELAY, relay, custodian_named=RELAY),
        router(NEAR, near, custodied=sorted(list(near) + list(far)),
               custodian_named=NEAR),
        router(FAR, far, custodian_named=NEAR),
    ]
    assert backlog_custodian(holders, wanted) == NEAR


def test_a_ledger_from_before_the_name_is_still_read_by_custody():
    # Every record written before chunk 4.4 carries no name, and the
    # arithmetic still has to read them.
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    both = sorted(list(near) + list(far))
    holders = [router(NEAR, near, custodied=both), router(FAR, far)]
    assert backlog_custodian(holders, both) == NEAR


def test_a_run_with_nothing_minted_in_the_window_names_no_custodian():
    near = {ident(NEAR, 1): 1.0}
    got = observations([router(NEAR, near)], gcs({ident(NEAR, 1): 2.0}),
                       OUT_START, OUT_END)
    assert "backlog_custodian" not in got


# ---------------------------------------------------------------- the totals
def test_the_worst_control_delay_includes_the_ground_station():
    got = a_run(gcs_ledger=gcs({}, control_queue_max_delay_s=0.4))
    assert got["control_queue_max_delay_s"] == pytest.approx(0.4)


def test_losses_are_summed_across_the_swarm_and_the_depth_is_the_worst():
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    got = observations(
        [router(NEAR, near, expired=1, evicted=2, peak_queue_depth=7),
         router(FAR, far, expired=3, evicted=0, peak_queue_depth=99)],
        gcs({}), OUT_START, OUT_END)
    assert got["expired"] == 4
    assert got["evicted"] == 2
    assert got["peak_queue_depth"] == 99


def test_every_row_the_schema_requires_is_present():
    required = ("generated_ids", "delivered_ids", "generated",
                "unique_delivered", "duplicated", "expired", "evicted",
                "peak_queue_depth", "backlog_drain_s",
                "control_queue_max_delay_s", "outage_start_s", "outage_end_s",
                "generated_during_outage", "delivered_after_restore",
                "drain_start_s", "drain_end_s", "delivery_complete_s",
                "ledger")
    got = a_run()
    for key in required:
        assert key in got, key


def test_the_ledger_carries_a_row_per_generated_observation():
    got = a_run()
    assert len(got["ledger"]) == got["generated"]
    row = got["ledger"][0]
    assert set(row) == {"id", "created_at_s", "delivered_at_s"}


def test_an_undelivered_observation_keeps_its_row_with_no_arrival():
    near = {ident(NEAR, 1): 61.0, ident(NEAR, 2): 61.2}
    got = observations([router(NEAR, near)], gcs({ident(NEAR, 1): 106.0}),
                       OUT_START, OUT_END)
    rows = {r["id"]: r["delivered_at_s"] for r in got["ledger"]}
    assert rows[ident(NEAR, 2)] is None


# ------------------------------------------------------- the scenario's clock
def test_the_offset_moves_every_time_the_nodes_wrote():
    """The nodes count from simulator bring-up and the record from t=0."""
    minted = {ident(NEAR, 1): 1160.0, ident(NEAR, 2): 1160.4}
    routers = [router(NEAR, minted, drain_end_at=1166.0)]
    delivered = {i: 1165.5 for i in minted}
    got = observations(routers, gcs(delivered), 60.0, 65.0,
                       epoch_s=1100.0)
    assert got["ledger"][0]["created_at_s"] == 60.0
    assert got["ledger"][0]["delivered_at_s"] == 65.5
    assert got["generated_during_outage"] == 2
    assert got["drain_end_s"] == 66.0
    assert got["backlog_drain_s"] == 1.0


def test_without_the_offset_nothing_falls_inside_the_window():
    # The failure this exists to stop, written down: 1160 is outside every
    # window a 300 s scenario has, so the block would report a clean run in
    # which nothing was generated during the outage at all.
    minted = {ident(NEAR, 1): 1160.0}
    got = observations([router(NEAR, minted)], gcs({ident(NEAR, 1): 1165.5}),
                       60.0, 105.0)
    assert got["generated_during_outage"] == 0


def test_an_offset_that_is_not_a_time_is_refused():
    with pytest.raises(LedgerError) as caught:
        a_run(epoch_s="the beginning")
    assert "the simulated time the scenario started at" in str(caught.value)


# -------------------------------------------------- what went down with it
def lost_run(**kwargs):
    """uav_2 is killed holding one of its own, and one of uav_4's."""
    relay = {ident(RELAY, 1): 118.0, ident(RELAY, 2): 119.8}
    far = {ident(FAR, n): 118.0 + n for n in range(1, 3)}
    delivered = {ident(RELAY, 1): 118.4}
    delivered.update({i: 150.0 for i in far})
    routers = [
        router(RELAY, relay, custodied=sorted(list(relay) + [ident(FAR, 1)]),
               unacknowledged_ids=[ident(RELAY, 2)]),
        router(FAR, far, unacknowledged_ids=[]),
    ]
    body = {"router_ledgers": routers, "gcs_ledger": gcs(delivered),
            "outage_start_s": 120.0, "outage_end_s": 152.0,
            "destroyed": [RELAY]}
    body.update(kwargs)
    return observations(**body)


def test_what_the_aircraft_was_still_holding_alone_is_named():
    got = lost_run()
    assert got["lost_with_vehicle"] == {RELAY: [ident(RELAY, 2)]}
    assert got["lost_with_vehicle_count"] == 1


def test_it_leaves_the_generated_set_rather_than_the_missing_one():
    # The claim the gate reads is that the swarm delivered what it was
    # carrying. An observation that stopped existing is not carried, and
    # leaving it in missing_ids would make the claim unsatisfiable by any
    # implementation rather than false for this one.
    got = lost_run()
    assert ident(RELAY, 2) not in got["generated_ids"]
    assert got["missing_ids"] == []
    assert observations_set_equal(got) is True


def test_the_data_the_dead_vehicle_carried_for_others_is_not_lost():
    # Retained by its origin until acknowledged, so it comes back on the
    # first retry over the repaired path. It is delivered here at 150 s.
    got = lost_run()
    assert ident(FAR, 1) in got["delivered_ids"]
    assert ident(FAR, 1) not in got.get("lost_with_vehicle", {}).get(RELAY, [])


def test_an_observation_that_arrived_was_not_lost():
    # The dead vehicle's ledger lists it as unacknowledged because the ack
    # never got home, and the ground station has it. Delivery wins.
    got = lost_run(gcs_ledger=gcs({ident(RELAY, 1): 118.4,
                                   ident(RELAY, 2): 119.9,
                                   ident(FAR, 1): 150.0,
                                   ident(FAR, 2): 150.0}))
    assert "lost_with_vehicle" not in got
    assert ident(RELAY, 2) in got["generated_ids"]


def test_a_run_that_destroyed_nothing_reports_no_losses():
    assert "lost_with_vehicle" not in a_run()


def test_only_a_vehicle_the_run_destroyed_can_lose_anything():
    # uav_4 is alive and its unacknowledged observation is still deliverable,
    # so nothing about it is excused.
    got = lost_run(destroyed=[])
    assert "lost_with_vehicle" not in got
    assert ident(RELAY, 2) in got["missing_ids"]


def test_a_destroyed_vehicle_with_no_ledger_is_refused():
    with pytest.raises(LedgerError) as caught:
        lost_run(destroyed=[ANCHOR])
    assert "wrote no ledger" in str(caught.value)


def test_a_destroyed_vehicle_that_did_not_say_what_it_held_is_refused():
    relay = {ident(RELAY, 1): 118.0}
    with pytest.raises(LedgerError) as caught:
        observations([router(RELAY, relay)], gcs({ident(RELAY, 1): 118.4}),
                     120.0, 152.0, destroyed=[RELAY])
    assert "still holding" in str(caught.value)


# --------------------------------------------------- the block covers the run
def test_traffic_minted_before_the_run_is_not_part_of_it():
    """The radio settles for eight seconds before scenario time zero.

    Those observations are real and the run record's delivery ratio counts
    them. They are not part of the outage arithmetic, and the schema puts a
    minimum of zero on every creation time in this block, which is how the
    first complete relay_kill was refused.
    """
    minted = {ident(NEAR, 1): -8.1, ident(NEAR, 2): 61.0}
    got = observations([router(NEAR, minted)],
                       gcs({ident(NEAR, 1): -7.9, ident(NEAR, 2): 106.0},
                           created={ident(NEAR, 1): -8.1,
                                    ident(NEAR, 2): 61.0}),
                       OUT_START, OUT_END)
    assert got["generated_ids"] == [ident(NEAR, 2)]
    assert got["delivered_ids"] == [ident(NEAR, 2)]
    assert got["generated_before_run"] == 1
    assert observations_set_equal(got) is True


def test_a_run_with_nothing_before_it_says_so():
    assert a_run()["generated_before_run"] == 0


def test_an_id_nobody_minted_is_still_unexpected():
    # The delivered side is scoped by the destination's own creation stamp
    # and not by membership of the generated set. Scoping it by membership
    # would make this check structurally impossible to fail.
    got = a_run(gcs_ledger=gcs({ident(NEAR, 1): 106.0, ident(NEAR, 2): 106.0,
                                ident(NEAR, 3): 106.0, ident(FAR, 1): 106.0,
                                ident(FAR, 2): 106.0, ident(FAR, 3): 106.0,
                                ident(ANCHOR, 9): 106.0}))
    assert got["unexpected_ids"] == [ident(ANCHOR, 9)]
    assert observations_set_equal(got) is False


def test_what_was_deferred_is_reported_beside_what_was_evicted():
    """A queue too small for a long outage is not a design that loses data.

    uav_2 goes quiet for two minutes and mints six hundred observations into
    a queue that holds five hundred and twelve. It holds a durable copy of
    every one of them, so the oldest being pushed out to make room costs the
    run nothing, and the record has to be able to say that.
    """
    routers = [router(NEAR, {ident(NEAR, 1): 61.0}, deferred=88, evicted=0)]
    got = observations(routers, gcs({ident(NEAR, 1): 106.0}),
                       OUT_START, OUT_END)
    assert got["deferred"] == 88
    assert got["evicted"] == 0


# ------------------------------------------------------------- the drains
def test_every_node_reports_its_drain_and_the_bound_names_its_own():
    """Both, because either alone loses something.

    The bound is about the queues this outage filled, so the gated vehicle's
    own store emptying two minutes later cannot be folded into it. It is
    still a fact about the run, and a record that dropped it would be hiding
    a number rather than scoping one.
    """
    near = {ident(NEAR, 1): 61.0}
    far = {ident(FAR, 1): 61.1}
    routers = [
        router(NEAR, near, custodied=sorted(list(near) + list(far)),
               drain_end_at=106.4),
        router(FAR, far, drain_end_at=106.2),
        router(RELAY, {ident(RELAY, 1): 61.2}, drain_end_at=248.0),
    ]
    delivered = {i: 106.5 for i in list(near) + list(far)}
    delivered[ident(RELAY, 1)] = 248.5
    got = observations(routers, gcs(delivered), OUT_START, OUT_END,
                       drain_start_s=106.0, drain_end_s=106.4,
                       drain_by_node={NEAR: 106.4, FAR: 106.2})
    assert got["drain_counted_for"] == sorted([NEAR, FAR])
    assert got["drain_by_node"][RELAY] == pytest.approx(248.0)
    assert got["backlog_drain_s"] == pytest.approx(0.4)

