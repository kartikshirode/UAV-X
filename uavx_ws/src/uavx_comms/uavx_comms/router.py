"""One node's whole state machine, with the tx/rx seam expressed in objects.

A Router holds no reference to another Router and no reference to the link
layer. The only ingress is `on_rx`, which is what arrives on this node's own rx
topic, and the only egress is `drain_tx`, which is what this node publishes on
its own tx topic. Everything else it knows it learned from a packet. W3 wires
those two methods to the two endpoints in the allowlist and adds nothing, and
`test_seam_shape.py` fails if this file ever grows a way round them.

Four decisions here are not transcription and W3 inherits every one of them.

The intended next hop rides in `path`. The link layer models a radio: a
transmission goes into the medium and every node in range hears it. There is no
next-hop field in SwarmPacket, so a transmitting node appends its chosen next
hop to `path` before transmitting, and a receiver acts on a unicast only when
it finds its own id at the end. Everyone else overhears and ignores. That makes
`path` genuinely "node ids in order, appended by each forwarder", it makes the
delivered path the evidence behind the handback trace, and it means exactly one
node forwards each packet.

`hop_count` counts forwarders, and `len(path) - 1` counts edges. The frozen
route key and scripts/check_geometry.py both count edges, so a delivered
observation from the far surveyor shows 3 there and 2 in `hop_count`. Both are
recorded, and neither is quietly used in place of the other.

A HELLO is relayed one hop further than the link it came over, and a relayed
HELLO never creates a neighbour. This is what lets a disconnected component
know where its attachment node is: nothing in the frozen geometry puts a
surveyor within radio range of the anchor, so without it nobody could compute a
bid distance or a slot. The symmetry rule is untouched, because only a HELLO
that arrived with hop_count zero admits a neighbour. Getting that wrong is
round 3 finding 1 all over again: treat a relayed HELLO as a neighbour and the
anchor becomes adjacent to both surveyors, the chain the relay was carrying
already exists, and killing the relay proves nothing.

The route to the ground station is the one under hysteresis. Everything else,
an acknowledgement going back to an origin or an observation making its way to
the backlog custodian, is routed on demand from the current graph, because a
control packet arriving by a slightly different path is not a flap and delaying
it would be.
"""

from collections import deque
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

from . import election, graph, packet as pk, params, routing, slots

# Why a received packet went nowhere. Counted rather than swallowed: a drop
# nobody can name is the failure this package exists to make impossible.
DROP_UNKNOWN_KIND = "unknown_kind"
DROP_TTL = "ttl_exceeded"
DROP_LOOP = "loop"
DROP_EXPIRED = "expired"

# What a component reported instead of electing.
NO_ATTACHMENT = "NO_ATTACHMENT"
RELAY_INFEASIBLE = slots.RELAY_INFEASIBLE


