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

    python3 -m pytest -q uavx_ws/src/uavx_comms/test/test_custody.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_comms import packet as pk
from uavx_comms import params
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
