"""The election, driven through failures rather than read back.

The question this file has to answer is not "does pick_winner sort correctly".
It is: when a vehicle dies, does a different one take the role, does the route
come back, does the swarm stop, and can anything make it change its mind after
it has settled. So most of these drive the whole network through an event and
assert on the outcome, and the few that poke the machine directly are the ones
about refusing a message, which cannot be produced by a healthy network at all.
"""

import math

from conftest import CONVERGENCE_S, frozen_net, settled_net

from uavx_comms import election, params, slots

import check_geometry as oracle


def oracle_relay_kill():
    """What check_geometry.py says relay_kill has to produce.

    Derived here rather than written down, so a test cannot go on asserting a
    slot this repository has stopped using.
    """
    attach = "uav_1"
    candidates = sorted(
        ("uav_3", "uav_4"),
        key=lambda n: (math.dist(oracle.START[n], oracle.START[attach]), n))
    mover, stays = candidates
    slot = oracle.banded_slot(oracle.START[attach], [oracle.START[stays]])
    moved = {k: v for k, v in oracle.START.items() if k != "uav_2"}
    moved[mover] = slot
    return {
        "attachment": attach,
        "mover": mover,
        "stays": stays,
        "slot": slot,
        "restored": oracle.path_to(moved, stays, "gcs"),
        "flight_m": math.dist(oracle.START[mover], slot),
    }


def run_relay_kill(seed=7, seconds=90.0):
    net = settled_net(seed=seed)
    net.start_observing("uav_3", at=net.now)
    net.start_observing("uav_4", at=net.now)
    net.run_for(4.0)
    killed_at = net.now
    net.kill("uav_2")
    net.run_for(seconds)
    return net, killed_at


# ------------------------------------------------- the relay dies and is replaced

def test_killing_the_relay_moves_the_role_and_reforms_the_route():
    """The 20 percent role row and the 15 percent recovery row, as one outcome.

    Every number compared here comes from the geometry oracle. The mover, the
    slot, the two hops and the restored path are all derived, so a change to
    the frozen topology moves this test with it instead of leaving it asserting
    last month's answer.
    """
    expected = oracle_relay_kill()
    net, _ = run_relay_kill()

    assert net.relay_holders() == [expected["mover"]]
    assert net.role_of(expected["stays"]) == election.ROLE_SURVEY
    assert net.role_of("uav_1") == election.ROLE_GCS_ANCHOR, (
        "the anchor took the relay role, which it is never eligible for")

    mover_at = net.position(expected["mover"])
    assert math.dist(mover_at, expected["slot"]) < 1.0, (
        "the mover parked at " + repr(tuple(round(v, 1) for v in mover_at))
        + " and the oracle puts the slot at "
        + repr(tuple(round(v, 1) for v in expected["slot"])))

    assert net.installed_route(expected["stays"]) == expected["restored"]
    assert expected["mover"] in net.installed_route(expected["stays"])

    decision = net.router(expected["mover"]).last_slot
    assert decision.anchor_hop_m <= params.USED_LINK_MAX_M
    assert decision.work_hop_m <= params.USED_LINK_MAX_M
    assert decision.band_reserved


def test_the_swarm_does_not_elect_before_it_has_ever_had_a_route():
    """Every node is disconnected for the first seconds of any run.

    A machine that opened an epoch on that would elect a relay before the graph
    had converged, and would do it on every launch. The condition is not "no
    route", it is "lost a route it used to have".
    """
    net = frozen_net()
    net.run_for(params.NEIGHBOUR_TIMEOUT_S + params.LSA_PERIOD_S)
    for node in ("uav_3", "uav_4"):
        assert net.router(node).roles.current is None, (
            node + " opened an epoch during startup convergence")
    net.run_for(CONVERGENCE_S)
    # uav_2 is the relay by configuration in the frozen scenarios, and it still
    # is. What must not have happened is a second one being elected.
    assert net.relay_holders() == ["uav_2"]
    for node in net.live_ids():
        assert net.router(node).roles.highest_epoch == 0