class Router:
    """The routing and role state machine for one node."""

    def __init__(self, node_id: str, position: Sequence[float],
                 role: int = election.ROLE_SURVEY,
                 destination: str = params.GCS_ID,
                 forwarding: bool = True,
                 elections_enabled: bool = True,
                 queue_capacity: int = params.QUEUE_CAPACITY) -> None:
        self.node_id = node_id
        self.position = tuple(position)
        self.destination = destination
        self.is_destination = node_id == destination

        # direct_only.yaml turns this off. A relay that "works" in a scenario
        # where the direct link would also have worked has demonstrated
        # nothing, so the control has to be a real switch in the real code.
        self.forwarding = forwarding
        self.elections_enabled = elections_enabled

        self.neighbours = routing.NeighbourTable()
        self.lsdb = graph.LinkStateDatabase()
        self.route = routing.RouteTable(destination)
        self.store = routing.PacketQueue(queue_capacity)
        self.control = deque()
        self.control_capacity = queue_capacity
        self.service = routing.ServiceRate()
        self.roles = election.RoleMachine(node_id, role)

        # Positions this node has been told about, by a direct or a relayed
        # HELLO. Stale by up to one HELLO period plus link latency, and after a
        # split as stale as the split is old, which is exactly why relay
        # separation is taken vertically rather than from these numbers.
        self.position_cache: Dict[str, Tuple[float, float, float]] = {
            node_id: tuple(position)}

        # What this node still has to do, as points. A station-keeping member
        # contributes its own position; a surveying member contributes the
        # corners of its assigned area at its cruise altitude.
        self.work_points: List[Tuple[float, float, float]] = [tuple(position)]
        self.roles.work_points = list(self.work_points)

        self._tx: List[pk.Packet] = []
        self._inflight_control_at: Dict[int, float] = {}
        self._hello_seq = 0
        self._lsa_seq = 0
        self._role_seq = 0
        self._ack_seq = 0
        self._obs_seq = 0
        # Highest flood sequence seen per (originator, kind). Higher wins and
        # a duplicate is not re-flooded, which is what makes flooding stop.
        # A growing set of every packet ever seen would work too and would
        # grow without bound across a 360 second run.
        self._flood_seen: Dict[Tuple[str, int], int] = {}

        self._next_hello_at = 0.0
        self._next_lsa_at = 0.0
        self._next_compute_at = 0.0
        self._last_topology_change = -1

        self._route_absent_since: Optional[float] = 0.0
        self._route_present_since: Optional[float] = None
        self.disconnected = False
        self.recovered_at: Optional[float] = None
        self.last_gcs_route: Optional[List[str]] = None
        # Set when a component has work but nowhere feasible to park a relay.
        # It suppresses further elections until the component changes, so the
        # swarm reports the condition instead of reopening an epoch forever.
        self.relay_infeasible = False
        self._infeasible_for: Optional[FrozenSet[str]] = None
        # Neighbours whose direct link went away since this node last held a
        # route home. These are the nodes the break happened at, and the
        # attachment rule has to step over them.
        self.lost_neighbours: Set[str] = set()

        # Retention. The origin keeps its copy until the ground station
        # acknowledges the identity, so losing the custodian does not lose the
        # observation. That guarantees duplicates at the destination, which is
        # why deduplication there is a set comparison and not a count.
        self.pending_ack: Dict[Tuple[str, int], pk.Packet] = {}
        self.generated_ids: List[str] = []

        # Destination bookkeeping. Only the node that is the destination ever
        # accepts, and it accepts an identity once.
        self.accepted: Dict[Tuple[str, int], float] = {}
        self.accepted_path: Dict[Tuple[str, int], List[str]] = {}
        self.accepted_hops: Dict[Tuple[str, int], int] = {}
        self.duplicated = 0
        self.ledger: List[dict] = []

        self.drops: Dict[str, int] = {}
        self.protocol_errors = 0
        self.control_max_delay_s = 0.0
        self.reports: List[Tuple[float, str]] = []
        self.last_slot: Optional[slots.SlotDecision] = None

    # -- what the node itself knows ----------------------------------------

    def set_position(self, position: Sequence[float]) -> None:
        """Own ground truth, from this node's own PX4 namespace and nowhere else."""
        self.position = tuple(position)
        self.position_cache[self.node_id] = self.position
        if len(self.work_points) == 1:
            self.work_points = [self.position]
            self.roles.work_points = list(self.work_points)

    def set_work(self, points: Sequence[Sequence[float]]) -> None:
        self.work_points = [tuple(p) for p in points]
        self.roles.work_points = list(self.work_points)

    def _drop(self, why: str) -> None:
        self.drops[why] = self.drops.get(why, 0) + 1

    def _report(self, now: float, what: str) -> None:
        self.reports.append((now, what))

    # -- the seam, ingress --------------------------------------------------

    def on_rx(self, incoming: pk.Packet, now: float) -> None:
        """One packet off this node's rx topic. The only way anything gets in."""
        if incoming.kind not in pk.KIND_NAMES:
            self.protocol_errors += 1
            self._drop(DROP_UNKNOWN_KIND)
            return
        if incoming.origin_id == self.node_id:
            return
        if incoming.is_broadcast():
            self._on_broadcast(incoming, now)
            return
        if not incoming.path or incoming.path[-1] != self.node_id:
            return                                  # overheard, not addressed
        if self.node_id in incoming.path[:-1]:
            self._drop(DROP_LOOP)
            self.protocol_errors += 1
            return
        if incoming.kind == pk.KIND_OBSERVATION:
            self._on_observation(incoming, now)
        elif incoming.kind == pk.KIND_ACK:
            self._on_ack(incoming, now)
        else:
            self.protocol_errors += 1
            self._drop(DROP_UNKNOWN_KIND)

    def _on_broadcast(self, incoming: pk.Packet, now: float) -> None:
        if incoming.kind == pk.KIND_HELLO:
            self._on_hello(incoming, now)
        elif incoming.kind == pk.KIND_LSA:
            self._on_lsa(incoming, now)
        elif incoming.kind == pk.KIND_ROLE:
            self._on_role(incoming, now)
        else:
            self.protocol_errors += 1
            self._drop(DROP_UNKNOWN_KIND)

    def _fresh_flood(self, incoming: pk.Packet) -> bool:
        key = (incoming.origin_id, incoming.kind)
        held = self._flood_seen.get(key)
        if held is not None and incoming.sequence <= held:
            return False
        self._flood_seen[key] = incoming.sequence
        return True

    def _on_hello(self, incoming: pk.Packet, now: float) -> None:
        body = incoming.payload
        sender = body["sender_id"]
        if incoming.hop_count == 0:
            # Direct. Only a direct HELLO admits a neighbour, which is what
            # keeps the symmetry rule meaning what architecture.md says, and it
            # is the difference between a swarm that has to relay and one whose
            # anchor turns out to be adjacent to every surveyor.
            self.position_cache[sender] = tuple(body["position"])
            self.neighbours.hello(sender, body["position"], now, body["seq"])
            self.lost_neighbours.discard(sender)
            self._relay_flood(incoming, now)
            return
        if not self._fresh_flood(incoming):
            return
        # Relayed. It teaches this node where a node it cannot hear is standing,
        # and nothing else. A disconnected component has no other way to learn
        # the position of the attachment node it is about to bid against.
        self.position_cache[sender] = tuple(body["position"])
        self._relay_flood(incoming, now)

    def _on_lsa(self, incoming: pk.Packet, now: float) -> None:
        body = incoming.payload
        lsa = graph.Lsa(originator_id=body["originator_id"],
                        lsa_seq=int(body["lsa_seq"]),
                        neighbours=frozenset(body["neighbours"]),
                        sent_at=float(body["sent_at"]),
                        ttl=int(body["ttl"]))
        if lsa.originator_id == self.node_id:
            return
        if self.lsdb.accept(lsa):
            self._last_topology_change = self.lsdb.accepted
            self._relay_flood(incoming, now)

    def _on_role(self, incoming: pk.Packet, now: float) -> None:
        if not self._fresh_flood(incoming):
            return
        message = incoming.payload
        position = message.get("attachment_position")
        if message.get("attachment_id") and position is not None:
            self.position_cache.setdefault(message["attachment_id"],
                                           tuple(position))
        distance = self._distance_to(message.get("attachment_id"))
        _, replies = self.roles.on_message(message, now, distance)
        for reply in replies:
            self._send_role(reply, now)
        self._relay_flood(incoming, now)

    def _relay_flood(self, incoming: pk.Packet, now: float) -> None:
        """Re-broadcast one hop further, while the ttl allows it."""
        if incoming.hop_count + 1 > params.LSA_TTL:
            self._drop(DROP_TTL)
            return
        out = incoming.copy(hop_count=incoming.hop_count + 1)
        out.path = list(incoming.path) + [self.node_id]
        self._queue_control(out, now)

    def _on_observation(self, incoming: pk.Packet, now: float) -> None:
        if self.is_destination:
            self._accept(incoming, now)
            return
        if not self.forwarding:
            self._drop("forwarding_disabled")
            return
        if incoming.hop_count + 1 > params.LSA_TTL:
            self._drop(DROP_TTL)
            self.protocol_errors += 1
            return
        if now > incoming.expires_at:
            self._drop(DROP_EXPIRED)
            return
        self.store.push(incoming)

    def _accept(self, incoming: pk.Packet, now: float) -> None:
        """Deduplication at the destination, by (origin_id, sequence) alone.

        A retry from the origin and a drain from the backlog custodian are both
        correct and both arrive, so duplicates are guaranteed by the design
        rather than by a bug. Counting arrivals would report the backlog
        delivered twice; comparing identities reports it delivered once.
        """
        key = incoming.identity()
        ack_path = list(incoming.path)
        if key in self.accepted:
            self.duplicated += 1
        else:
            self.accepted[key] = now
            self.accepted_path[key] = ack_path
            self.accepted_hops[key] = incoming.hop_count
            self.ledger.append({"id": incoming.identity_str(),
                                "created_at": incoming.created_at,
                                "delivered_at": now,
                                "path": ack_path,
                                "hop_count": incoming.hop_count})
        # Acknowledge either way. An unacknowledged duplicate would make the
        # origin retry forever against a destination that already has it.
        self._queue_ack(incoming, ack_path, now)

    def _queue_ack(self, incoming: pk.Packet, arrival_path: List[str],
                   now: float) -> None:
        next_hop = self._next_hop_toward(incoming.origin_id)
        if next_hop is None:
            return
        self._ack_seq += 1
        ack = pk.control(self.node_id, pk.KIND_ACK, now,
                         {"id": incoming.identity_str(),
                          "origin_id": incoming.origin_id,
                          "sequence": incoming.sequence,
                          "path": arrival_path},
                         dest_id=incoming.origin_id, sequence=self._ack_seq)
        ack.path = [self.node_id, next_hop]
        self._queue_control(ack, now)

    def _on_ack(self, incoming: pk.Packet, now: float) -> None:
        body = incoming.payload
        if incoming.dest_id != self.node_id:
            if not self.forwarding:
                self._drop("forwarding_disabled")
                return
            next_hop = self._next_hop_toward(incoming.dest_id)
            if next_hop is None:
                return
            out = incoming.copy(hop_count=incoming.hop_count + 1)
            out.path = list(incoming.path) + [next_hop]
            self._queue_control(out, now)
            return
        key = (body["origin_id"], int(body["sequence"]))
        self.pending_ack.pop(key, None)
        self.store.drop(key)
        for message in self.roles.confirm(body["id"], body["path"], now):
            self._send_role(message, now)

    # -- the seam, egress ---------------------------------------------------

    def drain_tx(self, now: float) -> List[pk.Packet]:
        """Everything this node publishes on its own tx topic this tick."""
        out = self._tx
        self._tx = []
        return out

    def _emit(self, outgoing: pk.Packet) -> None:
        self._tx.append(outgoing)

    def _queue_control(self, outgoing: pk.Packet, now: float) -> None:
        if len(self.control) >= self.control_capacity:
            self.control.popleft()
            self._drop("control_evicted")
        self.control.append((now, outgoing))

    # -- routing helpers ----------------------------------------------------

    def topology(self) -> Dict[str, Set[str]]:
        """The graph this node routes on, with its own row taken from live state."""
        return self.lsdb.topology({self.node_id: self.neighbours.live()})

    def relay_set(self) -> FrozenSet[str]:
        """Nodes currently holding the relay role, from what this node has seen.

        The route key charges a path for the temporary relays on it, so the
        set has to be the same on every node. It comes out of the role
        messages every member received, which is the only place the assignment
        is ever stated.
        """
        held = set()
        for epoch in self.roles.epochs.values():
            if epoch.winner is not None and epoch.open():
                held.add(epoch.winner)
        return frozenset(held)

    def _next_hop_toward(self, target: str) -> Optional[str]:
        if target == self.node_id:
            return None
        topo = self.topology()
        path = graph.dijkstra(topo, self.node_id, target, self.relay_set())
        if path is None or len(path) < 2:
            return None
        return path[1]

    def _distance_to(self, node_id: Optional[str]) -> Optional[float]:
        if node_id is None:
            return None
        other = self.position_cache.get(node_id)
        if other is None:
            return None
        return ((self.position[0] - other[0]) ** 2
                + (self.position[1] - other[1]) ** 2
                + (self.position[2] - other[2]) ** 2) ** 0.5

    def custodian(self) -> Optional[str]:
        """The lowest id member of a component that has no route to the destination.

        Without this rule the backlog splits across the disconnected origins
        and the deepest queue is half of what the design is sized for, so the
        depth the store-and-forward claim rests on is never reached by
        anything that could fail.
        """
        topo = self.topology()
        component = graph.component_of(topo, self.node_id)
        if self.destination in component:
            return None
        return min(component)

    def reachability(self) -> Dict[str, bool]:
        """Every node this router knows of, and whether it can reach it now."""
        topo = self.topology()
        known = set(topo) | set(self.position_cache) | {self.destination}
        return graph.reachability(topo, self.node_id, sorted(known))

    def route_status(self) -> str:
        if self.route.has_route():
            return "route_up"
        return "disconnected" if self.disconnected else "route_down"

    # -- the tick -----------------------------------------------------------

    def tick(self, now: float, dt: float) -> None:
        """Timers, in the order the design depends on."""
        self.store.expire(now)
        gone = self.neighbours.expire(now)
        if gone:
            self.lost_neighbours |= gone
            self._next_lsa_at = now                 # immediately on any change

        topo = self.topology()
        self.route.withdraw_if_broken(topo)

        if now >= self._next_compute_at:
            self._next_compute_at = now + params.LSA_PERIOD_S
            self._compute_route(topo, now)

        self._track_connectivity(now)

        if now >= self._next_hello_at:
            self._next_hello_at = now + params.HELLO_PERIOD_S
            self._send_hello(now)

        if now >= self._next_lsa_at:
            self._next_lsa_at = now + params.LSA_PERIOD_S
            self._send_lsa(now)
            if self._relay_still_reachable():
                for message in self.roles.renew(now):
                    self._send_role(message, now)

        for message in self.roles.close_window(now):
            if message["kind"] == election.ASSIGN:
                message = self._with_slot(message, now)
                if message is None:
                    continue
            self._send_role(message, now)
        if self.roles.check_lease(now):
            self._report(now, "lease_expired")
        if self.roles.abandon_stale_prepare(now):
            self._report(now, "release_abandoned")

        self._service(now, dt)

    def _relay_still_reachable(self) -> bool:
        """Whether the epoch owner should go on renewing the relay's lease.

        The owner renews for as long as the epoch is open, and the epoch is
        open until the relay is released or the lease runs out. If the relay
        itself dies, nothing releases it, so an owner that renewed
        unconditionally would hold the epoch open forever: the role would never
        revert, no member could open the next epoch, and a swarm that lost its
        elected relay would sit there. Renewal stops when the relay is no
        longer in this node's component, the lease runs down, and every member
        abandons the epoch on its own clock rather than on a message.
        """
        epoch = self.roles.current
        if epoch is None or not epoch.open() or epoch.winner is None:
            return False
        return epoch.winner in graph.component_of(self.topology(), self.node_id)

    def _compute_route(self, topo: Dict[str, Set[str]], now: float) -> None:
        relays = self.relay_set()
        event = self.route.compute(topo, self.node_id, relays)
        if self.route.installed:
            self.last_gcs_route = list(self.route.installed)
        if self.route.has_route():
            # Retry on route recovery, oldest first, and then once per
            # computation for as long as anything is still unacknowledged.
            #
            # Recovery alone is not enough, and the reason is the fade band.
            # The mover crosses from beyond r_max to its slot, so the link to
            # the anchor comes up inside the fade band and drops real packets
            # there. Those are already gone from every queue and are held only
            # by their origin, so a retry that fires once on recovery leaves
            # them retained forever and the delivered set never equals the
            # generated set. Retention until acknowledgement means nothing if
            # nothing ever acts on it.
            #
            # The retry is skipped entirely while there is no route, so an
            # outage does not turn into a retransmission storm, and the queue is
            # keyed by identity so re-queueing cannot inflate the depth the gate
            # reads.
            self.retry_pending()
        self._offer_handback(topo, relays, now)

    def _with_slot(self, message: dict, now: float) -> Optional[dict]:
        """Fill in the relay slot the winner is being sent to.

        architecture.md section 4 step 1 puts slot_position in the ELECTION
        message, and the slot rule two sections above it defines the slot
        against the work of the members that STAY. Those cannot both be true:
        who stays is not known until step 3. The binding slot therefore travels
        in ASSIGN, which is the first message sent after the winner is known,
        and the ELECTION carries the attachment node instead. This is the one
        place the implementation departs from the frozen text and it is written
        down rather than absorbed.
        """
        epoch = self.roles.current
        if epoch is None:
            return None
        winner = message["winner"]
        anchor = self.position_cache.get(epoch.attachment_id)
        if anchor is None:
            return self._give_up(epoch, now, NO_ATTACHMENT)

        work: List[Tuple[float, float, float]] = []
        for member in sorted(epoch.members):
            if member == winner:
                continue
            declared = epoch.work.get(member)
            if declared:
                work.extend(tuple(p) for p in declared)
            else:
                held = self.position_cache.get(member)
                if held is not None:
                    work.append(tuple(held))
        if not work:
            # A component of one. The only eligible mover is also the only
            # member that would stay, so there is no work to balance against
            # and no link for a relay to carry. Reporting it beats parking the
            # last vehicle in a slot with nothing behind it.
            return self._give_up(epoch, now, RELAY_INFEASIBLE)

        # Every vehicle this node has ever been told about, minus the mover and
        # the ground station, is treated as still flying. Round 5 finding 4: the
        # silent one is the one you cannot see, and assuming it is gone is how a
        # relay ends up parked on top of it.
        live = [pos for node, pos in sorted(self.position_cache.items())
                if node not in (winner, self.destination)]
        decision = slots.solve(anchor, work, live)
        if not decision.feasible():
            return self._give_up(epoch, now, RELAY_INFEASIBLE)

        out = dict(message)
        out["slot"] = decision.slot
        out["anchor_hop_m"] = decision.anchor_hop_m
        out["work_hop_m"] = decision.work_hop_m
        out["clearance_m"] = decision.clearance_m
        out["band_reserved"] = decision.band_reserved
        self.last_slot = decision
        return out

    def _offer_handback(self, topo: Dict[str, Set[str]],
                        relays: FrozenSet[str], now: float) -> None:
        """The owner's half of make before break, once per computation.

        Every staying member has to have a cheapest route that avoids the
        relay, not just this one, and the offer is withdrawn the moment any of
        them stops having one. Round 5 finding 1: acting on the mere existence
        of an alternate somewhere in the swarm breaks the installed next hop
        before the alternate is selected.
        """
        epoch = self.roles.current
        if epoch is None or not epoch.open() or epoch.owner != self.node_id:
            return
        if epoch.winner is None:
            return
        own_path = None
        for member in sorted(epoch.members):
            if member == epoch.winner:
                continue
            path = graph.dijkstra(topo, member, self.destination, relays)
            if path is None or epoch.winner in path:
                own_path = None
                break
            if member == self.node_id:
                own_path = path
        for message in self.roles.offer_path(own_path, now):
            self._send_role(message, now)

    def _track_connectivity(self, now: float) -> None:
        if self.route.has_route():
            self._route_absent_since = None
            if self._route_present_since is None:
                self._route_present_since = now
            elif (self.recovered_at is None
                  and now - self._route_present_since >= params.STABILITY_WINDOW_S):
                self.recovered_at = now
                self.disconnected = False
            return

        self._route_present_since = None
        self.recovered_at = None
        if self._route_absent_since is None:
            self._route_absent_since = now
        if (not self.disconnected
                and now - self._route_absent_since >= params.NEIGHBOUR_TIMEOUT_S):
            self.disconnected = True
            self._report(now, "disconnected")
        if self.disconnected and self.elections_enabled:
            self._maybe_open_election(now)

    def _give_up(self, epoch, now: float, reason: str) -> None:
        """Abandon this epoch and stop reopening it for the same component.

        Reporting the condition and then immediately trying again produces an
        epoch per computation forever, which is a flap wearing a different
        hat: nothing moves, nothing recovers, and the epoch counter runs away.
        The suppression lifts when the component membership changes, because
        that is the only thing that can change the answer.
        """
        self._report(now, reason)
        epoch.abandoned = True
        self.relay_infeasible = True
        self._infeasible_for = frozenset(epoch.members)
        return None

    def _maybe_open_election(self, now: float) -> None:
        topo = self.topology()
        component = graph.component_of(topo, self.node_id)
        if not self.last_gcs_route:
            # A component that has never held a route home has nothing to
            # reconnect to, and every node is DISCONNECTED for the first few
            # seconds of any run while the graph is still converging. Electing
            # there would be a relay chosen before the swarm had a topology.
            return
        if self.relay_infeasible:
            if self._infeasible_for == frozenset(component):
                return
            self.relay_infeasible = False
            self._infeasible_for = None
        if self.node_id != min(component):
            return
        attachment = self._attachment(topo, component)
        if attachment is None:
            self._report(now, NO_ATTACHMENT)
            return
        opened = self.roles.open_election(now, frozenset(component),
                                          attachment, None)
        if opened is not None:
            self._report(now, "election_opened")
            self._send_role(opened, now)

    def _attachment(self, topo: Dict[str, Set[str]],
                    component: Set[str]) -> Optional[str]:
        """The last node the component could reach that STILL had a route home.

        Walked outward along the route this node held before the split, so the
        anchor behind the break is chosen rather than the nearest connected
        node. Round 3 finding 1 broke the previous rule, which could pick the
        very candidate the election was about to send away.

        The word doing the work is "still". A component cannot be told that the
        far side has stopped listing the failed node, because every path that
        news could have travelled went through the failed node: the surveyors'
        link-state view keeps the anchor and the dead relay listing each other
        forever, so the dead relay still appears to hold a route home. The node
        the direct link was lost to is the break, and the break is never the
        attachment. Without this the component elects a relay and sends it to
        balance against a corpse, which lands it nowhere near the anchor and
        the chain never reforms.
        """
        for node in (self.last_gcs_route or []):
            if node in component or node in self.lost_neighbours:
                continue
            if node not in topo:
                continue
            if graph.dijkstra(topo, node, self.destination) is not None:
                return node
        return None

    # -- outgoing control ---------------------------------------------------

    def _send_hello(self, now: float) -> None:
        self._hello_seq += 1
        hello = pk.control(self.node_id, pk.KIND_HELLO, now,
                           {"sender_id": self.node_id,
                            "sent_at": now,
                            "position": self.position,
                            "velocity": (0.0, 0.0, 0.0),
                            "seq": self._hello_seq},
                           sequence=self._hello_seq)
        self._queue_control(hello, now)

    def _send_lsa(self, now: float) -> None:
        self._lsa_seq += 1
        neighbours = sorted(self.neighbours.live())
        lsa = pk.control(self.node_id, pk.KIND_LSA, now,
                         {"originator_id": self.node_id,
                          "lsa_seq": self._lsa_seq,
                          "neighbours": neighbours,
                          "sent_at": now,
                          "ttl": params.LSA_TTL},
                         sequence=self._lsa_seq)
        self._queue_control(lsa, now)
        self.lsdb.accept(graph.Lsa(self.node_id, self._lsa_seq,
                                   frozenset(neighbours), now, params.LSA_TTL))

    def _send_role(self, message: dict, now: float) -> None:
        self._role_seq += 1
        body = dict(message)
        body["position"] = self.position
        body["attachment_position"] = self.position_cache.get(
            body.get("attachment_id"))
        body["work"] = [tuple(p) for p in self.work_points]
        role = pk.control(self.node_id, pk.KIND_ROLE, now, body,
                          sequence=self._role_seq)
        self._queue_control(role, now)
        # A node acts on its own role message immediately. The alternative is
        # waiting to overhear itself, which never happens.
        distance = self._distance_to(body.get("attachment_id"))
        _, replies = self.roles.on_message(message, now, distance)
        for reply in replies:
            self._send_role(reply, now)

    # -- the application ----------------------------------------------------

    def observe(self, now: float) -> pk.Packet:
        """Generate one observation. The origin retains it until it is acknowledged."""
        self._obs_seq += 1
        obs = pk.observation(self.node_id, self._obs_seq, now)
        self.generated_ids.append(obs.identity_str())
        self.pending_ack[obs.identity()] = obs
        self.store.push(obs)
        return obs

    def retry_pending(self) -> int:
        """Re-queue everything still unacknowledged, oldest first.

        Called when a route returns. The store may already hold some of them,
        and the queue is keyed by identity, so re-queueing is idempotent and
        cannot inflate the depth the gate reads.
        """
        requeued = 0
        for key in sorted(self.pending_ack,
                          key=lambda k: self.pending_ack[k].created_at):
            if key in self.store:
                continue
            self.store.push(self.pending_ack[key])
            requeued += 1
        return requeued

    def _observation_next_hop(self) -> Optional[str]:
        """Where the observations in the store go next, or None to keep holding."""
        if self.route.has_route():
            return self.route.next_hop()
        if not self.forwarding:
            return None
        holder = self.custodian()
        if holder is None or holder == self.node_id:
            return None
        return self._next_hop_toward(holder)

    def _service(self, now: float, dt: float) -> None:
        """Serve control first, always, then observations at the forward rate.

        Without this order a 450 packet backlog sits in front of the very
        traffic that would end the outage, and the swarm takes longer to
        recover the harder it was working.
        """
        allowance = self.service.allowance(dt)
        while allowance > 0 and self.control:
            queued_at, outgoing = self.control.popleft()
            self.control_max_delay_s = max(self.control_max_delay_s,
                                           now - queued_at)
            self._emit(outgoing)
            allowance -= 1
        if allowance <= 0:
            return

        next_hop = self._observation_next_hop()
        if next_hop is None:
            return
        while allowance > 0:
            held = self.store.pop()
            if held is None:
                break
            allowance -= 1
            hop_count = held.hop_count
            if held.origin_id != self.node_id:
                hop_count += 1
            if hop_count > params.LSA_TTL:
                self._drop(DROP_TTL)
                continue
            out = held.copy(hop_count=hop_count)
            out.path = list(held.path) + [next_hop]
            self._emit(out)

    # -- what the run record wants -----------------------------------------

    def observation_summary(self) -> dict:
        """The counters architecture.md section 3 names, from this node alone."""
        return {
            "generated": len(self.generated_ids),
            "generated_ids": list(self.generated_ids),
            "delivered_ids": sorted(
                "{0}:{1}".format(o, s) for (o, s) in self.accepted),
            "unique_delivered": len(self.accepted),
            "duplicated": self.duplicated,
            "expired": self.store.expired,
            "evicted": self.store.evicted,
            "peak_queue_depth": self.store.peak,
            "control_queue_max_delay_s": self.control_max_delay_s,
            "protocol_errors": self.protocol_errors,
            "drops": dict(self.drops),
        }
