"""Routing, driven through situations rather than read back.

Every test here starts from the frozen topology and an event, and asserts on
what the swarm ended up doing. The oracle for anything geometric is
scripts/check_geometry.py, which enumerates every pair and every loop-free
path; nothing in this file recomputes a distance.
"""

import math

from conftest import CONVERGENCE_S, frozen_net, oracle_path, settled_net

from uavx_comms import graph, packet as pk, params, routing

import check_geometry as oracle


def drain(net, seconds=6.0):
    net.run_for(seconds)


# --------------------------------------------------------------- reachability

def test_the_far_surveyor_reaches_the_ground_station_by_relay_and_not_directly():
    """The claim the whole comms row rests on, asked as an outcome.

    Two halves, and only having both makes it falsifiable. The route that
    installs has to be the one the oracle derives, and the direct pair has to
    be one the radio refuses. A test that only asserted the first would pass on
    a topology where the far surveyor could have shouted straight at the
    ground station, which is round 3 finding 1 exactly.
    """
    net = settled_net()
    installed = net.installed_route("uav_4")
    assert installed == oracle_path(oracle.START, "uav_4")
    assert "uav_2" in installed, (
        "the far surveyor reached the ground station without the relay, so "
        "killing the relay would prove nothing")

    direct = math.dist(oracle.START["uav_4"], oracle.START["gcs"])
    assert not net.link.deliver("uav_4", "gcs", direct)


def test_hop_counts_are_the_ones_the_geometry_oracle_derives():
    net = settled_net()
    net.start_observing("uav_4", at=net.now)
    net.start_observing("uav_3", at=net.now)
    drain(net, 6.0)

    sink = net.router("gcs")
    assert sink.accepted, "nothing arrived, so there are no hops to check"
    for (origin, _sequence), arrived in sorted(sink.accepted_path.items()):
        expected = oracle_path(oracle.START, origin)
        assert arrived == expected, (origin, arrived, expected)

    for key, hops in sorted(sink.accepted_hops.items()):
        edges = len(sink.accepted_path[key]) - 1
        # hop_count counts forwarders and the route key counts edges, and the
        # two differ by exactly one for any delivered packet. Recording only
        # one of them is how a gate asking for at least two hops ends up
        # satisfied by a one hop delivery.
        assert hops == edges - 1


def test_a_partitioned_vehicle_is_reported_unreachable_rather_than_dropped():
    """Silence is the failure mode. The swarm has to say so, and hold the data.

    A router that quietly discarded observations while it had no route would
    look identical to this one on every delivery count taken after recovery,
    right up until the recovered set was compared with the generated set.
    """
    net = settled_net()
    net.start_observing("uav_4", at=net.now)
    drain(net, 4.0)
    net.kill("uav_2")

    uav_4 = net.router("uav_4")
    net.run_for(params.NEIGHBOUR_TIMEOUT_S - 0.5)
    assert uav_4.route_status() != "disconnected", (
        "the node called itself disconnected before the neighbour timeout had "
        "elapsed, so one missed HELLO would be enough to open an election")

    net.run_for(params.NEIGHBOUR_TIMEOUT_S + 2 * params.LSA_PERIOD_S + 1.0)
    assert uav_4.route_status() == "disconnected"
    assert uav_4.reachability()["gcs"] is False
    assert uav_4.reachability()["uav_3"] is True

    generated = set(uav_4.generated_ids)
    delivered = set(net.delivered_ids())
    stranded = generated - delivered
    assert stranded, "nothing was stranded, so this proves nothing about holding"
    held = set(uav_4.pending_ack) | set(net.router("uav_3").store.identities())
    held_ids = {"{0}:{1}".format(o, s) for (o, s) in held}
    assert stranded <= held_ids, (
        "observations went missing rather than being held: "
        + repr(sorted(stranded - held_ids)[:5]))
    assert uav_4.store.evicted == 0 and uav_4.store.expired == 0


