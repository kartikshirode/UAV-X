"""Neighbours, route hysteresis, and the bounded store-and-forward queue.

Three state machines that a node owns and that nothing outside it can see.

The neighbour table is HELLO driven. A neighbour is live while a HELLO from it
has arrived inside neighbour_timeout, which is three missed periods. Positions
travel in HELLO because the relay slot and the separation predictor both need
them, and both are therefore late by up to one HELLO period plus link latency.
That is stated rather than hidden: safety degrades when the link degrades.

Route hysteresis has three cases and only one of them waits. A new route
replaces an installed one only after winning two consecutive computations, so a
link sitting near a band edge cannot make the route oscillate. Installing when
nothing is installed does not wait, because there is nothing to replace and
holding packets while a route exists is strictly worse. Withdrawing an
installed route whose edges have gone does not wait either, because the
alternative is forwarding into a hole for two more computations.

A computation is one tick of the LSA period and not one tick of the simulation.
Tying it to a fixed clock is what stops a fast-flapping link from winning
hysteresis twice inside a second: two wins means two LSA periods of agreement,
whatever the link does in between.

The queue is 512 packets and it is sized from the gate rather than from the
expected result. Round 4 finding 3 caught the previous 50: at 5 Hz that holds
10 seconds against an outage the gate allows to reach 45, so a surveying node
was required by the design to drop about three quarters of its observations
while the same design claimed none were silently dropped. No implementation
could satisfy both. Every drop here is counted, and the counters are the point:
an eviction that is not reported is the failure this package exists to prevent.
"""

from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from . import graph, params
from .packet import Packet

# What a route computation did. The caller acts on these rather than diffing
# the table itself, so a node cannot accidentally treat "still thinking about
# it" as "installed".
UNCHANGED = "unchanged"
INSTALLED = "installed"
REPLACED = "replaced"
WITHDRAWN = "withdrawn"
CHALLENGED = "challenged"
NO_ROUTE = "no_route"


@dataclass
class Neighbour:
    node_id: str
    last_hello_at: float
    position: Tuple[float, float, float]
    hello_seq: int


class NeighbourTable:
    """Who this node can currently hear, and where they said they were."""

    def __init__(self) -> None:
        self.entries: Dict[str, Neighbour] = {}
        self.changes = 0

    def hello(self, sender_id: str, position: Sequence[float], at: float,
              hello_seq: int) -> bool:
        """Record a HELLO. True if this is a neighbour we did not have."""
        fresh = sender_id not in self.entries
        self.entries[sender_id] = Neighbour(sender_id, at, tuple(position),
                                            hello_seq)
        if fresh:
            self.changes += 1
        return fresh

    def expire(self, now: float) -> Set[str]:
        """Drop neighbours that have missed three HELLOs. Returns what went."""
        gone = {n for n, e in self.entries.items()
                if now - e.last_hello_at > params.NEIGHBOUR_TIMEOUT_S}
        for node in gone:
            del self.entries[node]
        if gone:
            self.changes += 1
        return gone

    def live(self) -> FrozenSet[str]:
        return frozenset(self.entries)

    def position_of(self, node_id: str) -> Optional[Tuple[float, float, float]]:
        entry = self.entries.get(node_id)
        return None if entry is None else entry.position