def test_a_healthy_swarm_never_opens_an_epoch():
    net = settled_net()
    net.run_for(60.0)
    for node in net.live_ids():
        assert net.router(node).roles.highest_epoch == 0, (
            node + " opened an epoch with nothing wrong")
    assert net.role_of("uav_2") == election.ROLE_RELAY


def test_the_role_settles_once_and_stays_settled():
    """Recovery is not the end of the test. Staying put is.

    An election that keeps reopening still passes any assertion taken at the
    moment the route returns, so this runs on for another two minutes and
    checks the epoch number and the role change count have not moved.
    """
    net, _ = run_relay_kill(seconds=60.0)
    mover = net.relay_holders()[0]
    epoch = net.router(mover).roles.current.number
    changes = {n: net.router(n).roles.role_changes for n in net.live_ids()}

    net.run_for(120.0)
    assert net.relay_holders() == [mover]
    assert net.router(mover).roles.current.number == epoch
    assert {n: net.router(n).roles.role_changes
            for n in net.live_ids()} == changes, (
        "a role changed again after the swarm had recovered")
    assert epoch == 1, (
        "the swarm reached epoch " + str(epoch) + " for one failure, so it "
        "reopened the election at least once")


def test_the_elected_relay_dying_leaves_one_member_reporting_no_feasible_slot():
    """A component of one has nobody to relay for, and says so.

    The elected relay is killed in turn, which leaves a single vehicle. It is
    both the only eligible mover and the only member that would stay, so the
    balance rule has no work to solve against. Electing itself would park the
    last surveyor in a relay slot with nothing behind it, which is worse than
    reporting the condition. The epoch still advances, so the swarm has a
    record of having tried.
    """
    net, _ = run_relay_kill(seconds=70.0)
    first = net.relay_holders()[0]
    survivor = "uav_4" if first == "uav_3" else "uav_3"

    net.kill(first)
    net.run_for(90.0)

    machine = net.router(survivor).roles
    assert net.role_of(survivor) == election.ROLE_SURVEY
    assert machine.highest_epoch == 2, (
        "the second failure reused the first epoch instead of opening one")
    reports = [what for _at, what in net.router(survivor).reports]
    assert "RELAY_INFEASIBLE" in reports, (
        "the last vehicle went quiet about being unable to reconnect: "
        + repr(reports))


def test_the_role_is_reassigned_when_the_elected_relay_itself_fails():
    """Reassignment is a different thing from assignment, and nothing else drives it.

    Stage 1 never builds a component that still has two eligible members after
    the elected relay dies, so this constructs one. That is deliberate: round 6
    finding 9 is about a rule that was only ever checked in the one shape the
    frozen scenarios produce, and was wrong in the next shape up.

    The extra vehicle sits inside full range of one surveyor and beyond maximum
    range of everything on the far side of the relay, so the placement rule
    still holds for every pair it introduces.
    """
    from uavx_comms import link, pure_net
    from conftest import FROZEN_ROLES

    positions = dict(oracle.START)
    positions["uav_5"] = (475.0, -75.0, 80.0)
    for a in positions:
        for b in positions:
            if a < b:
                separation = math.dist(positions[a], positions[b])
                assert link.placement_holds(separation), (
                    a + " to " + b + " sits in the undecided gap, so this "
                    "topology cannot give a deterministic answer")

    roles = dict(FROZEN_ROLES)
    roles["uav_5"] = election.ROLE_SURVEY
    net = pure_net.PureNet(positions, roles=roles, seed=7)
    net.run_for(CONVERGENCE_S)

    net.kill("uav_2")
    net.run_for(90.0)
    first = net.relay_holders()
    assert first == ["uav_3"], repr(first)

    net.kill("uav_3")
    net.run_for(120.0)
    second = net.relay_holders()
    assert second == ["uav_4"], (
        "the role was not reassigned after the elected relay failed: "
        + repr(second))
    assert net.router("uav_4").roles.current.number == 2
    assert net.router("uav_4").roles.current.owner == "uav_5", (
        "the owner of the second epoch is the relay again")
    assert net.installed_route("uav_5") is not None, (
        "the survivor never got its route back")


