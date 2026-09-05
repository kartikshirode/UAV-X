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


def gcs(delivered, duplicated=0, **extra):
    row = {
        "node": "gcs",
        "duplicated": duplicated,
        "control_queue_max_delay_s": 0.0,
        "ledger": [{"id": i, "created_at": 0.0, "delivered_at": at}
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