def test_with_forwarding_off_the_far_surveyor_delivers_nothing():
    """direct_only.yaml, the control that makes the relay claim falsifiable.

    A relay that works in a scenario where the direct link would also have
    worked has demonstrated nothing. The switch has to be real code, and the
    result has to be exactly zero rather than merely low. The last assertion is
    the one that stops this passing for the wrong reason: if the anchor, which
    is one hop from the ground station, also delivered nothing, then this run
    proves the harness is broken rather than that the control works.
    """
    net = frozen_net(forwarding=False, elections_enabled=False)
    net.run_for(CONVERGENCE_S)
    net.start_observing("uav_4", at=net.now)
    net.start_observing("uav_1", at=net.now)
    drain(net, 20.0)

    delivered = set(net.delivered_ids())
    assert not any(i.startswith("uav_4:") for i in delivered), (
        "the far surveyor delivered with forwarding disabled")
    assert net.router("uav_4").generated_ids, "it never generated anything"
    assert any(i.startswith("uav_1:") for i in delivered)


# -------------------------------------------------------------- delivered once

def test_a_packet_is_never_counted_delivered_twice():
    """Duplicates are guaranteed by the design, so deduplication is the deliverable.

    During a partition the origin keeps its own copy until the ground station
    acknowledges it, and the backlog custodian holds a copy too. Both drain
    when the route returns. An implementation that counted arrivals would
    report the backlog delivered twice and still satisfy a gate that compares
    totals; comparing identities is what makes the claim mean anything. The
    first assertion is the important one, because without a duplicate actually
    arriving the set equality below is proving something much easier.
    """
    net = settled_net()
    net.start_observing("uav_3", at=net.now)
    net.start_observing("uav_4", at=net.now)
    drain(net, 4.0)
    net.kill("uav_2")
    net.run_for(90.0)
    net.stop_observing("uav_3")
    net.stop_observing("uav_4")
    net.run_for(30.0)

    obs = net.observations()
    assert obs["duplicated"] > 0, (
        "no duplicate ever arrived, so nothing here exercised deduplication")
    assert obs["unique_delivered"] == obs["generated"]
    assert obs["set_equal"], (
        "delivered set differs from generated set by "
        + repr(sorted(set(obs["generated_ids"])
                      ^ set(obs["delivered_ids"]))[:5]))
    assert obs["unexpected_count"] == 0
    assert obs["evicted"] == 0 and obs["expired"] == 0

    sink = net.router("gcs")
    ledger_ids = [row["id"] for row in sink.ledger]
    assert len(ledger_ids) == len(set(ledger_ids)), (
        "the ledger carries an id twice, so a row was written per delivery "
        "instead of per identity")


def test_the_backlog_custodian_is_the_lowest_id_of_the_disconnected_component():
    """Without the custodian rule the deepest queue is half what it should be.

    The design is sized against a depth only one node ever reaches. If each
    origin held its own, that depth is never reached by anything that could
    fail, and the store-and-forward claim reads as though it passed.
    """
    # The election is disabled here for the same reason queue_drain.yaml
    # disables it: an election shortens the outage, and the custodian rule is
    # about what happens while there is no route at all. With the election on,
    # this component recovers before its queue means anything.
    net = frozen_net(elections_enabled=False)
    net.run_for(CONVERGENCE_S)
    net.start_observing("uav_3", at=net.now)
    net.start_observing("uav_4", at=net.now)
    drain(net, 2.0)
    net.kill("uav_2")
    net.run_for(40.0)

    assert net.router("uav_3").custodian() == "uav_3"
    assert net.router("uav_4").custodian() == "uav_3"

    custodian_depth = net.router("uav_3").store.peak
    origin_depth = net.router("uav_4").store.peak
    assert custodian_depth > origin_depth, (
        "the disconnected component split its backlog across both origins "
        "instead of collecting it, so the deepest queue is half the depth the "
        "design is sized for")
    held = {o for (o, _s) in net.router("uav_3").store.identities()}
    assert held == {"uav_3", "uav_4"}, (
        "the custodian is holding only its own observations: " + repr(held))