# ------------------------------------------------------ settling without flapping

def test_two_equally_good_candidates_settle_on_one_winner_and_stay_there():
    """The tie is deliberate, and the result has to be identical everywhere.

    Both candidates are placed at exactly the same distance from the attachment
    node, so distance decides nothing and the tie-break carries the whole
    result. The failure this catches is an implementation where the winner
    depends on which bid arrived first: it would look correct on the frozen
    geometry, where the two candidates differ, and would disagree between nodes
    the moment they did not.
    """
    positions = dict(oracle.START)
    x, y, _z = oracle.START["uav_2"]
    positions["uav_3"] = (x + 120.0, y + 60.0, 50.0)
    positions["uav_4"] = (x + 120.0, y - 60.0, 50.0)
    anchor = oracle.START["uav_1"]
    assert abs(math.dist(positions["uav_3"], anchor)
               - math.dist(positions["uav_4"], anchor)) < 1e-9, (
        "the two candidates are not actually equidistant, so this is not a tie")

    from uavx_comms import pure_net
    from conftest import FROZEN_ROLES
    net = pure_net.PureNet(positions, roles=dict(FROZEN_ROLES), seed=7)
    net.run_for(CONVERGENCE_S)
    net.kill("uav_2")
    net.run_for(90.0)

    holders = net.relay_holders()
    assert len(holders) == 1, (
        "a tie produced " + repr(holders) + " relays, so the two candidates "
        "did not agree")
    assert holders == ["uav_3"], (
        "the tie was not broken by lowest id, so the winner depends on "
        "something that is not the same on every node")

    winners = {net.router(n).roles.current.winner
               for n in ("uav_3", "uav_4")}
    assert winners == {"uav_3"}, (
        "the two members disagree about who won: " + repr(winners))

    net.run_for(120.0)
    assert net.relay_holders() == ["uav_3"]
    assert net.router("uav_3").roles.role_changes == 1


def test_the_winner_does_not_depend_on_the_order_bids_arrive_in():
    """The same bids, delivered both ways round, decide the same way."""
    bids = {"uav_4": 300.0, "uav_3": 300.0}
    assert election.pick_winner(bids) == "uav_3"
    assert election.pick_winner(dict(reversed(list(bids.items())))) == "uav_3"
    assert election.pick_winner({"uav_4": 299.0, "uav_3": 300.0}) == "uav_4", (
        "the id was used before the distance, so the nearer candidate lost")


def test_a_bid_that_arrives_after_the_window_does_not_reopen_the_decision():
    """A late bid that could reopen a settled election is a flap with a timer."""
    machine = election.RoleMachine("uav_3")
    machine.open_election(0.0, frozenset({"uav_3", "uav_4"}), "uav_1", None)
    machine.on_message({"kind": election.BID, "epoch": 1, "node_id": "uav_3",
                        "distance": 320.0}, 0.1)
    assign = machine.close_window(params.ELECTION_WINDOW_S + 0.1)
    assert assign and assign[0]["winner"] == "uav_3"
    machine.on_message(dict(assign[0]), params.ELECTION_WINDOW_S + 0.2)

    outcome, _ = machine.on_message(
        {"kind": election.BID, "epoch": 1, "node_id": "uav_4",
         "distance": 1.0}, params.ELECTION_WINDOW_S + 0.3)
    assert outcome == election.WINDOW_CLOSED
    assert machine.current.winner == "uav_3"


def test_the_window_is_actually_waited_out():
    machine = election.RoleMachine("uav_3")
    machine.open_election(0.0, frozenset({"uav_3", "uav_4"}), "uav_1", None)
    machine.on_message({"kind": election.BID, "epoch": 1, "node_id": "uav_3",
                        "distance": 320.0}, 0.1)
    assert machine.close_window(params.ELECTION_WINDOW_S - 0.1) == [], (
        "the coordinator decided before the window elapsed, so a slower "
        "member's bid could never be counted")
    assert machine.close_window(params.ELECTION_WINDOW_S) != []


