"""How a relay role is assigned, reassigned, and settled without flapping.

stage-1/architecture.md section 4. "The swarm elects" is not a specification,
so this is the machine, and every clause of it is a rule that stops something
specific from going wrong.

Who elects. The disconnected component elects its own relay. Round 3 finding 1
broke the previous rule, which made the coordinator the lowest id with a route
to the ground station: in the frozen topology the only node left with a route
after the relay dies is the anchor, and the anchor is never eligible to move,
so nobody could have opened an election. It is also the more honest reading of
"reconfigures itself as drones fail": the part of the swarm that lost contact
is the part that has to solve it.

Who owns the handback. Round 6 finding 7. The coordinator rule is a property of
a disconnected component, and the moment the radio comes back there is no
component left to take the lowest id of. Ownership is therefore carried rather
than recomputed, and the owner is never the relay: in link_loss the lowest id
member is also the member the election sends away, so an owner recomputed from
itself would be evaluating routes that all begin at the relay and the release
condition could never be true.

Six properties keep this from oscillating, and each one is tested by driving
the machine rather than by reading it back:

1.  Epochs are monotonic. A node ignores any role message for an epoch below
    the highest it has seen, and never opens one it has already seen, so a
    duplicated or late message cannot restart a settled election.
2.  The assignment is write once per epoch. The first ASSIGN a node accepts
    fixes the winner, and a later one for the same epoch is ignored, so even a
    coordinator that changed its mind cannot move the role inside an epoch.
3.  The bid order is total. The key is (distance to the attachment node, node
    id), and two candidates at exactly equal distance still have different
    ids, so every member computes the same winner from the same bids with no
    arrival order and no random component anywhere in it.
4.  Bids are collected for a fixed window and decided once. A better bid that
    arrives after the decision is ignored rather than reopening it.
5.  The role ends by release from the epoch owner, or by lease expiry, and by
    nothing else. A missed renewal does not revert the role, it starts the
    lease running down.
6.  A release is accepted only from the current epoch's owner. If the owner
    dies, nothing takes over the open epoch: two nodes each believing they own
    it could send contradictory releases, and a stale release is the outage
    this whole machine exists to prevent. The lease simply stops being
    renewed, the relay reverts on its own, and any member still disconnected
    opens the next epoch under the ordinary rule.

And the handback is make before break. The old link is never torn down until
its replacement has carried real traffic and the ground station has named it in
an acknowledgement. Round 5 finding 1 is right that the existence of an
alternate route is not enough on its own: acting on mere existence breaks the
installed next hop before the alternate is selected, which is the second outage
the design forbids.
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from . import params

# Role values, from uavx_msgs/msg/RoleAssignment.
ROLE_SURVEY = 0
ROLE_RELAY = 1
ROLE_GCS_ANCHOR = 2

ROLE_NAMES = {
    ROLE_SURVEY: "SURVEY",
    ROLE_RELAY: "RELAY",
    ROLE_GCS_ANCHOR: "GCS_ANCHOR",
}

# The role protocol, carried inside a ROLE kind SwarmPacket. W3 serializes
# these into the payload; the names are the contract between the two halves.
ELECTION = "ELECTION"
BID = "BID"
ASSIGN = "ASSIGN"
ROLE_ACK = "ROLE_ACK"
LEASE = "LEASE"
PREPARE_RELEASE = "PREPARE_RELEASE"
RELEASE = "RELEASE"

# Why a message was refused. Silence is the wrong answer here: a swarm that
# ignores a contradictory release and says nothing about it has no way to show
# a judge that it refused rather than missed it.
STALE_EPOCH = "stale_epoch"
ALREADY_ASSIGNED = "already_assigned"
WINDOW_CLOSED = "window_closed"
NOT_OWNER = "not_owner"
NOT_ELIGIBLE = "not_eligible"
ACCEPTED = "accepted"


@dataclass
class Epoch:
    """One election, from the moment it opens to the moment the role goes back."""

    number: int
    coordinator: str
    attachment_id: str
    members: FrozenSet[str]
    slot: Optional[Tuple[float, float, float]]
    opened_at: float
    bids: Dict[str, float] = field(default_factory=dict)
    work: Dict[str, List[Tuple[float, float, float]]] = field(
        default_factory=dict)
    decided: bool = False
    winner: Optional[str] = None
    owner: Optional[str] = None
    assigned_at: Optional[float] = None
    acked_by_winner: bool = False
    lease_expires_at: Optional[float] = None
    prepared_path: Optional[List[str]] = None
    prepared_wins: int = 0
    prepared_at: Optional[float] = None
    confirmed_id: Optional[str] = None
    confirmed_at: Optional[float] = None
    released: bool = False
    released_at: Optional[float] = None
    release_sender: Optional[str] = None
    abandoned: bool = False

    def open(self) -> bool:
        return not (self.released or self.abandoned)


def rank_bids(bids: Dict[str, float]) -> List[Tuple[float, str]]:
    """The total order every member computes identically.

    Distance first, then node id. The id is not a fallback that only runs on an
    exact tie; it is the second element of a single key, so the order is total
    for every possible pair of bids and there is no branch anywhere that could
    take a different path on two different nodes.
    """
    return sorted((distance, node) for node, distance in bids.items())


def pick_winner(bids: Dict[str, float]) -> Optional[str]:
    ranked = rank_bids(bids)
    return ranked[0][1] if ranked else None


def epoch_owner(members: FrozenSet[str], winner: Optional[str]) -> Optional[str]:
    """The lowest id member of the component that is not the elected relay.

    Deterministic from the election result alone, so every member computes the
    same one, and it is fixed here rather than recomputed later: merging graph
    components does not move it.
    """
    candidates = sorted(m for m in members if m != winner)
    return candidates[0] if candidates else None


class RoleMachine:
    """One node's whole view of the role protocol.

    Every node runs this, including the ones that never win anything. That is
    deliberate: the coordinator, the mover and the owner are three different
    nodes in the frozen scenarios, and a machine that only the coordinator ran
    would have to be a different machine on each.
    """

    def __init__(self, node_id: str, role: int = ROLE_SURVEY) -> None:
        self.node_id = node_id
        self.role = role
        self.initial_role = role
        self.highest_epoch = 0
        self.epochs: Dict[int, Epoch] = {}
        self.current: Optional[Epoch] = None
        self.slot_target: Optional[Tuple[float, float, float]] = None
        self.work_points: List[Tuple[float, float, float]] = []
        self.role_changes = 0
        self.refusals: List[Tuple[str, str]] = []
        self.lease_expiries = 0

    # -- helpers ------------------------------------------------------------

    def eligible(self) -> bool:
        """Only SURVEY members bid. A GCS_ANCHOR is never eligible to move."""
        return self.role == ROLE_SURVEY

    def is_relay(self) -> bool:
        return self.role == ROLE_RELAY

    def _refuse(self, kind: str, why: str) -> str:
        self.refusals.append((kind, why))
        return why

    def _set_role(self, role: int) -> None:
        if role != self.role:
            self.role = role
            self.role_changes += 1

    def open_epoch_for(self, number: int) -> Optional[Epoch]:
        held = self.epochs.get(number)
        return held if held is not None and held.open() else None

    # -- opening an election ------------------------------------------------

    def open_election(self, now: float, members: FrozenSet[str],
                      attachment_id: str,
                      slot: Optional[Tuple[float, float, float]]) -> Optional[dict]:
        """Called by the coordinator when its component has lost the route.

        Returns the ELECTION message to broadcast, or None when there is
        nothing to open. Refusing to open is as important as opening: a second
        epoch while one is still live is how a swarm ends up with two relays
        and two owners.
        """
        if self.node_id != min(members):
            return None
        if self.current is not None and self.current.open():
            return None
        number = self.highest_epoch + 1
        epoch = Epoch(number=number, coordinator=self.node_id,
                      attachment_id=attachment_id, members=frozenset(members),
                      slot=slot, opened_at=now)
        self.epochs[number] = epoch
        self.current = epoch
        self.highest_epoch = number
        return {"kind": ELECTION, "epoch": number, "coordinator": self.node_id,
                "attachment_id": attachment_id, "members": sorted(members),
                "slot": slot}

    # -- handling one role message -----------------------------------------

    def on_message(self, message: dict, now: float,
                   distance_to_attachment: Optional[float] = None) -> Tuple[str, List[dict]]:
        """Apply one role message. Returns (outcome, messages to send)."""
        kind = message["kind"]
        number = int(message["epoch"])

        if number < self.highest_epoch:
            return self._refuse(kind, STALE_EPOCH), []
        if number > self.highest_epoch:
            self.highest_epoch = number

        handler = {
            ELECTION: self._on_election,
            BID: self._on_bid,
            ASSIGN: self._on_assign,
            ROLE_ACK: self._on_role_ack,
            LEASE: self._on_lease,
            PREPARE_RELEASE: self._on_prepare_release,
            RELEASE: self._on_release,
        }.get(kind)
        if handler is None:
            return self._refuse(kind, "unknown_role_message"), []
        # Compared by name, not by identity. `self._on_election` builds a new
        # bound method every time it is read, so an `is` test against one of
        # them is false even when it names the same function.
        if kind == ELECTION:
            return handler(message, now, distance_to_attachment)
        return handler(message, now)

    def _on_election(self, message: dict, now: float,
                     distance_to_attachment: Optional[float]) -> Tuple[str, List[dict]]:
        number = int(message["epoch"])
        epoch = self.epochs.get(number)
        if epoch is None:
            epoch = Epoch(number=number, coordinator=message["coordinator"],
                          attachment_id=message["attachment_id"],
                          members=frozenset(message["members"]),
                          slot=message["slot"], opened_at=now)
            self.epochs[number] = epoch
            if self.current is None or not self.current.open():
                self.current = epoch
        if epoch.decided:
            return ALREADY_ASSIGNED, []
        if not self.eligible():
            return NOT_ELIGIBLE, []
        if distance_to_attachment is None:
            return NOT_ELIGIBLE, []
        return ACCEPTED, [{"kind": BID, "epoch": number, "node_id": self.node_id,
                           "distance": float(distance_to_attachment),
                           "work": list(self.work_points)}]

    def _on_bid(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        number = int(message["epoch"])
        epoch = self.epochs.get(number)
        if epoch is None or epoch.coordinator != self.node_id:
            return NOT_OWNER, []
        if epoch.decided:
            # Round 3 finding 1's sibling: a late bid that reopened a settled
            # election would make the winner depend on message timing, which is
            # exactly the flap this window exists to prevent.
            return self._refuse(BID, WINDOW_CLOSED), []
        epoch.bids[message["node_id"]] = float(message["distance"])
        epoch.work[message["node_id"]] = [tuple(p)
                                          for p in message.get("work", ())]
        return ACCEPTED, []

    def _on_assign(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        number = int(message["epoch"])
        epoch = self.epochs.get(number)
        if epoch is None:
            epoch = Epoch(number=number, coordinator=message.get("coordinator", ""),
                          attachment_id=message.get("attachment_id", ""),
                          members=frozenset(message.get("members", ())),
                          slot=message.get("slot"), opened_at=now)
            self.epochs[number] = epoch
        if epoch.winner is not None:
            return self._refuse(ASSIGN, ALREADY_ASSIGNED), []
        epoch.decided = True
        epoch.winner = message["winner"]
        epoch.owner = message["owner"]
        epoch.slot = message.get("slot", epoch.slot)
        epoch.assigned_at = now
        epoch.lease_expires_at = now + params.ROLE_LEASE_S
        if self.current is None or self.current.number <= number:
            self.current = epoch
        if epoch.winner == self.node_id:
            self._set_role(ROLE_RELAY)
            self.slot_target = epoch.slot
            return ACCEPTED, [{"kind": ROLE_ACK, "epoch": number,
                               "node_id": self.node_id}]
        return ACCEPTED, []

    def _on_role_ack(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        epoch = self.epochs.get(int(message["epoch"]))
        if epoch is None:
            return STALE_EPOCH, []
        if message["node_id"] == epoch.winner:
            epoch.acked_by_winner = True
        return ACCEPTED, []

    def _on_lease(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        epoch = self.epochs.get(int(message["epoch"]))
        if epoch is None or not epoch.open():
            return STALE_EPOCH, []
        if message["sender_id"] != epoch.owner:
            return self._refuse(LEASE, NOT_OWNER), []
        epoch.lease_expires_at = now + params.ROLE_LEASE_S
        return ACCEPTED, []

    def _on_prepare_release(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        epoch = self.epochs.get(int(message["epoch"]))
        if epoch is None or not epoch.open():
            return STALE_EPOCH, []
        if message["sender_id"] != epoch.owner:
            return self._refuse(PREPARE_RELEASE, NOT_OWNER), []
        epoch.prepared_path = list(message["path"])
        epoch.prepared_at = now
        # The relay keeps forwarding. Nothing has been given up yet, and that
        # is the whole content of make before break.
        return ACCEPTED, []

    def _on_release(self, message: dict, now: float) -> Tuple[str, List[dict]]:
        epoch = self.epochs.get(int(message["epoch"]))
        if epoch is None or not epoch.open():
            return STALE_EPOCH, []
        if message["sender_id"] != epoch.owner:
            return self._refuse(RELEASE, NOT_OWNER), []
        epoch.released = True
        epoch.released_at = now
        epoch.release_sender = message["sender_id"]
        if epoch.winner == self.node_id:
            self._set_role(self.initial_role)
            self.slot_target = None
        return ACCEPTED, []

    # -- timers -------------------------------------------------------------

    def close_window(self, now: float) -> List[dict]:
        """Decide, once the election window has elapsed. Coordinator only."""
        epoch = self.current
        if epoch is None or not epoch.open():
            return []
        if epoch.coordinator != self.node_id or epoch.decided:
            return []
        if now - epoch.opened_at < params.ELECTION_WINDOW_S:
            return []
        winner = pick_winner(epoch.bids)
        if winner is None:
            # Nobody eligible bid. The component reports it rather than
            # electing somebody who never offered.
            epoch.decided = True
            epoch.abandoned = True
            return []
        owner = epoch_owner(epoch.members, winner)
        return [{"kind": ASSIGN, "epoch": epoch.number, "winner": winner,
                 "owner": owner, "slot": epoch.slot,
                 "coordinator": epoch.coordinator,
                 "attachment_id": epoch.attachment_id,
                 "members": sorted(epoch.members)}]

    def renew(self, now: float) -> List[dict]:
        """The owner renews the relay's lease each LSA period, and only the owner."""
        epoch = self.current
        if epoch is None or not epoch.open() or epoch.owner != self.node_id:
            return []
        if epoch.winner is None:
            return []
        return [{"kind": LEASE, "epoch": epoch.number, "sender_id": self.node_id,
                 "node_id": epoch.winner,
                 "lease_expires_at": now + params.ROLE_LEASE_S}]

    def check_lease(self, now: float) -> bool:
        """An expired lease reverts the node to its starting role, on its own.

        This is what stops a dead coordinator from stranding a vehicle, and it
        is the only path back to SURVEY that does not need a message. Losing a
        vehicle's time is recoverable; tearing down a working link on a message
        from a node that no longer owns the decision is not.
        """
        epoch = self.current
        if epoch is None or not epoch.open() or epoch.lease_expires_at is None:
            return False
        if now <= epoch.lease_expires_at:
            return False
        epoch.abandoned = True
        self.lease_expiries += 1
        if epoch.winner == self.node_id:
            self._set_role(self.initial_role)
            self.slot_target = None
        return True

    # -- the handback -------------------------------------------------------

    def offer_path(self, path: Optional[Sequence[str]], now: float) -> List[dict]:
        """The owner's per-computation view of the best relay-free route.

        The route must win two consecutive computations before PREPARE_RELEASE,
        the same hysteresis every other route change obeys. A path that appears
        once as the returning vehicle comes back into range is not a path to
        hand a vehicle back on.
        """
        epoch = self.current
        if epoch is None or not epoch.open() or epoch.owner != self.node_id:
            return []
        if epoch.winner is None or epoch.prepared_at is not None:
            return []
        if path is None or epoch.winner in path:
            epoch.prepared_wins = 0
            epoch.prepared_path = None
            return []
        candidate = list(path)
        if epoch.prepared_path == candidate:
            epoch.prepared_wins += 1
        else:
            epoch.prepared_path = candidate
            epoch.prepared_wins = 1
        if epoch.prepared_wins < params.ROUTE_HYSTERESIS_WINS:
            return []
        epoch.prepared_at = now
        return [{"kind": PREPARE_RELEASE, "epoch": epoch.number,
                 "sender_id": self.node_id, "staying_member": self.node_id,
                 "path": candidate}]

    def confirm(self, observation_id: str, arrival_path: Sequence[str],
                now: float) -> List[dict]:
        """The ground station named an id and the path it arrived on.

        Only on that acknowledgement does the owner send RELEASE. An
        acknowledgement naming a different path is not the confirmation this
        transaction is waiting for, so it is ignored rather than treated as
        good enough.
        """
        epoch = self.current
        if epoch is None or not epoch.open() or epoch.owner != self.node_id:
            return []
        if epoch.prepared_at is None or epoch.confirmed_at is not None:
            return []
        if list(arrival_path) != list(epoch.prepared_path or ()):
            return []
        epoch.confirmed_id = observation_id
        epoch.confirmed_at = now
        return [{"kind": RELEASE, "epoch": epoch.number,
                 "sender_id": self.node_id}]

    def abandon_stale_prepare(self, now: float) -> bool:
        """No acknowledgement inside the stability window means the relay stays.

        A swarm that keeps a vehicle parked is worse off than one that does
        not. A swarm that drops the link to recover a vehicle is much worse off
        than both.
        """
        epoch = self.current
        if epoch is None or not epoch.open():
            return False
        if epoch.prepared_at is None or epoch.confirmed_at is not None:
            return False
        if now - epoch.prepared_at <= params.STABILITY_WINDOW_S:
            return False
        epoch.prepared_at = None
        epoch.prepared_path = None
        epoch.prepared_wins = 0
        return True

    # -- what the run record wants ------------------------------------------

    def handback_trace(self) -> dict:
        """The named trace the gate asserts, straight out of the machine."""
        epoch = self.current
        if epoch is None:
            return {}
        return {
            "epoch": epoch.number,
            "epoch_owner": epoch.owner,
            "staying_member": epoch.owner,
            "prepared_path": list(epoch.prepared_path or ()),
            "prepared_path_computations": epoch.prepared_wins,
            "confirmed_observation_id": epoch.confirmed_id,
            "confirmed_at": epoch.confirmed_at,
            "release_sender": epoch.release_sender,
            "release_at": epoch.released_at,
            "relay_role_holder": epoch.winner,
        }