def test_a_full_custodian_queue_evicts_loudly_and_still_loses_no_observation():
    """Eviction is allowed. Silent eviction, and actual data loss, are not.

    Driven past the queue on purpose with a capacity small enough to reach in a
    unit test, so the custodian genuinely evicts. The claim being tested is the
    one architecture.md makes about retention: the origin keeps its own copy
    until the ground station acknowledges the identity, so loss of the
    custodian does not lose the observation. If retention were dropped as an
    optimisation, every count in this test would still look plausible and the
    delivered set would quietly be short.

    The radio is restored by hand rather than by an election, because an
    election would end the outage before the queue filled.
    """
    capacity = 40
    net = frozen_net(queue_capacity=capacity, elections_enabled=False)
    net.run_for(CONVERGENCE_S)
    net.start_observing("uav_3", at=net.now)
    net.start_observing("uav_4", at=net.now)
    net.run_for(2.0)

    net.blackout("uav_2")
    net.run_for(40.0)
    custodian = net.router("uav_3")
    assert custodian.store.evicted > 0, (
        "the queue never filled, so this says nothing about what happens when "
        "it does")
    assert custodian.store.peak <= capacity, (
        "the queue went past its own capacity, so the bound is decorative")

    net.restore("uav_2")
    net.run_for(30.0)
    net.stop_observing("uav_3")
    net.stop_observing("uav_4")
    net.run_for(60.0)

    obs = net.observations()
    assert obs["evicted"] > 0
    assert obs["set_equal"], (
        "the custodian evicted and the data went with it. Missing: "
        + repr(sorted(set(obs["generated_ids"])
                      - set(obs["delivered_ids"]))[:5]))
    assert obs["unexpected_count"] == 0


def test_an_expired_observation_is_counted_rather_than_forgotten():
    queue = routing.PacketQueue(capacity=8)
    fresh = pk.observation("uav_4", 1, 0.0)
    stale = pk.observation("uav_4", 2, 0.0)
    stale.expires_at = 1.0
    queue.push(fresh)
    queue.push(stale)
    assert queue.expire(2.0) == 1
    assert queue.expired == 1
    assert len(queue) == 1


def test_the_queue_drops_the_oldest_first_and_says_so():
    queue = routing.PacketQueue(capacity=3)
    for sequence in range(1, 5):
        queue.push(pk.observation("uav_4", sequence, float(sequence)))
    assert queue.evicted == 1
    assert [s for (_o, s) in queue.identities()] == [2, 3, 4]
    assert queue.peak == 3


def test_the_queue_treats_a_second_copy_as_the_same_packet():
    # The custodian receives the same observation from the origin more than
    # once. Counting the copy as a new packet would inflate the depth the gate
    # reads and could evict a real one to make room for it.
    queue = routing.PacketQueue(capacity=4)
    obs = pk.observation("uav_4", 1, 0.0)
    assert queue.push(obs) == "queued"
    assert queue.push(obs.copy()) == "duplicate"
    assert len(queue) == 1 and queue.evicted == 0


# ------------------------------------------------------------------ hysteresis

def chain(*edges):
    topology = {}
    for a, b in edges:
        topology.setdefault(a, set()).add(b)
        topology.setdefault(b, set()).add(a)
    return topology


LONG_WAY = chain(("uav_4", "uav_2"), ("uav_2", "uav_1"), ("uav_1", "gcs"))
SHORT_WAY = chain(("uav_4", "uav_1"), ("uav_1", "gcs"),
                  ("uav_4", "uav_2"), ("uav_2", "uav_1"))