def test_a_second_assign_for_the_same_epoch_cannot_move_the_role():
    """Assignment is write once. Even a coordinator that changed its mind loses."""
    machine = election.RoleMachine("uav_4")
    first = {"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
             "owner": "uav_4", "slot": (0.0, 0.0, 75.0), "members": ["uav_3",
                                                                     "uav_4"]}
    machine.on_message(dict(first), 1.0)
    assert machine.current.winner == "uav_3"

    second = dict(first)
    second["winner"] = "uav_4"
    outcome, _ = machine.on_message(second, 1.1)
    assert outcome == election.ALREADY_ASSIGNED
    assert machine.current.winner == "uav_3"
    assert machine.role == election.ROLE_SURVEY, (
        "the node took the relay role from a contradictory second assignment")


def test_a_message_for_an_older_epoch_is_refused():
    machine = election.RoleMachine("uav_4")
    machine.on_message({"kind": election.ASSIGN, "epoch": 4, "winner": "uav_3",
                        "owner": "uav_4", "slot": None, "members": []}, 1.0)
    outcome, _ = machine.on_message(
        {"kind": election.ASSIGN, "epoch": 3, "winner": "uav_4",
         "owner": "uav_3", "slot": None, "members": []}, 1.1)
    assert outcome == election.STALE_EPOCH
    assert machine.current.winner == "uav_3"


def test_the_anchor_never_bids():
    machine = election.RoleMachine("uav_1", election.ROLE_GCS_ANCHOR)
    outcome, replies = machine.on_message(
        {"kind": election.ELECTION, "epoch": 1, "coordinator": "uav_3",
         "attachment_id": "uav_1", "members": ["uav_1", "uav_3"],
         "slot": None}, 0.0, distance_to_attachment=0.0)
    assert outcome == election.NOT_ELIGIBLE
    assert replies == []


# ------------------------------------------------------------------ ownership

def test_the_epoch_owner_is_never_the_relay_and_is_carried_not_recomputed():
    """Round 6 finding 7, as an outcome.

    The lowest id member of the component is also the member the election sends
    away, so an owner recomputed from the component would be the relay, every
    route it evaluated would begin at the relay, and the release condition
    could never be true. Ownership is fixed at the assignment and survives the
    component merging back.
    """
    net, _ = run_relay_kill(seconds=70.0)
    mover = net.relay_holders()[0]
    stays = "uav_4" if mover == "uav_3" else "uav_3"

    for node in (mover, stays):
        epoch = net.router(node).roles.current
        assert epoch.owner == stays, (
            node + " thinks the owner is " + repr(epoch.owner))
        assert epoch.owner != epoch.winner

    assert election.epoch_owner(frozenset({"uav_3", "uav_4"}), "uav_3") == "uav_4"
    assert election.epoch_owner(frozenset({"uav_3", "uav_4"}), "uav_4") == "uav_3"
    assert election.epoch_owner(frozenset({"uav_3"}), "uav_3") is None, (
        "a component of one produced an owner, so a single stranded vehicle "
        "would elect itself and then own its own handback")


def test_a_release_from_anyone_but_the_owner_is_refused():
    """A stale release is the outage the whole handback section exists to stop.

    Two nodes each believing they own an epoch could send contradictory
    releases, so the sender is checked rather than trusted. Both of the
    plausible impostors are tried: the relay itself, and the node that opened
    the epoch and then lost ownership to the election result.
    """
    machine = election.RoleMachine("uav_3")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": (0.0, 0.0, 75.0),
                        "members": ["uav_3", "uav_4"]}, 1.0)
    assert machine.role == election.ROLE_RELAY

    for impostor in ("uav_3", "uav_1", "gcs"):
        outcome, _ = machine.on_message(
            {"kind": election.RELEASE, "epoch": 1, "sender_id": impostor}, 2.0)
        assert outcome == election.NOT_OWNER, impostor
        assert machine.role == election.ROLE_RELAY, (
            "a release from " + impostor + " tore down the relay")

    outcome, _ = machine.on_message(
        {"kind": election.RELEASE, "epoch": 1, "sender_id": "uav_4"}, 3.0)
    assert outcome == election.ACCEPTED
    assert machine.role == election.ROLE_SURVEY


