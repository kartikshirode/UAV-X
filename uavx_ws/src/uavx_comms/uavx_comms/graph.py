"""The link-state database, the route key, and the shortest path over both.

Link-state routing, chosen because it fits in a paragraph of the proposal and
is exactly correct for five nodes. Each node floods its neighbour table, every
node builds the same graph, and Dijkstra runs to the ground station.

Two rules here are load bearing and neither is obvious.

Symmetry is what removes a dead node from the graph. An edge counts only if
both endpoints list each other, so when a vehicle dies its neighbours stop
hearing its HELLO, drop it after neighbour_timeout, flood that, and every edge
to the dead node fails the symmetry test even though its own last link-state
advertisement still claims them. No separate age-out rule is needed and none is
invented here, which matters because an invented staleness timer would be a
frozen number nobody derived.

The route key is a pair, not a scalar. Round 5 finding 1 found that hop count
alone makes the link_loss handback impossible: the recovered route and the
relay route both cost three hops, a tie never wins hysteresis, so the installed
route through the relay is never replaced and the release can never fire. The
first fix was hops plus a surcharge of 0.5 per temporary relay, and round 6
finding 9 is right that it only behaves while a path holds at most one relay.
Two relays on a three hop path come to 4.0, which ties a four hop path holding
none, and at three relays the shorter path loses outright.

    key(path) = (hops, temporary relays on the path)

compared left to right. Fewer hops always wins. Relay count only ever decides a
tie. There is no weight to pick and nothing to be wrong at a larger relay
count. What it expresses is real: a route as short as another but which ties up
a surveyor is worse, and hop count has no way to say so.
"""

import heapq
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from . import params


@dataclass(frozen=True)
class Lsa:
    """One link-state advertisement, mirroring uavx_msgs/msg/LinkState."""

    originator_id: str
    lsa_seq: int
    neighbours: FrozenSet[str]
    sent_at: float
    ttl: int = params.LSA_TTL


class LinkStateDatabase:
    """Newest advertisement per originator. Higher sequence wins, duplicates drop."""

    def __init__(self) -> None:
        self.records: Dict[str, Lsa] = {}
        self.accepted = 0
        self.duplicates = 0

    def accept(self, lsa: Lsa) -> bool:
        """True if this advertisement is news. False if it is a duplicate or older.

        A stale advertisement is dropped rather than merged, which is what
        makes flooding terminate: a node that already holds this sequence does
        not re-flood it.
        """
        if lsa.lsa_seq > params.UINT32_MAX or lsa.lsa_seq < 0:
            raise ValueError("lsa_seq is a uint32 and this one is outside it: "
                             "{0}".format(lsa.lsa_seq))
        held = self.records.get(lsa.originator_id)
        if held is not None and lsa.lsa_seq <= held.lsa_seq:
            self.duplicates += 1
            return False
        self.records[lsa.originator_id] = lsa
        self.accepted += 1
        return True

    def topology(self,
                 override: Optional[Dict[str, FrozenSet[str]]] = None
                 ) -> Dict[str, Set[str]]:
        """The graph, with the symmetry rule applied.

        `override` replaces one originator's claimed neighbours, and a node
        always passes its own live neighbour table there. Reading its own row
        out of the database instead would make the node's view of itself lag by
        up to one LSA period, which is long enough for a route to be computed
        over a link the node has already given up on.

        An edge exists only where both sides advertise each other. A node that
        has gone silent still has its own record here claiming its old
        neighbours, and every one of those edges fails this test because the
        neighbours have already dropped it. That is the whole liveness
        mechanism, and it is the one architecture.md section 3 freezes.
        """
        claims = {node: set(rec.neighbours) for node, rec in self.records.items()}
        for node, listed in (override or {}).items():
            claims[node] = set(listed)
        graph: Dict[str, Set[str]] = {node: set() for node in claims}
        for node, listed in claims.items():
            for other in listed:
                if other in claims and node in claims[other]:
                    graph[node].add(other)
                    graph.setdefault(other, set()).add(node)
        return graph


def route_key(path: Sequence[str], relays: FrozenSet[str]) -> Tuple[int, int]:
    """(hops, temporary relays on the path). Lower wins, compared left to right."""
    return (len(path) - 1, len(set(path) & set(relays)))


def dijkstra(topology: Dict[str, Set[str]], src: str, dst: str,
             relays: FrozenSet[str] = frozenset()) -> Optional[List[str]]:
    """Cheapest path on the route key, with a total order so it cannot flap.

    Costs are non-negative and the lexicographic order is preserved by adding
    the same vector to both sides, so Dijkstra is correct over the pair. The
    third element of the heap entry is the path itself, which breaks a
    remaining tie by node id order. Without it two equally good paths would be
    chosen by whichever the heap happened to surface, and route hysteresis
    would then be comparing this computation's coin flip with the last one's.
    """
    if src not in topology or dst not in topology:
        return None
    if src == dst:
        return [src]
    relays = frozenset(relays)
    start = (0, 1 if src in relays else 0, [src])
    heap = [start]
    settled: Set[str] = set()
    while heap:
        hops, on_relays, path = heapq.heappop(heap)
        node = path[-1]
        if node in settled:
            continue
        settled.add(node)
        if node == dst:
            return path
        for nb in sorted(topology.get(node, ())):
            if nb in path:
                continue
            heapq.heappush(heap,
                           (hops + 1,
                            on_relays + (1 if nb in relays else 0),
                            path + [nb]))
    return None


def component_of(topology: Dict[str, Set[str]], node: str) -> Set[str]:
    """Every node reachable from this one, including itself."""
    if node not in topology:
        return {node}
    seen = {node}
    stack = [node]
    while stack:
        current = stack.pop()
        for nb in topology.get(current, ()):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return seen


def reachability(topology: Dict[str, Set[str]], src: str,
                 known: Sequence[str]) -> Dict[str, bool]:
    """Which of the nodes we know about this one can currently reach.

    A partitioned vehicle has to be reported unreachable rather than silently
    dropped, so the answer names every node the swarm has ever heard of and
    says yes or no about each. A node missing from the map is a node nobody has
    to explain.
    """
    live = component_of(topology, src)
    return {node: (node in live) for node in sorted(set(known) | {src})}