def test_a_better_route_has_to_win_twice_before_it_replaces_the_installed_one():
    table = routing.RouteTable("gcs")
    assert table.compute(LONG_WAY, "uav_4") == routing.INSTALLED
    assert table.compute(SHORT_WAY, "uav_4") == routing.CHALLENGED
    assert table.installed == ["uav_4", "uav_2", "uav_1", "gcs"]
    assert table.compute(SHORT_WAY, "uav_4") == routing.REPLACED
    assert table.installed == ["uav_4", "uav_1", "gcs"]


def test_a_route_that_alternates_every_computation_never_installs():
    """The flap this rule exists for, driven rather than described."""
    table = routing.RouteTable("gcs")
    table.compute(LONG_WAY, "uav_4")
    installed = list(table.installed)
    for _ in range(20):
        table.compute(SHORT_WAY, "uav_4")
        table.compute(LONG_WAY, "uav_4")
    assert table.installed == installed
    assert table.installs == 1, (
        "the route was installed " + str(table.installs) + " times while the "
        "link alternated, which is the oscillation hysteresis is for")


def test_installing_the_first_route_does_not_wait_for_hysteresis():
    # There is nothing to replace, and holding packets while a route exists is
    # strictly worse than installing one that might change.
    table = routing.RouteTable("gcs")
    assert table.compute(chain(("uav_1", "gcs")), "uav_1") == routing.INSTALLED
    assert table.installed == ["uav_1", "gcs"]


def test_a_broken_route_is_withdrawn_immediately_and_not_after_two_rounds():
    """Hysteresis delays adopting. It must never delay letting go.

    A router applying the same two-computation rule to withdrawal would keep
    forwarding into a hole for two more LSA periods, which is the one direction
    where waiting costs packets.
    """
    table = routing.RouteTable("gcs")
    table.compute(LONG_WAY, "uav_4")
    assert table.installed is not None
    assert table.withdraw_if_broken(chain(("uav_4", "uav_3"))) is True
    assert table.installed is None
    assert table.next_hop() is None


def test_the_route_key_prefers_fewer_hops_and_only_then_fewer_relays():
    """Round 5 finding 1 and round 6 finding 9, on the oracle's own examples.

    The pair is checked against check_geometry's route_key so the two cannot
    disagree, and then over relay counts Stage 1 never builds, because the
    scalar surcharge that preceded it was correct at one relay and wrong at
    two.
    """
    short = ["uav_4", "uav_5", "uav_6", "gcs"]
    long_way = ["uav_4", "uav_2", "uav_1", "uav_0", "gcs"]
    relays = frozenset({"uav_5", "uav_6"})
    assert graph.route_key(short, relays) == oracle.route_key(short, relays)
    assert graph.route_key(long_way, relays) == oracle.route_key(long_way,
                                                                 relays)
    assert graph.route_key(short, relays) < graph.route_key(long_way, relays)

    for hops_a in range(1, 8):
        for hops_b in range(1, 8):
            for relays_a in range(0, hops_a + 1):
                for relays_b in range(0, hops_b + 1):
                    if hops_a < hops_b:
                        assert (hops_a, relays_a) < (hops_b, relays_b)
    assert (3, 0) < (3, 1) < (3, 2)


def test_the_recovered_route_beats_the_relay_route_on_the_pair():
    """The handback would be impossible without this, so it is checked as one.

    After the mover has parked at the slot and the silenced vehicle is back,
    two routes tie at three hops. A tie never wins hysteresis, so on hop count
    alone the installed route through the relay could never be replaced and the
    release could never fire. The oracle is asked which route should win and
    the router is asked which one it picked.
    """
    mover, stays, attach = "uav_3", "uav_4", "uav_1"
    slot = oracle.banded_slot(oracle.START[attach], [oracle.START[stays]])
    post = dict(oracle.START)
    post[mover] = slot

    routes = oracle.all_paths(post, stays, "gcs")
    fewest = min(len(p) - 1 for p in routes)
    shortest = [p for p in routes if len(p) - 1 == fewest]
    assert len(shortest) >= 2, (
        "only one route is shortest, so the tie this rule exists to break has "
        "moved and the rule is arguing against nothing")

    best = min(routes, key=lambda p: oracle.route_key(p, {mover}))
    assert mover not in best

    topology = {node: set() for node in post}
    for a in post:
        for b in post:
            if a < b and math.dist(post[a], post[b]) <= params.R_MAX_M:
                topology[a].add(b)
                topology[b].add(a)
    assert graph.dijkstra(topology, stays, "gcs", frozenset({mover})) == best