def test_a_lease_renewal_from_anyone_but_the_owner_is_refused():
    machine = election.RoleMachine("uav_3")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": None,
                        "members": ["uav_3", "uav_4"]}, 0.0)
    outcome, _ = machine.on_message(
        {"kind": election.LEASE, "epoch": 1, "sender_id": "uav_3",
         "node_id": "uav_3"}, 1.0)
    assert outcome == election.NOT_OWNER
    assert machine.current.lease_expires_at == params.ROLE_LEASE_S, (
        "a node renewed its own lease, so a relay could hold the role forever "
        "with nobody responsible for it")


def test_an_unrenewed_lease_expires_and_the_vehicle_goes_back_to_surveying():
    """If the owner dies, nothing takes over. The lease just runs out.

    Losing a vehicle's time is recoverable. Tearing down a working link on a
    message from a node that no longer owns the decision is not, which is why
    there is no takeover at all.
    """
    machine = election.RoleMachine("uav_3")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": (0.0, 0.0, 75.0),
                        "members": ["uav_3", "uav_4"]}, 0.0)
    assert machine.role == election.ROLE_RELAY

    assert machine.check_lease(params.ROLE_LEASE_S - 0.1) is False
    assert machine.role == election.ROLE_RELAY, (
        "the role reverted before the lease had run out")

    assert machine.check_lease(params.ROLE_LEASE_S + 0.1) is True
    assert machine.role == election.ROLE_SURVEY
    assert machine.slot_target is None


def test_a_renewed_lease_does_not_expire():
    machine = election.RoleMachine("uav_3")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": None,
                        "members": ["uav_3", "uav_4"]}, 0.0)
    for at in range(1, 60, 2):
        machine.on_message({"kind": election.LEASE, "epoch": 1,
                            "sender_id": "uav_4", "node_id": "uav_3"},
                           float(at))
        assert machine.check_lease(float(at)) is False
    assert machine.role == election.ROLE_RELAY


# ------------------------------------------------------------ make before break

def owned_epoch_at_prepare():
    """An owner that has just been given a relay-free route, twice."""
    machine = election.RoleMachine("uav_4")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": None,
                        "members": ["uav_3", "uav_4"]}, 0.0)
    path = ["uav_4", "uav_2", "uav_1", "gcs"]
    assert machine.offer_path(path, 1.0) == [], (
        "the release was prepared on a single computation, so a path that "
        "appeared once as a radio came back would be enough")
    prepared = machine.offer_path(path, 2.0)
    assert prepared and prepared[0]["kind"] == election.PREPARE_RELEASE
    return machine, path


def test_the_release_needs_two_computations_then_an_acknowledgement():
    machine, path = owned_epoch_at_prepare()
    assert machine.current.prepared_wins >= params.ROUTE_HYSTERESIS_WINS
    assert machine.current.released is False, (
        "preparing a release released the relay, which is the second outage "
        "make before break exists to prevent")

    # An acknowledgement naming a different path is not the confirmation this
    # transaction is waiting for.
    assert machine.confirm("uav_4:12", ["uav_4", "uav_3", "uav_1", "gcs"],
                           3.0) == []
    assert machine.current.confirmed_at is None

    release = machine.confirm("uav_4:13", path, 4.0)
    assert release and release[0]["kind"] == election.RELEASE
    assert release[0]["sender_id"] == "uav_4"

    trace = machine.handback_trace()
    assert trace["prepared_path"] == path
    assert trace["epoch_owner"] == "uav_4"
    assert trace["confirmed_observation_id"] == "uav_4:13"
    assert trace["prepared_path_computations"] >= 2


