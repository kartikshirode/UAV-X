"""Chunk 4.2: what a node held, when it minted, and what the destination took.

Three things the ledgers did not carry and every field of the observations
block needs.

The store is empty by the time a run ends, so a custody count taken at
shutdown reports nothing. The peak depth is a number and cannot tell one node
holding 450 from two nodes holding 225 each, which is the difference between
the custody rule working and the backlog splitting, and splitting is the
failure `queue_drain` exists to rule out.

The destination has built a row per accepted observation since chunk 3.1,
carrying the creation time, the delivery time, the path and the hop count.
Nothing ever read them. Every timing field the gate asks of an outage is
arithmetic over those rows.

The unacknowledged set at the bottom is the fourth, and it is the one that
decides what a destroyed vehicle costs. An observation still held only by the
aircraft that minted it goes down with the aircraft. One that had reached a
neighbour is retained by its origin until the destination acknowledges it, so
it comes back on the next retry over the repaired path.

    python3 -m pytest -q uavx_ws/src/uavx_comms/test/test_custody.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_comms import packet as pk
from uavx_comms import params, slots
from uavx_comms.router import Router
from uavx_comms.routing import PacketQueue

VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

HERE = (475.0, 75.0, 50.0)


def observation(origin, sequence, created_at=0.0):
    return pk.observation(origin, sequence, created_at)


# ------------------------------------------------------------- the queue
def test_the_queue_remembers_an_identity_after_the_packet_leaves():
    queue = PacketQueue()
    queue.push(observation(FAR, 1))
    queue.pop()
    assert len(queue) == 0
    assert queue.held_ids == {f"{FAR}:1"}


def test_a_duplicate_push_is_held_once():
    queue = PacketQueue()
    queue.push(observation(FAR, 1))
    queue.push(observation(FAR, 1))
    assert queue.held_ids == {f"{FAR}:1"}
    assert queue.duplicates == 1


def test_an_evicted_packet_was_still_held():
    # It was in the queue and then it was pushed out. Forgetting it would
    # report a smaller backlog than the run actually carried.
    queue = PacketQueue(capacity=2)
    for n in range(1, 4):
        queue.push(observation(FAR, n))
    assert queue.evicted == 1
    assert len(queue.held_ids) == 3


def test_a_requeued_packet_does_not_count_twice():
    queue = PacketQueue()
    packet = observation(FAR, 1)
    queue.push(packet)
    queue.pop()
    queue.push(packet)
    assert len(queue.held_ids) == 1


# ------------------------------------------------------------- the router
def router(node_id=NEAR, **kwargs):
    return Router(node_id=node_id, position=HERE, **kwargs)


def test_a_node_holds_the_observations_it_mints():
    # The custody rule is about what one node held, its own included: a
    # disconnected component funnels its whole backlog to one member.
    node = router()
    node.observe(10.0)
    node.observe(10.2)
    summary = node.observation_summary()
    assert summary["custodied"] == 2
    assert summary["custodied_ids"] == [f"{NEAR}:1", f"{NEAR}:2"]


def test_a_forwarder_holds_what_it_takes_for_somebody_else():
    node = router()
    node._on_observation(observation(FAR, 1, created_at=5.0), 5.0)
    summary = node.observation_summary()
    assert summary["custodied_ids"] == [f"{FAR}:1"]


def test_a_node_that_refuses_to_forward_holds_nothing_of_anybody_elses():
    node = router(forwarding=False)
    node._on_observation(observation(FAR, 1, created_at=5.0), 5.0)
    assert node.observation_summary()["custodied"] == 0


def test_generation_times_are_reported_per_identity():
    node = router()
    node.observe(12.5)
    node.observe(12.7)
    assert node.observation_summary()["generated_at"] == {
        f"{NEAR}:1": 12.5, f"{NEAR}:2": 12.7}


def test_the_destination_reports_a_row_per_accepted_observation():
    gcs = Router(node_id=params.GCS_ID, position=(0.0, 0.0, 0.0))
    incoming = observation(FAR, 1, created_at=5.0)
    incoming.hop_count = 2
    incoming.path = [FAR, RELAY, ANCHOR, params.GCS_ID]
    gcs._accept(incoming, 5.4)

    rows = gcs.observation_summary()["ledger"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == f"{FAR}:1"
    assert row["created_at"] == 5.0
    assert row["delivered_at"] == 5.4
    assert row["hop_count"] == 2
    assert row["path"][-1] == params.GCS_ID


def test_a_duplicate_arrival_adds_no_second_row():
    # A retry from the origin and a drain from the custodian are both correct
    # and both arrive. Counting arrivals would report the backlog delivered
    # twice; the rows are what delivered once is read from.
    gcs = Router(node_id=params.GCS_ID, position=(0.0, 0.0, 0.0))
    for at in (5.4, 9.1):
        gcs._accept(observation(FAR, 1, created_at=5.0), at)
    assert len(gcs.observation_summary()["ledger"]) == 1
    assert gcs.duplicated == 1


def test_a_node_that_is_not_the_destination_has_no_rows():
    node = router()
    node.observe(10.0)
    assert node.observation_summary()["ledger"] == []


def test_the_summary_still_carries_what_week_three_read():
    node = router()
    node.observe(10.0)
    summary = node.observation_summary()
    for key in ("generated", "generated_ids", "delivered_ids",
                "unique_delivered", "duplicated", "expired", "evicted",
                "peak_queue_depth", "control_queue_max_delay_s"):
        assert key in summary, key


def test_an_expired_observation_was_still_generated():
    # Expiry is a loss, not a retraction. A node that dropped its generated
    # id along with the packet would report a denominator that shrank to fit
    # what it managed to deliver.
    node = router()
    node.observe(10.0)
    node.store.expire(10.0 + params.OBSERVATION_LIFETIME_S + 1.0)
    summary = node.observation_summary()
    assert summary["generated"] == 1
    assert summary["expired"] == 1
    assert summary["custodied"] == 1


# ------------------------------------------------- what went down with it
def test_a_minted_observation_is_unacknowledged_until_it_is_acknowledged():
    node = router()
    node.observe(10.0)
    node.observe(10.2)
    summary = node.observation_summary()
    assert summary["unacknowledged_ids"] == [f"{NEAR}:1", f"{NEAR}:2"]
    assert summary["unacknowledged"] == 2


def test_an_acknowledged_observation_is_no_longer_at_risk():
    node = router()
    node.observe(10.0)
    node._on_ack(pk.control(
        params.GCS_ID, pk.KIND_ACK, 10.4,
        {"id": f"{NEAR}:1", "origin_id": NEAR, "sequence": 1,
         "path": [NEAR, ANCHOR, params.GCS_ID]},
        dest_id=NEAR, sequence=1), 10.4)
    assert node.observation_summary()["unacknowledged_ids"] == []


def test_what_a_node_carries_for_others_is_not_its_to_lose():
    # Held for somebody else, and that somebody else is still holding it too.
    # A vehicle destroyed with this in its store loses nothing permanently.
    node = router()
    node._on_observation(observation(FAR, 1, created_at=5.0), 5.0)
    summary = node.observation_summary()
    assert summary["custodied_ids"] == [f"{FAR}:1"]
    assert summary["unacknowledged_ids"] == []


# ------------------------------------------------------- the route coming back
def test_a_node_that_never_lost_its_route_has_no_recovery_to_report():
    node = router()
    assert node.observation_summary()["recovered_at"] is None


def test_the_summary_carries_the_recovery_the_router_recorded():
    node = router()
    node.recovered_at = 152.5
    assert node.observation_summary()["recovered_at"] == 152.5


# ---------------------------------------------------------------- the slot
def test_a_node_that_ran_no_election_commanded_no_slot():
    assert router().observation_summary()["relay_slot"] is None


def test_a_feasible_decision_is_the_records_relay_slot():
    decision = slots.solve((165.0, 0.0, 30.0), [(475.0, -75.0, 60.0)],
                           live=[(475.0, -75.0, 60.0)])
    assert decision.feasible()
    row = decision.as_record()
    assert len(row["commanded"]) == 3
    assert row["commanded"][2] == params.RELAY_BAND_M
    assert row["band_reserved"] is True
    assert row["clearance_m"] >= params.SLOT_CLEARANCE_M
    assert row["status"] == slots.RELAY_FEASIBLE


def test_the_router_reports_the_slot_it_last_decided():
    node = router()
    node.last_slot = slots.solve((165.0, 0.0, 30.0), [(475.0, -75.0, 60.0)],
                                 live=[(475.0, -75.0, 60.0)])
    row = node.observation_summary()["relay_slot"]
    assert row["commanded"] == [float(v) for v in node.last_slot.slot]


def test_a_decision_with_nowhere_to_park_is_not_a_slot():
    # RELAY_INFEASIBLE with no point at all. A record carrying it would have
    # to invent a commanded position, and the gate reads that position.
    decision = slots.SlotDecision(
        status=slots.RELAY_INFEASIBLE, slot=None, anchor_hop_m=None,
        work_hop_m=None, clearance_m=None, band_reserved=True,
        reason="no altitude clears every flying vehicle")
    with pytest.raises(ValueError):
        decision.as_record()


def test_a_clearance_measured_against_nothing_is_not_a_number():
    # min() over an empty set of live vehicles is infinity, which is not a
    # distance, is not valid JSON, and is not something the gate may read as
    # satisfying a 15 m rule.
    decision = slots.solve((165.0, 0.0, 30.0), [(475.0, -75.0, 60.0)])
    assert decision.clearance_m == float("inf")
    assert decision.as_record()["clearance_m"] is None