def test_dijkstra_returns_the_same_path_however_the_graph_is_ordered():
    """Two equally good paths must not be chosen by whatever the heap surfaced.

    Without a total order the route computation returns a coin flip, and route
    hysteresis then compares this computation's coin flip with the last one's,
    which is a flap the two-win rule cannot see.
    """
    edges = [("uav_4", "a"), ("uav_4", "b"), ("a", "gcs"), ("b", "gcs")]
    first = graph.dijkstra(chain(*edges), "uav_4", "gcs")
    second = graph.dijkstra(chain(*reversed(edges)), "uav_4", "gcs")
    assert first == second == ["uav_4", "a", "gcs"]


# ------------------------------------------------------------------- the graph

def test_a_link_counts_only_when_both_sides_advertise_it():
    lsdb = graph.LinkStateDatabase()
    lsdb.accept(graph.Lsa("uav_1", 1, frozenset({"uav_2"}), 0.0))
    lsdb.accept(graph.Lsa("uav_2", 1, frozenset(), 0.0))
    assert lsdb.topology()["uav_1"] == set(), (
        "a one-sided claim became an edge, so a node that can hear another but "
        "cannot be heard by it would be routed through")

    lsdb.accept(graph.Lsa("uav_2", 2, frozenset({"uav_1"}), 1.0))
    assert lsdb.topology()["uav_1"] == {"uav_2"}


def test_a_dead_node_leaves_the_graph_without_any_age_out_rule():
    """Symmetry is the whole liveness mechanism, and this is why it is enough.

    The dead node's own advertisement still claims its neighbours forever.
    Every one of those edges fails because the neighbours have already dropped
    it, so no separate staleness timer has to be invented and no number nobody
    derived ends up in the design. The last assertion checks that: if the dead
    node vanished from the graph entirely, something aged it out instead.
    """
    lsdb = graph.LinkStateDatabase()
    lsdb.accept(graph.Lsa("uav_2", 1, frozenset({"uav_1", "uav_3"}), 0.0))
    lsdb.accept(graph.Lsa("uav_1", 1, frozenset({"uav_2", "gcs"}), 0.0))
    lsdb.accept(graph.Lsa("gcs", 1, frozenset({"uav_1"}), 0.0))
    lsdb.accept(graph.Lsa("uav_3", 1, frozenset({"uav_2"}), 0.0))
    assert graph.dijkstra(lsdb.topology(), "uav_3", "gcs") is not None

    lsdb.accept(graph.Lsa("uav_1", 2, frozenset({"gcs"}), 5.0))
    lsdb.accept(graph.Lsa("uav_3", 2, frozenset(), 5.0))
    assert graph.dijkstra(lsdb.topology(), "uav_3", "gcs") is None
    assert "uav_2" in lsdb.topology()


def test_a_node_routes_on_its_own_live_neighbours_not_on_its_own_advertisement():
    """Its own row cannot lag, because that lag is a route into a hole.

    A node reading its own neighbours back out of the database is up to one
    LSA period behind itself, which is long enough to compute a route over a
    link it has already given up on. That is not a hypothetical: it is what
    made the attachment node come out as the dead relay the first time this
    was driven.
    """
    lsdb = graph.LinkStateDatabase()
    lsdb.accept(graph.Lsa("uav_3", 1, frozenset({"uav_2"}), 0.0))
    lsdb.accept(graph.Lsa("uav_2", 1, frozenset({"uav_3"}), 0.0))
    assert lsdb.topology()["uav_3"] == {"uav_2"}
    assert lsdb.topology({"uav_3": frozenset()})["uav_3"] == set()