def test_a_route_through_the_relay_is_never_offered_as_the_handback_path():
    machine = election.RoleMachine("uav_4")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": None,
                        "members": ["uav_3", "uav_4"]}, 0.0)
    through_relay = ["uav_4", "uav_3", "uav_1", "gcs"]
    assert machine.offer_path(through_relay, 1.0) == []
    assert machine.offer_path(through_relay, 2.0) == [], (
        "a path through the relay itself won hysteresis and was offered as "
        "the route to hand the vehicle back on")
    assert machine.current.prepared_at is None


def test_an_alternate_that_disappears_resets_the_count():
    machine = election.RoleMachine("uav_4")
    machine.on_message({"kind": election.ASSIGN, "epoch": 1, "winner": "uav_3",
                        "owner": "uav_4", "slot": None,
                        "members": ["uav_3", "uav_4"]}, 0.0)
    path = ["uav_4", "uav_2", "uav_1", "gcs"]
    machine.offer_path(path, 1.0)
    machine.offer_path(None, 2.0)
    assert machine.offer_path(path, 3.0) == [], (
        "the win count survived the route going away, so two appearances "
        "separated by an outage counted as two consecutive computations")


def test_an_unconfirmed_release_is_abandoned_and_the_relay_stays():
    """A swarm that keeps a vehicle parked is better off than one with no link."""
    machine, _path = owned_epoch_at_prepare()
    assert machine.abandon_stale_prepare(2.0 + params.STABILITY_WINDOW_S
                                         - 0.1) is False
    assert machine.abandon_stale_prepare(2.0 + params.STABILITY_WINDOW_S
                                         + 0.1) is True
    assert machine.current.released is False
    assert machine.current.prepared_at is None
    assert machine.current.open()


# ------------------------------------------------------------------- the slot

def test_the_slot_is_the_balance_point_the_geometry_oracle_derives():
    expected = oracle_relay_kill()
    decision = slots.solve(oracle.START[expected["attachment"]],
                           [oracle.START[expected["stays"]]],
                           live=[oracle.START[expected["stays"]]])
    assert decision.feasible()
    for got, want in zip(decision.slot, expected["slot"]):
        assert abs(got - want) < 1e-6
    assert abs(decision.anchor_hop_m - decision.work_hop_m) < 1e-6, (
        "the slot does not balance the two hops it has to carry")
    assert decision.anchor_hop_m <= params.USED_LINK_MAX_M


def test_the_slot_sits_in_the_reserved_band_whatever_the_work_altitude_is():
    """Separation is vertical so it does not depend on knowing where anyone drifted.

    Round 5 finding 4: raising the slot only when it happened to collide was a
    rule no accepted scenario ever exercised, so nothing proved an
    implementation would call it at all.
    """
    for altitude in (30.0, 40.0, 50.0, 60.0):
        decision = slots.solve((165.0, 0.0, 30.0),
                               [(400.0, -80.0, altitude)])
        assert decision.slot[2] >= params.RELAY_BAND_M
        assert decision.band_reserved


def test_a_slot_that_would_sit_on_a_flying_vehicle_is_raised():
    anchor = (0.0, 0.0, params.RELAY_BAND_M)
    work = [(200.0, 0.0, params.RELAY_BAND_M)]
    balanced = slots.banded_slot(anchor, work)
    decision = slots.solve(anchor, work, live=[balanced])
    assert decision.feasible()
    assert decision.slot[2] > params.RELAY_BAND_M
    assert decision.clearance_m >= params.SLOT_CLEARANCE_M


def test_relay_infeasible_when_no_altitude_clears_the_airspace():
    anchor = (0.0, 0.0, params.RELAY_BAND_M)
    work = [(200.0, 0.0, params.RELAY_BAND_M)]
    balanced = slots.banded_slot(anchor, work)
    blocked = [(balanced[0], balanced[1], z)
               for z in range(int(params.RELAY_BAND_M),
                              int(params.SLOT_CEILING_M) + 10,
                              int(params.SLOT_RAISE_STEP_M))]
    decision = slots.solve(anchor, work, live=blocked)
    assert decision.status == slots.RELAY_INFEASIBLE
    assert decision.slot is None


