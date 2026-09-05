"""Chunk 4.1: what a vehicle accepts, and everything it has to refuse.

A role manager reads the whole role conversation, because role messages are
flooded and most of them are about somebody else. Four ways of getting that
wrong all leave a vehicle flying somewhere it should not be, and none of them
looks like an error at the time:

  accepting an ASSIGN naming another node, which sends two aircraft to one
  slot;

  accepting an older epoch after a newer one has opened, which undoes an
  election that already happened;

  accepting a replayed grant for an epoch already given back, which sends a
  vehicle out again after it came home;

  and honouring a renewal that shortens a lease, which pulls a vehicle out of
  a role it is currently holding.

The last group is the lease, which is the whole recovery from a dead epoch
owner: nothing detects the death, the renewals simply stop.

    python3 -m pytest -q uavx_ws/src/uavx_roles/test/test_grant.py

Runs on a clean checkout with nothing built.
"""

import pytest

from uavx_comms import election as el
from uavx_roles.grant import (ACCEPTED, MALFORMED, NOT_MINE, RELEASED, RENEWED,
                              STALE_EPOCH, UNKNOWN_KIND, Grant, GrantError,
                              GrantTracker)

# Built per index, never written out. scripts/check_seam.sh counts distinct
# vehicle endpoint literals per file and that rule covers tests.
VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

SLOT = [317.3, -36.8, 75.0]


def assign(winner, epoch=1, slot=None, owner=FAR):
    return {"kind": el.ASSIGN, "epoch": epoch, "winner": winner,
            "owner": owner, "slot": list(SLOT if slot is None else slot),
            "coordinator": NEAR, "attachment_id": ANCHOR,
            "members": [NEAR, FAR]}


def lease(node, epoch=1, expires=None):
    body = {"kind": el.LEASE, "epoch": epoch, "sender_id": FAR,
            "node_id": node}
    if expires is not None:
        body["lease_expires_at"] = expires
    return body


def release(epoch=1):
    return {"kind": el.RELEASE, "epoch": epoch, "sender_id": FAR}


def tracker(node=NEAR):
    return GrantTracker(node)


# ------------------------------------------------------------ the assignment
def test_a_grant_naming_this_vehicle_is_accepted():
    have = tracker()
    assert have.apply(assign(NEAR), 0.0) == ACCEPTED
    assert have.grant is not None
    assert have.grant.is_relay
    assert have.grant.slot == tuple(SLOT)
    assert have.role == el.ROLE_RELAY


def test_a_grant_naming_somebody_else_is_not_this_vehicles():
    have = tracker()
    assert have.apply(assign(FAR), 0.0) == NOT_MINE
    assert have.grant is None
    assert have.role == el.ROLE_SURVEY


def test_the_epoch_still_counts_when_the_grant_is_somebody_elses():
    # A node that has seen epoch 3 open must not later accept a grant from
    # epoch 2, whoever epoch 3 was about.
    have = tracker()
    have.apply(assign(FAR, epoch=3), 0.0)
    assert have.epoch == 3
    assert have.apply(assign(NEAR, epoch=2), 1.0) == STALE_EPOCH
    assert have.grant is None


def test_a_later_epoch_naming_somebody_else_takes_the_role_away():
    have = tracker()
    have.apply(assign(NEAR, epoch=1), 0.0)
    assert have.grant is not None
    assert have.apply(assign(FAR, epoch=2), 1.0) == NOT_MINE
    assert have.grant is None


def test_a_repeated_grant_for_the_same_epoch_is_idempotent():
    have = tracker()
    assert have.apply(assign(NEAR), 0.0) == ACCEPTED
    assert have.apply(assign(NEAR), 0.5) == ACCEPTED
    assert have.grant.epoch == 1
    assert have.counts[ACCEPTED] == 2


def test_a_grant_replayed_after_the_release_is_refused():
    # Role messages are flooded, so a late copy of the assignment can arrive
    # after the epoch has finished. Accepting it sends a vehicle that already
    # came home back out to the slot.
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    assert have.apply(release(), 10.0) == RELEASED
    assert have.apply(assign(NEAR), 11.0) == STALE_EPOCH
    assert have.grant is None


def test_a_grant_with_no_lease_time_gets_the_frozen_one():
    have = tracker()
    body = assign(NEAR)
    have.apply(body, 100.0)
    assert have.grant.lease_expires_at == pytest.approx(
        100.0 + el.params.ROLE_LEASE_S)


def test_a_grant_with_a_slot_that_is_not_three_numbers_is_refused():
    have = tracker()
    assert have.apply(assign(NEAR, slot=[1.0, 2.0]), 0.0) == MALFORMED
    assert have.grant is None