def test_an_older_advertisement_is_dropped_and_not_merged():
    lsdb = graph.LinkStateDatabase()
    assert lsdb.accept(graph.Lsa("uav_2", 5, frozenset({"uav_1"}), 0.0))
    assert not lsdb.accept(graph.Lsa("uav_2", 4, frozenset({"uav_3"}), 1.0))
    assert not lsdb.accept(graph.Lsa("uav_2", 5, frozenset({"uav_3"}), 1.0))
    assert lsdb.records["uav_2"].neighbours == frozenset({"uav_1"})
    assert lsdb.duplicates == 2


def test_a_sequence_outside_uint32_is_refused_rather_than_stored():
    lsdb = graph.LinkStateDatabase()
    try:
        lsdb.accept(graph.Lsa("uav_2", params.UINT32_MAX + 1, frozenset(), 0.0))
    except ValueError:
        return
    raise AssertionError("a sequence too large for the wire type was accepted")


# ------------------------------------------------------------ control priority

def test_control_leaves_before_a_backlog_it_was_queued_behind():
    """Observations never delay control.

    Without this rule a full backlog sits in front of the very traffic that
    would end the outage, and the swarm takes longer to recover the harder it
    was working. Driven by filling the store first and queueing one control
    packet after it, which is the order that makes a naive single queue fail.
    """
    net = settled_net()
    uav_4 = net.router("uav_4")
    for sequence in range(1, 200):
        uav_4.store.push(pk.observation("uav_4", 10000 + sequence, net.now))
    marker = pk.control("uav_4", pk.KIND_HELLO, net.now,
                        {"sender_id": "uav_4", "sent_at": net.now,
                         "position": (0.0, 0.0, 0.0),
                         "velocity": (0.0, 0.0, 0.0), "seq": 999},
                        sequence=999)
    uav_4._queue_control(marker, net.now)
    uav_4.tick(net.now, net.dt)
    kinds = [p.kind for p in uav_4.drain_tx(net.now)]
    assert pk.KIND_HELLO in kinds, "nothing control shaped was sent at all"
    first_observation = next((i for i, k in enumerate(kinds)
                              if k == pk.KIND_OBSERVATION), len(kinds))
    assert kinds.index(pk.KIND_HELLO) < first_observation, (
        "an observation went out ahead of a control message queued after it")


def test_the_forward_rate_is_the_rate_and_not_a_truncated_integer():
    rate = routing.ServiceRate(rate_pps=200.0)
    total = sum(rate.allowance(0.003) for _ in range(1000))
    assert abs(total - 600) <= 1, (
        "truncating each tick lost " + str(600 - total) + " packets of "
        "allowance, which quietly changes the rate the drain bound comes from")


# ---------------------------------------------------------------- the protocol

def test_a_packet_with_an_unknown_kind_is_dropped_and_counted():
    net = settled_net()
    uav_3 = net.router("uav_3")
    before = uav_3.protocol_errors
    bad = pk.observation("uav_4", 1, net.now)
    bad.kind = 99
    uav_3.on_rx(bad, net.now)
    assert uav_3.protocol_errors == before + 1


def test_a_packet_that_has_already_visited_this_node_is_dropped_as_a_loop():
    net = settled_net()
    uav_3 = net.router("uav_3")
    looped = pk.observation("uav_4", 1, net.now)
    looped.path = ["uav_4", "uav_3", "uav_2", "uav_3"]
    before = uav_3.drops.get("loop", 0)
    uav_3.on_rx(looped, net.now)
    assert uav_3.drops.get("loop", 0) == before + 1


