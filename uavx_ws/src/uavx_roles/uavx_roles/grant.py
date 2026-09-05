"""Chunk 4.1: what one vehicle makes of the role messages addressed to it.

The election itself lives in `uavx_comms`. `RoleMachine` opens epochs, ranks
bids, picks a winner, fixes the epoch owner and runs the make before break
handback, and every router already carries one. None of that is repeated here
and repeating it would be the defect this project keeps finding: two answers
to a question the run record then reports one of.

What is here is the other half, and it did not exist. A grant is a message.
Flying somewhere because of one is a decision about an aircraft, and it
belongs to the vehicle rather than to the process that routes packets:

    Grant         one role grant, with the lease that keeps it alive
    GrantTracker  the grants addressed to this vehicle, newest epoch wins

The tracker is deliberately narrow. It never decides who should move, never
computes a slot and never opens anything. It reads what it was told, refuses
what is stale or addressed elsewhere, and answers one question: what is this
vehicle supposed to be right now.

The lease is the whole safety story. If the node that granted the role stops
renewing, for any reason including its own death, the grant lapses on its own
and the vehicle goes back to what it was doing. Nothing has to notice the
owner died, which is what makes a dead owner recoverable and a stale release
not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

# The role numbers and message kinds are frozen in uavx_comms and imported
# rather than restated. A second spelling of ROLE_RELAY here would be a second
# answer to what the relay is.
from uavx_comms import election as el

Point = Tuple[float, float, float]

# What apply() reports. Every message gets exactly one of these, so a caller
# can count what it ignored rather than discovering later that it ignored
# everything.
ACCEPTED = "accepted"
NOT_MINE = "not_mine"
STALE_EPOCH = "stale_epoch"
RELEASED = "released"
RENEWED = "renewed"
UNKNOWN_KIND = "unknown_kind"
MALFORMED = "malformed"

OUTCOMES = (ACCEPTED, NOT_MINE, STALE_EPOCH, RELEASED, RENEWED, UNKNOWN_KIND,
            MALFORMED)


class GrantError(ValueError):
    """A grant that cannot be read, or one built from nonsense."""


def _finite(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _point(raw) -> Optional[Point]:
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise GrantError(f"a slot is three numbers, got {raw!r}")
    if not all(_finite(v) for v in raw):
        raise GrantError(f"a slot is three finite numbers, got {raw!r}")
    return (float(raw[0]), float(raw[1]), float(raw[2]))


@dataclass(frozen=True)
class Grant:
    """One role, granted for one epoch, alive until its lease runs out."""

    epoch: int
    node_id: str
    role: int
    slot: Optional[Point]
    lease_expires_at: float
    sender_id: str

    @property
    def is_relay(self) -> bool:
        return self.role == el.ROLE_RELAY

    def live_at(self, now: float) -> bool:
        return now <= self.lease_expires_at

    def renewed_to(self, lease_expires_at: float) -> "Grant":
        return Grant(epoch=self.epoch, node_id=self.node_id, role=self.role,
                     slot=self.slot, lease_expires_at=float(lease_expires_at),
                     sender_id=self.sender_id)

    def as_record(self) -> dict:
        return {
            "epoch": self.epoch,
            "node_id": self.node_id,
            "role": el.ROLE_NAMES.get(self.role, str(self.role)),
            "slot": list(self.slot) if self.slot is not None else None,
            "lease_expires_at": round(self.lease_expires_at, 3),
            "sender_id": self.sender_id,
        }


class GrantTracker:
    """The grants addressed to one vehicle, in epoch order.

    Every router in the swarm floods role messages, so a vehicle sees the
    whole conversation and most of it is about somebody else. The tracker
    keeps the epoch number from all of it, because an ASSIGN naming another
    node still tells this one that an epoch has opened, and keeps the grant
    from only the messages that name it.
    """

    def __init__(self, node_id: str, home_role: int = el.ROLE_SURVEY) -> None:
        if not node_id:
            raise GrantError(
                "a role manager needs to know which vehicle it is. Every "
                "grant it reads is addressed by node id, and one that does "
                "not know its own accepts either everything or nothing")
        self.node_id = node_id
        self.home_role = home_role
        self.epoch = 0
        self.grant: Optional[Grant] = None
        self.released_epochs: set = set()
        self.counts = {outcome: 0 for outcome in OUTCOMES}

    # ------------------------------------------------------------ the state
    @property
    def role(self) -> int:
        return self.grant.role if self.grant is not None else self.home_role

    @property
    def slot(self) -> Optional[Point]:
        return self.grant.slot if self.grant is not None else None

    def _count(self, outcome: str) -> str:
        self.counts[outcome] += 1
        return outcome

    # --------------------------------------------------------- the messages
    def apply(self, payload: Any, now: float) -> str:
        """One decoded role message. Returns what became of it."""
        if not isinstance(payload, Mapping):
            return self._count(MALFORMED)
        kind = payload.get("kind")
        if kind not in (el.ASSIGN, el.LEASE, el.RELEASE, el.ELECTION, el.BID,
                        el.PREPARE_RELEASE, el.ROLE_ACK):
            return self._count(UNKNOWN_KIND)
        try:
            number = int(payload["epoch"])
        except (KeyError, TypeError, ValueError):
            return self._count(MALFORMED)

        if kind == el.ASSIGN:
            return self._on_assign(payload, number, now)
        if kind == el.LEASE:
            return self._on_lease(payload, number, now)
        if kind == el.RELEASE:
            return self._on_release(number)

        # ELECTION, BID, PREPARE_RELEASE and the acks are the conversation
        # this vehicle is not part of. The epoch still counts: a node that
        # has seen epoch 3 open must not accept a grant from epoch 2.
        self.epoch = max(self.epoch, number)
        return self._count(NOT_MINE)

    def _on_assign(self, payload: Mapping, number: int, now: float) -> str:
        if number < self.epoch:
            return self._count(STALE_EPOCH)
        self.epoch = number
        if payload.get("winner") != self.node_id:
            # Somebody else is flying. If this vehicle held the role in an
            # older epoch it does not any more, because a new assignment
            # replaces the old one for the whole component.
            if self.grant is not None and self.grant.epoch < number:
                self.grant = None
            return self._count(NOT_MINE)
        if number in self.released_epochs:
            # A grant already given back. Re-accepting it on a replayed flood
            # would send the vehicle out again after it came home.
            return self._count(STALE_EPOCH)
        try:
            slot = _point(payload.get("slot"))
        except GrantError:
            return self._count(MALFORMED)
        lease = payload.get("lease_expires_at")
        if not _finite(lease):
            lease = now + el.params.ROLE_LEASE_S
        self.grant = Grant(epoch=number, node_id=self.node_id,
                           role=el.ROLE_RELAY, slot=slot,
                           lease_expires_at=float(lease),
                           sender_id=str(payload.get("owner")
                                         or payload.get("coordinator") or ""))
        return self._count(ACCEPTED)

    def _on_lease(self, payload: Mapping, number: int, now: float) -> str:
        if self.grant is None or number != self.grant.epoch:
            return self._count(STALE_EPOCH)
        if payload.get("node_id") not in (None, self.node_id):
            return self._count(NOT_MINE)
        lease = payload.get("lease_expires_at")
        if not _finite(lease):
            lease = now + el.params.ROLE_LEASE_S
        if float(lease) < self.grant.lease_expires_at:
            # A renewal that shortens a lease is not a renewal. Honouring it
            # would let a late duplicate of an old message pull a vehicle out
            # of a role it is currently holding.
            return self._count(STALE_EPOCH)
        self.grant = self.grant.renewed_to(float(lease))
        return self._count(RENEWED)

    def _on_release(self, number: int) -> str:
        if self.grant is None or number != self.grant.epoch:
            return self._count(STALE_EPOCH)
        self.released_epochs.add(number)
        self.grant = None
        return self._count(RELEASED)

    # ----------------------------------------------------------- the clock
    def expire(self, now: float) -> bool:
        """Drop a grant whose lease ran out. True when one was dropped.

        This is the whole answer to a dead epoch owner. Nothing detects the
        death, nothing takes over the epoch and no message is needed: the
        renewals stop, the lease runs out, and the vehicle goes back to what
        it was doing before. Losing a vehicle's time is recoverable. Tearing
        down a working link on a message from a node that no longer owns the
        decision is not.
        """
        if self.grant is None or self.grant.live_at(now):
            return False
        self.grant = None
        return True

    def as_record(self) -> dict:
        return {
            "node": self.node_id,
            "epoch_seen": self.epoch,
            "grant": self.grant.as_record() if self.grant else None,
            "released_epochs": sorted(self.released_epochs),
            "message_outcomes": dict(sorted(self.counts.items())),
        }