def test_a_grant_with_a_non_finite_slot_is_refused():
    have = tracker()
    bad = [1.0, 2.0, float("inf")]
    assert have.apply(assign(NEAR, slot=bad), 0.0) == MALFORMED


# ------------------------------------------------------------------ the lease
def test_a_renewal_extends_the_lease():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    first = have.grant.lease_expires_at
    assert have.apply(lease(NEAR, expires=first + 30.0), 10.0) == RENEWED
    assert have.grant.lease_expires_at == pytest.approx(first + 30.0)


def test_a_renewal_for_another_epoch_is_stale():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    assert have.apply(lease(NEAR, epoch=9, expires=999.0), 1.0) == STALE_EPOCH


def test_a_renewal_naming_another_node_is_not_this_vehicles():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    assert have.apply(lease(FAR, expires=999.0), 1.0) == NOT_MINE


def test_a_renewal_that_shortens_the_lease_is_refused():
    # A late duplicate of an older renewal must not pull a vehicle out of a
    # role it is currently holding.
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    long = have.grant.lease_expires_at
    have.apply(lease(NEAR, expires=long + 60.0), 1.0)
    assert have.apply(lease(NEAR, expires=long), 2.0) == STALE_EPOCH
    assert have.grant.lease_expires_at == pytest.approx(long + 60.0)


def test_a_renewal_with_no_grant_is_stale():
    have = tracker()
    assert have.apply(lease(NEAR, expires=99.0), 1.0) == STALE_EPOCH


def test_an_expired_lease_gives_the_role_back_on_its_own():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    expires = have.grant.lease_expires_at
    assert have.expire(expires - 0.1) is False
    assert have.grant is not None
    assert have.expire(expires + 0.1) is True
    assert have.grant is None
    assert have.role == el.ROLE_SURVEY


def test_expiring_twice_reports_once():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    have.expire(1e9)
    assert have.expire(1e9) is False


# ---------------------------------------------------------------- the release
def test_a_release_for_the_held_epoch_gives_the_role_back():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    assert have.apply(release(), 5.0) == RELEASED
    assert have.grant is None
    assert have.role == el.ROLE_SURVEY


def test_a_release_for_another_epoch_is_stale():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    assert have.apply(release(epoch=7), 5.0) == STALE_EPOCH
    assert have.grant is not None


def test_a_release_with_no_grant_is_stale():
    assert tracker().apply(release(), 5.0) == STALE_EPOCH


# ------------------------------------------------------- the rest of the flood
@pytest.mark.parametrize("kind", [el.ELECTION, el.BID, el.PREPARE_RELEASE,
                                  el.ROLE_ACK])
def test_the_conversation_this_vehicle_is_not_part_of(kind):
    have = tracker()
    assert have.apply({"kind": kind, "epoch": 4}, 0.0) == NOT_MINE
    assert have.epoch == 4
    assert have.grant is None


def test_a_message_of_an_unknown_kind_is_counted():
    assert tracker().apply({"kind": "PROMOTE", "epoch": 1}, 0.0) == UNKNOWN_KIND


@pytest.mark.parametrize("payload", [None, "ASSIGN", 7, []])
def test_a_payload_that_is_not_a_mapping_is_malformed(payload):
    assert tracker().apply(payload, 0.0) == MALFORMED


def test_a_message_with_no_epoch_is_malformed():
    assert tracker().apply({"kind": el.ASSIGN, "winner": NEAR}, 0.0) == MALFORMED


def test_a_message_with_an_unreadable_epoch_is_malformed():
    body = assign(NEAR)
    body["epoch"] = "first"
    assert tracker().apply(body, 0.0) == MALFORMED


# ---------------------------------------------------------------- the record
def test_a_tracker_needs_to_know_which_vehicle_it_is():
    with pytest.raises(GrantError):
        GrantTracker("")


def test_the_record_carries_the_grant_and_what_was_ignored():
    have = tracker()
    have.apply(assign(NEAR), 0.0)
    have.apply(assign(FAR, epoch=1), 0.0)
    row = have.as_record()
    assert row["node"] == NEAR
    assert row["grant"]["role"] == "RELAY"
    assert row["grant"]["slot"] == SLOT
    assert row["message_outcomes"][ACCEPTED] == 1
    assert row["message_outcomes"][NOT_MINE] == 1


def test_a_grant_reports_whether_it_is_still_alive():
    held = Grant(epoch=1, node_id=NEAR, role=el.ROLE_RELAY, slot=tuple(SLOT),
                 lease_expires_at=30.0, sender_id=FAR)
    assert held.live_at(29.9) is True
    assert held.live_at(30.0) is True
    assert held.live_at(30.1) is False
    assert held.renewed_to(60.0).live_at(45.0) is True