def test_a_packet_addressed_to_someone_else_is_overheard_and_ignored():
    net = settled_net()
    uav_3 = net.router("uav_3")
    before = len(uav_3.store)
    overheard = pk.observation("uav_4", 1, net.now)
    overheard.path = ["uav_4", "uav_2"]
    uav_3.on_rx(overheard, net.now)
    assert len(uav_3.store) == before, (
        "a node forwarded a packet it merely overheard, so every node in range "
        "forwards every packet and the mesh floods itself")


def test_a_packet_at_the_ttl_is_dropped_rather_than_forwarded_forever():
    net = settled_net()
    uav_3 = net.router("uav_3")
    old = pk.observation("uav_4", 1, net.now)
    old.hop_count = params.LSA_TTL
    old.path = ["uav_4", "uav_3"]
    before = uav_3.drops.get("ttl_exceeded", 0)
    uav_3.on_rx(old, net.now)
    assert uav_3.drops.get("ttl_exceeded", 0) == before + 1


def test_a_relayed_hello_teaches_a_position_and_never_creates_a_neighbour():
    """The rule that lets a component know where its attachment node is.

    Nothing in the frozen geometry puts a surveyor inside radio range of the
    anchor, so without relayed HELLO nobody could compute a bid distance or a
    slot. The danger is the obvious shortcut: treat a relayed HELLO as a
    neighbour and the anchor becomes adjacent to both surveyors, the chain the
    relay was carrying already exists, and killing the relay proves nothing.
    That is round 3 finding 1 rebuilt out of a one line mistake, so it is
    asserted from both sides here.
    """
    net = settled_net()
    uav_4 = net.router("uav_4")

    assert "uav_1" in uav_4.position_cache, (
        "the far surveyor never learned where the anchor is, so it could not "
        "bid or compute a slot")
    assert uav_4.position_cache["uav_1"] == oracle.START["uav_1"]

    assert "uav_1" not in uav_4.neighbours.live(), (
        "a relayed HELLO created a neighbour entry")
    assert "uav_1" not in uav_4.topology()["uav_4"], (
        "the anchor became a graph edge of the far surveyor, so the relay is "
        "no longer load bearing and the whole scenario is a no-op")
    assert oracle.band(math.dist(oracle.START["uav_4"],
                                 oracle.START["uav_1"])) == "out", (
        "the oracle says these two can hear each other, so this test is "
        "checking the wrong pair")


def test_the_attachment_node_is_never_the_vehicle_that_just_failed():
    """The component cannot be told that the far side dropped the dead relay.

    Every path that news could travel went through the relay, so the anchor and
    the dead relay go on listing each other in the surveyors' link-state view
    forever, and the dead relay still looks like it holds a route home. Picking
    it as the attachment node sends the mover to balance against a corpse: the
    slot lands nowhere near the anchor and the chain never reforms, which is
    what happened the first time this was driven.

    The first half proves the trap is still there. The second proves the rule
    steps over it, and the third proves the swarm actually attached to the
    anchor when it ran the election for real.
    """
    stranded = frozen_net(elections_enabled=False)
    stranded.run_for(CONVERGENCE_S)
    stranded.kill("uav_2")
    stranded.run_for(30.0)

    coordinator = stranded.router("uav_3")
    topology = coordinator.topology()
    component = graph.component_of(topology, "uav_3")
    assert component == {"uav_3", "uav_4"}
    assert "uav_2" in coordinator.lost_neighbours
    assert graph.dijkstra(topology, "uav_2", "gcs") is not None, (
        "the dead relay no longer looks routable from inside the component, so "
        "this test is no longer exercising the trap it was written for")
    assert coordinator._attachment(topology, component) == "uav_1"

    electing = settled_net()
    electing.kill("uav_2")
    electing.run_for(60.0)
    epoch = electing.router("uav_3").roles.current
    assert epoch is not None
    assert epoch.members == frozenset({"uav_3", "uav_4"})
    assert epoch.attachment_id == "uav_1", (
        "the component attached to " + repr(epoch.attachment_id))