@dataclass
class RouteTable:
    """The installed route to one destination, and the challenger for it."""

    destination: str
    installed: Optional[List[str]] = None
    challenger: Optional[List[str]] = None
    challenger_wins: int = 0
    computations: int = 0
    installs: int = 0
    withdrawals: int = 0
    last_event: str = NO_ROUTE

    def next_hop(self) -> Optional[str]:
        if not self.installed or len(self.installed) < 2:
            return None
        return self.installed[1]

    def has_route(self) -> bool:
        return self.installed is not None

    def _still_valid(self, topology: Dict[str, Set[str]]) -> bool:
        if self.installed is None:
            return False
        for a, b in zip(self.installed, self.installed[1:]):
            if b not in topology.get(a, ()):
                return False
        return True

    def withdraw_if_broken(self, topology: Dict[str, Set[str]]) -> bool:
        """Immediate. Hysteresis never delays letting go of a route that is gone."""
        if self.installed is not None and not self._still_valid(topology):
            self.installed = None
            self.challenger = None
            self.challenger_wins = 0
            self.withdrawals += 1
            self.last_event = WITHDRAWN
            return True
        return False

    def compute(self, topology: Dict[str, Set[str]], src: str,
                relays: FrozenSet[str] = frozenset()) -> str:
        """One route computation. Call once per LSA period, never once per tick."""
        self.computations += 1
        best = graph.dijkstra(topology, src, self.destination, relays)

        if best is None:
            if self.installed is not None:
                self.installed = None
                self.withdrawals += 1
                self.last_event = WITHDRAWN
            else:
                self.last_event = NO_ROUTE
            self.challenger = None
            self.challenger_wins = 0
            return self.last_event

        if self.installed is None:
            self.installed = list(best)
            self.installs += 1
            self.challenger = None
            self.challenger_wins = 0
            self.last_event = INSTALLED
            return self.last_event

        if best == self.installed:
            self.challenger = None
            self.challenger_wins = 0
            self.last_event = UNCHANGED
            return self.last_event

        if self.challenger == best:
            self.challenger_wins += 1
        else:
            self.challenger = list(best)
            self.challenger_wins = 1

        if self.challenger_wins >= params.ROUTE_HYSTERESIS_WINS:
            self.installed = list(best)
            self.installs += 1
            self.challenger = None
            self.challenger_wins = 0
            self.last_event = REPLACED
        else:
            self.last_event = CHALLENGED
        return self.last_event


class PacketQueue:
    """Bounded store and forward, oldest dropped first, every loss counted.

    Keyed by packet identity so the custodian rule gets deduplication for
    free: a copy of an observation this node already holds is not a second
    packet, and counting it as one would make the depth the design is sized
    for unreachable for the wrong reason.
    """

    def __init__(self, capacity: int = params.QUEUE_CAPACITY) -> None:
        self.capacity = capacity
        self._items = OrderedDict()
        self.evicted = 0
        self.expired = 0
        self.duplicates = 0
        self.peak = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: Tuple[str, int]) -> bool:
        return key in self._items

    def identities(self) -> List[Tuple[str, int]]:
        return list(self._items)

    def push(self, packet: Packet) -> str:
        """Queue one packet. Returns what happened, which is never nothing."""
        key = packet.identity()
        if key in self._items:
            self.duplicates += 1
            return "duplicate"
        outcome = "queued"
        if len(self._items) >= self.capacity:
            self._items.popitem(last=False)
            self.evicted += 1
            outcome = "evicted_oldest"
        self._items[key] = packet
        self.peak = max(self.peak, len(self._items))
        return outcome

    def pop(self) -> Optional[Packet]:
        if not self._items:
            return None
        _, packet = self._items.popitem(last=False)
        return packet

    def drop(self, key: Tuple[str, int]) -> bool:
        return self._items.pop(key, None) is not None

    def expire(self, now: float) -> int:
        """Drop observations past their lifetime. Counted, never silent."""
        dead = [k for k, p in self._items.items() if now > p.expires_at]
        for key in dead:
            del self._items[key]
        self.expired += len(dead)
        return len(dead)


class ServiceRate:
    """The forward rate, as an accumulator rather than a per-tick integer.

    200 packets per second at a 50 ms tick is 10 per tick, which divides
    exactly. It will not stay that way, and truncating each tick would quietly
    change the rate the drain bound is derived from.
    """

    def __init__(self, rate_pps: float = params.FORWARD_RATE_PPS) -> None:
        self.rate_pps = rate_pps
        self._credit = 0.0

    def allowance(self, dt: float) -> int:
        self._credit += self.rate_pps * dt
        whole = int(self._credit)
        self._credit -= whole
        return whole
