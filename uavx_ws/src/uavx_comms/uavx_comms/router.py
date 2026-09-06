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

        # Chunk 4.2. When a route last reappeared, and when the store first
        # ran empty afterwards. The drain bound is a claim about this node's
        # queue, and the only other place to measure it from is the arrival
        # times at the ground station, which is a different quantity: link
        # latency does not disappear when a queue does.
        self.route_returned_at: Optional[float] = None
        self.drain_end_at: Optional[float] = None
        # Every moment this node declared itself cut off. A count would say
        # how often and not when, and link_loss asks whether anything went
        # down again after the relay was handed back, which is a question
        # about times.
        self.outages: list = []
        # Who this node named to hold the component's backlog, and for how
        # many simulated seconds it named them. Written down because the
        # answer cannot be reconstructed afterwards: by the time the run ends
        # the component has merged, and reading custody off what each node
        # turned out to be holding names the anchor that forwarded everything
        # rather than the member that was cut off holding it.
        #
        # Seconds rather than a single name, because the first tick after a
        # link drops can see a component of one before the neighbour table has
        # caught up, and a node that named itself for a tenth of a second is
        # not the custodian of anything.
        self.custodian_seconds: Dict[str, float] = {}
        # What this node was holding at the moment its route came back, and
        # what is left of it. The backlog an outage built is that set and
        # nothing else; a surveying vehicle mints into the same queue while it
        # drains, so the store itself never runs empty on the backlog's
        # timetable.
        self._backlog: Set[Tuple[str, int]] = set()
        # The custodian this node has already handed its backlog to. Cleared
        # when a route returns, so the next outage hands over again.
        self._handed_to: Optional[str] = None
        # One row per period this node had a route: when it came back, when
        # it had held it long enough to be believed, when the store first ran
        # empty on it, and when it went again.
        #
        # The three scalars above hold the latest of each, which is what the
        # node needs to run and what the first complete link_loss found to be
        # useless afterwards: the relay flapped once while flying home, and a
        # run that reconnected in 23 s read as one that took 137.
        self.route_episodes: list = []
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
        # When each of those was last sent, so the retry can tell a packet
        # that is overdue from one that is still in flight.
        self._sent_at: Dict[Tuple[str, int], float] = {}
        self.generated_ids: List[str] = []
        self.generated_at: Dict[str, float] = {}

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
        # What that delay is a statement about. Control is served in the
        # callback that produced it, so the delay is zero in a healthy run,
        # and a zero next to no count at all is the shape of a measurement
        # nobody took.
        self.control_served = 0
        self.control_peak_depth = 0
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
        """One packet off this node's rx topic. The only way anything gets in.

        Whatever this produces leaves in the same callback. See _serve_control:
        a HELLO forwarded here and served on the next tick has waited a tick,
        and on a 10 Hz clock a tick reads as a tenth of a second of delay that
        had nothing to do with anything being busy.
        """
        self._dispatch(incoming, now)
        self._serve_control(now)

    def _dispatch(self, incoming: pk.Packet, now: float) -> None:
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
            fresh = self.neighbours.hello(sender, body["position"], now,
                                          body["seq"])
            self.lost_neighbours.discard(sender)
            if fresh:
                # A neighbour arriving is a topology change and the design
                # promises the link state goes out immediately on any of
                # them. Only the leaving half was wired, so a radio coming
                # back waited up to one LSA period to be told about, and the
                # queue that is waiting for the route filled through it.
                #
                # The flood only. Hearing a neighbour says this node has an
                # edge again; it says nothing about what the neighbour can
                # reach, and the database still holds whatever that neighbour
                # advertised before it went quiet. Recomputing here installs a
                # route over an edge that stopped existing 45 seconds ago: the
                # 6 September queue_drain did exactly that, took a route at
                # 223.1 and withdrew it at 223.3 when the real link state
                # arrived. The computation is triggered by that arrival
                # instead, in _on_lsa.
                self._next_lsa_at = now
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
            # News, so the graph this node routes over has just changed. This
            # is the moment to recompute: the alternative is waiting out the
            # rest of a period holding a route the new link state may have
            # already broken, or acting on a hello, which says a neighbour is
            # back and nothing about what it can reach.
            self._next_compute_at = now
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
        self._send_role_replies(replies, now)
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
        self.store.push(incoming, self.pending_ack)

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
        self._sent_at.pop(key, None)
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
        self.control_peak_depth = max(self.control_peak_depth,
                                      len(self.control))

    def _serve_control(self, now: float) -> int:
        """Hand the radio every control message that is waiting.

        Private on purpose. Nothing outside the router calls it: it is the
        two internal places control is produced, and the only way anything
        leaves this object is still drain_tx.

        Called wherever control is produced: the tick that generates HELLO and
        LSA, and the rx callback that forwards somebody else's. architecture.md
        section 3 says observations never delay control, and a queue served
        only on the tick makes that false by a tick even when the radio is
        idle.

        Control is not charged against the forward rate. That rate is the size
        of the observation pipe and the number the 2.25 s drain bound comes
        from, and taking control out of it would make a swarm that talks more
        drain slower.
        """
        served = 0
        while self.control:
            queued_at, outgoing = self.control.popleft()
            self.control_max_delay_s = max(self.control_max_delay_s,
                                           now - queued_at)
            self._emit(outgoing)
            served += 1
        self.control_served += served
        return served

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

    def _note_custodian(self, dt: float) -> None:
        """Charge this tick to whoever this node currently says holds the backlog.

        Only while this node is cut off, which is the design's own condition
        and not merely the absence of a route right now. Every node is without
        a route for the first seconds of a run while the link state travels,
        and a node counting its own bring-up would name itself custodian of a
        component that had not finished forming.
        """
        if self.route.has_route() or not self.disconnected:
            return
        holder = self.custodian()
        if holder is None:
            return
        step = float(dt)
        if step <= 0:
            return
        self.custodian_seconds[holder] = (
            self.custodian_seconds.get(holder, 0.0) + step)

    def custodian_named(self) -> Optional[str]:
        """The node this router named custodian for longest, ties to the lowest id."""
        if not self.custodian_seconds:
            return None
        return min(self.custodian_seconds,
                   key=lambda node: (-self.custodian_seconds[node], node))

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
        self._note_custodian(dt)

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
        for message in self.roles.release_when_due(now):
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
            self.retry_pending(now)
            self._handed_to = None
        else:
            # No route, so the backlog belongs with the component's custodian
            # rather than with whoever happened to mint it.
            #
            # Everything this node sent in the three seconds between the radio
            # going quiet and the neighbour table timing out was dropped by a
            # radio that was not there. It is retained and it is nowhere near
            # the member that is holding the backlog, and the design's claim
            # is about one member holding it. Handing over once at the moment
            # the component reorganises puts those observations where the rest
            # of them are.
            #
            # Once per custodian and not once per computation. What this node
            # has already passed on is out of its own queue, so a retry every
            # two seconds would send the whole backlog again every two seconds.
            holder = self.custodian()
            if holder is not None and holder != self._handed_to:
                self._handed_to = holder
                self.retry_pending(now, only_overdue=False)
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
                # The drain clock starts when the route comes back, not when
                # the recovery is confirmed three seconds later.
                self.route_returned_at = now
                self.drain_end_at = None
                self._backlog = {key for key in self.store.identities()}
                self.route_episodes.append({
                    "returned_at": round(now, 3), "recovered_at": None,
                    "drained_at": None, "lost_at": None,
                    # The backlog this node was holding when it got somewhere
                    # to send it, and when the last of that set left. The
                    # bound the gate reads is measured between the two.
                    "backlog": len(self._backlog),
                    "backlog_cleared_at": (round(now, 3) if not self._backlog
                                           else None)})
            elif (self.recovered_at is None
                  and now - self._route_present_since >= params.STABILITY_WINDOW_S):
                self.recovered_at = now
                self.disconnected = False
                if self.route_episodes:
                    self.route_episodes[-1]["recovered_at"] = round(now, 3)
            return

        if self._route_present_since is not None and self.route_episodes:
            self.route_episodes[-1]["lost_at"] = round(now, 3)
        self._route_present_since = None
        self.recovered_at = None
        if self._route_absent_since is None:
            self._route_absent_since = now
        if (not self.disconnected
                and now - self._route_absent_since >= params.NEIGHBOUR_TIMEOUT_S):
            self.disconnected = True
            self.outages.append(round(now, 3))
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
        self._send_role_replies(replies, now)

    def _send_role_replies(self, replies, now: float) -> None:
        """Send what the machine wants sent, except the acceptance.

        Chunk 4.1. The machine produces a ROLE_ACK the moment it decodes an
        ASSIGN naming this node, and the router used to put that on the wire
        straight away. It said only that a message had been received: the
        aircraft was still 195 m from the slot, and nothing anywhere read the
        flag it set at the coordinator.

        `uavx_roles.role_manager` sends it instead, on arrival, from the
        vehicle whose role it is. Same message, same epoch, same reader. It
        now means the node that was told to move has moved, which is what
        `relay_role_moved` claims and what a run record should be able to be
        read against.
        """
        for reply in replies:
            if reply.get("kind") == election.ROLE_ACK:
                continue
            self._send_role(reply, now)

    # -- the application ----------------------------------------------------

    def observe(self, now: float) -> pk.Packet:
        """Generate one observation. The origin retains it until it is acknowledged."""
        self._obs_seq += 1
        obs = pk.observation(self.node_id, self._obs_seq, now)
        self.generated_ids.append(obs.identity_str())
        self.generated_at[obs.identity_str()] = now
        self.pending_ack[obs.identity()] = obs
        self.store.push(obs, self.pending_ack)
        return obs

    def retry_pending(self, now: float, only_overdue: bool = True) -> int:
        """Re-queue everything overdue, oldest first.

        Overdue and not merely unacknowledged. An observation sent a moment ago
        has not had time to reach the ground station and be answered, and
        putting a second copy of it into the queue is what turns a recovery
        into a retransmission storm. The first queue_drain measured it: 153
        packets back into a queue that was two seconds from empty, every route
        computation, for as long as anything was outstanding.

        `only_overdue` is false for the one case where waiting proves nothing.
        When the component reorganises, everything this node sent toward a next
        hop it can no longer reach is gone for certain rather than in flight,
        and it belongs with the custodian now.

        The store may already hold some of them, and the queue is keyed by
        identity, so re-queueing is idempotent and cannot inflate the depth the
        gate reads.
        """
        requeued = 0
        for key in sorted(self.pending_ack,
                          key=lambda k: self.pending_ack[k].created_at):
            if key in self.store:
                continue
            sent = self._sent_at.get(key)
            if (only_overdue and sent is not None
                    and now - sent < params.RETRY_AFTER_S):
                continue
            self.store.push(self.pending_ack[key], self.pending_ack)
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
        self._serve_control(now)
        allowance = self.service.allowance(dt)

        next_hop = self._observation_next_hop()
        if next_hop is None:
            return
        self._note_drain(now)
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
            if held.identity() in self.pending_ack:
                self._sent_at[held.identity()] = now
            self._note_backlog(held.identity(), now)
        self._note_drain(now)

    def _note_backlog(self, key, now: float) -> None:
        """One backlog packet has left. Stamp the episode when the last does."""
        if not self._backlog:
            return
        self._backlog.discard(key)
        if self._backlog or not self.route_episodes:
            return
        if self.route_episodes[-1].get("backlog_cleared_at") is None:
            self.route_episodes[-1]["backlog_cleared_at"] = round(now, 3)

    def _note_drain(self, now: float) -> None:
        """The first moment the store was empty after the route returned.

        Called on both sides of the drain loop, so a queue that was already
        empty when the route came back reports the moment it came back rather
        than waiting for a packet that never arrives to prove it.
        """
        if self.drain_end_at is not None or self.route_returned_at is None:
            return
        if len(self.store) == 0 and now >= self.route_returned_at:
            self.drain_end_at = now
            if self.route_episodes:
                self.route_episodes[-1]["drained_at"] = round(now, 3)

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
            # Pushed out of the queue and not lost: this node minted it
            # and still holds it until the destination acknowledges it.
            "deferred": self.store.deferred,
            "peak_queue_depth": self.store.peak,
            "control_queue_max_delay_s": self.control_max_delay_s,
            "control_served": self.control_served,
            "control_peak_depth": self.control_peak_depth,
            "protocol_errors": self.protocol_errors,
            "drops": dict(self.drops),
            # When each of this node's own observations was minted, so the
            # arithmetic downstream can say which of them were generated
            # during an outage without asking the runner to guess from a rate.
            "generated_at": {k: round(v, 3)
                             for k, v in sorted(self.generated_at.items())},
            # Everything this node's store has ever held, its own
            # observations included. queue_drain's custody rule is that a
            # disconnected component funnels its whole backlog to one member,
            # so the claim is about what one node held rather than about what
            # it held for others.
            "custodied_ids": sorted(self.store.held_ids),
            "custodied": len(self.store.held_ids),
            # Who this node named to hold the component's backlog while it had
            # no route, and for how long it named them. The record reads the
            # custodian off the nodes that were actually cut off, because
            # every forwarder ends up holding ids it did not mint and the
            # lowest id of those is the anchor that was never disconnected.
            "custodian_named": self.custodian_named(),
            "custodian_named_s": round(
                self.custodian_seconds.get(self.custodian_named(), 0.0), 3),
            # What this node has minted and not yet had acknowledged. In a
            # healthy run this is the last packet or two and it empties on the
            # next ack. It is in the file because of what it means when the
            # vehicle does not come back: these are the observations that were
            # still only on that aircraft, so they went down with it. A record
            # that cannot name them cannot tell an observation the swarm
            # dropped from one that stopped existing, and the first is a
            # routing failure while the second is arithmetic.
            "unacknowledged_ids": sorted(
                packet.identity_str() for packet in self.pending_ack.values()),
            "unacknowledged": len(self.pending_ack),
            # One row per period this node had a route. Every recovery
            # number is about the first of them after the fault, and the
            # scalars below are only ever the latest.
            "route_episodes": list(self.route_episodes),
            # When this node lost its route, each time it did. The handback
            # claim is that giving the vehicle back broke nothing, and an
            # outage after the release is what would make it false.
            "outages": list(self.outages),
            # When this node last had a route it had held for the stability
            # window. Cleared the moment the route goes, so a value later than
            # an injected event is this node saying it lost the route and got
            # it back.
            "recovered_at": (None if self.recovered_at is None
                             else round(self.recovered_at, 3)),
            # When the route last came back and when this node's queue first
            # ran empty afterwards. The drain bound is measured between them.
            "route_returned_at": (None if self.route_returned_at is None
                                  else round(self.route_returned_at, 3)),
            "drain_end_at": (None if self.drain_end_at is None
                             else round(self.drain_end_at, 3)),
            # The handback transaction, straight out of the machine that ran
            # it. Empty on a node that never opened an epoch. Only the owner
            # holds all of it: it is the node that prepared the path, took the
            # confirmation off the destination's acknowledgement and sent the
            # release, and it applies its own release locally rather than
            # waiting to overhear itself.
            "handback": self.roles.handback_trace(),
            # The slot this node last computed for a relay, or None if it
            # never ran an election. Carried out of the component that decided
            # it rather than recomputed by the runner, which would be a second
            # answer to the question the gate reads.
            "relay_slot": (self.last_slot.as_record()
                           if self.last_slot is not None
                           and self.last_slot.slot is not None else None),
            # One row per accepted observation, non-empty only at the
            # destination. The destination has built these since chunk 3.1 and
            # nothing has ever read them: they are the per delivery times and
            # paths every field of the observations block is computed from.
            "ledger": list(self.ledger),
        }