def test_relay_infeasible_when_a_hop_would_be_outside_the_usable_limit():
    """Better to report it than to send somebody to a place that does not work."""
    decision = slots.solve((0.0, 0.0, 0.0), [(600.0, 0.0, 50.0)])
    assert decision.status == slots.RELAY_INFEASIBLE
    assert decision.anchor_hop_m > params.USED_LINK_MAX_M


def test_the_derived_reconnect_budget_matches_the_oracle_term_for_term():
    expected = oracle_relay_kill()
    ours = slots.reconnect_budget_s(oracle.START[expected["mover"]],
                                    expected["slot"])
    theirs = oracle.reconnect_budget(expected["flight_m"])
    assert abs(ours - theirs) < 1e-6


def test_reconnection_happens_inside_the_budget_the_oracle_derives():
    """The harness quantises time, so this is a bound and not a measurement.

    The number that goes in a run record comes from a scenario under the real
    harness. What this asserts is that the logic does not need longer than the
    geometry says it should, which is the half a unit test can answer.
    """
    expected = oracle_relay_kill()
    budget = oracle.reconnect_budget(expected["flight_m"])

    net = settled_net()
    net.run_for(2.0)
    killed_at = net.now
    net.kill("uav_2")
    lost_at = None
    reconnected = None
    while net.now < killed_at + budget * 3:
        net.step()
        route = net.installed_route(expected["stays"])
        if lost_at is None and route is None:
            lost_at = net.now
        if lost_at is not None and reconnected is None and route is not None:
            reconnected = net.now
            break
    assert reconnected is not None, "the route never came back at all"
    assert reconnected - killed_at <= budget, (
        "recovery took " + str(round(reconnected - killed_at, 1))
        + " s against a derived budget of " + str(round(budget, 1)) + " s")


def test_a_component_that_cannot_place_a_relay_stops_asking():
    """Reporting a condition and retrying forever is a flap with no motion.

    Nothing moves, nothing recovers, and the epoch counter runs away. Driven
    for two minutes past the point where the component gave up, and the epoch
    number has to be the same at the end as at the start.
    """
    net, _ = run_relay_kill(seconds=70.0)
    relay = net.relay_holders()[0]
    survivor = "uav_4" if relay == "uav_3" else "uav_3"
    net.kill(relay)
    net.run_for(90.0)

    machine = net.router(survivor).roles
    settled = machine.highest_epoch
    net.run_for(120.0)
    assert machine.highest_epoch == settled, (
        "the component opened " + str(machine.highest_epoch - settled)
        + " more epochs after reporting that it could not place a relay")
    assert net.router(survivor).relay_infeasible


def test_the_owner_stops_renewing_a_lease_for_a_relay_that_has_gone():
    """Otherwise the epoch stays open forever and no later epoch can start.

    The owner renews for as long as the epoch is open, and the epoch is open
    until the relay is released or the lease runs out. Nothing releases a relay
    that died, so an owner renewing unconditionally holds the role assigned to
    a vehicle that no longer exists and blocks every recovery after it.
    """
    net, _ = run_relay_kill(seconds=70.0)
    relay = net.relay_holders()[0]
    owner_id = "uav_4" if relay == "uav_3" else "uav_3"
    owner = net.router(owner_id)
    assert owner.roles.current.owner == owner_id
    assert owner._relay_still_reachable() is True

    net.kill(relay)
    net.run_for(params.NEIGHBOUR_TIMEOUT_S + 2 * params.LSA_PERIOD_S + 1.0)
    assert owner._relay_still_reachable() is False, (
        "the owner still believes it should renew the lease of a relay it "
        "cannot reach")

    net.run_for(params.ROLE_LEASE_S + 5.0)
    assert owner.roles.epochs[1].open() is False, (
        "epoch 1 never closed, so no later epoch could ever open")
