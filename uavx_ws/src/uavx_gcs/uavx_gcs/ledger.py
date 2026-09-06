"""What the ground station reports, as arithmetic with no ROS in it.

`gcs_node.py` is the node. This is everything the node concludes from what it
accepted, which is what lets it be tested on a checkout with nothing built.
The same split the metrics collector uses, for the same reason: a simulator run
is the most expensive way there is to find a division in the wrong place.

Two numbers describe the same delivery and they are not interchangeable.
`router.py` is explicit about it: `hop_count` counts forwarders and
`len(path) - 1` counts edges, so an observation from the far surveyor arriving
through the relay and the anchor shows 2 in one and 3 in the other. Both are
reported, under names that say which is which, so neither can be quietly used
in place of the other. The frozen route key and `scripts/check_geometry.py`
count edges; the gate's relay assertion counts forwarders, because a delivery
that arrived direct has zero of those and any relayed one has at least two.

Both are minimums across the observations from a given origin, never maximums.
A run where almost everything went direct and one packet took a long way round
would otherwise report the long way round as though it were the route.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

Identity = Tuple[str, int]


class LedgerError(ValueError):
    """Ledgers that contradict each other, or one that contradicts itself."""


def _minimum_by_origin(values: Iterable[Tuple[Identity, int]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for identity, value in values:
        origin = identity[0]
        seen = out.get(origin)
        out[origin] = value if seen is None else min(seen, value)
    return dict(sorted(out.items()))


def delivered_hops_by_node(accepted_hops: Mapping[Identity, int]) -> Dict[str, int]:
    """Forwarders per origin, taking the fewest any accepted packet went through.

    This is the number the relay assertion reads. An observation that reached
    the ground station without being forwarded has zero, so a run where the
    direct link happened to work cannot satisfy a threshold of two.
    """
    return _minimum_by_origin(accepted_hops.items())


def delivered_edges_by_node(
        accepted_path: Mapping[Identity, Sequence[str]]) -> Dict[str, int]:
    """Edges per origin, which is what the geometry oracle counts.

    An empty or single-entry path is zero edges rather than a negative number.
    A path should never be empty, and reporting minus one for one that is
    would put a number in the record that no arithmetic produced.
    """
    return _minimum_by_origin(
        (identity, max(0, len(path) - 1))
        for identity, path in accepted_path.items())


def delivery_ratio(generated_ids: Sequence[str],
                   delivered_ids: Iterable[str]) -> float:
    """Delivered over generated, as a comparison of identity sets.

    Not a count of arrivals. A retry from the origin and a drain from the
    backlog custodian are both correct and both arrive, so counting arrivals
    reports the backlog delivered twice; comparing identities reports it
    delivered once. RFC 9171 draws the same line for the same reason.

    Zero generated returns 0.0 and never 1.0. Nothing over nothing reads as a
    perfect score, and `uavx_eval.check` refuses a record with a zero
    denominator precisely so that it cannot.
    """
    wanted = set(generated_ids)
    if not wanted:
        return 0.0
    return len(wanted & set(delivered_ids)) / len(wanted)


def ratio_by_node(generated_by_node: Mapping[str, Sequence[str]],
                  delivered_ids: Iterable[str]) -> Dict[str, float]:
    """The same ratio, per originating node.

    The gate reads the far surveyor's row on its own, because a swarm average
    stays comfortable while the one vehicle that needs the relay is delivering
    nothing at all.

    A node that generated nothing has no row. Round 9 finding 1 gave
    queue_drain two surveying origins, so its anchor and its gated relay send
    no observations at all, and a ratio over an empty denominator reads as a
    score of zero while meaning "this vehicle was not asked to do anything".
    The record writer already refuses that and it is right to: the honest
    answer is the absence of a row, and app_packets_sent_by_node still carries
    the zero so the vehicle does not vanish from the record.
    """
    arrived = set(delivered_ids)
    return {node: delivery_ratio(ids, arrived)
            for node, ids in sorted(generated_by_node.items()) if ids}


# --------------------------------------------------------- the outage block
#
# Chunk 4.2. Every field here is read off the files the nodes wrote as they
# shut down, and none of it is asked of the process that would benefit from
# the answer. The shape of the question is the same one week 3 settled for
# the delivery ratio: the origins own the denominator, the destination owns
# the numerator, and the comparison is of identity sets rather than of counts.
#
# What week 4 adds is time. "450 observations were generated" can be satisfied
# by producing them before the route went down, which tests nothing about a
# queue holding data through an outage, so the block carries a row per
# generated observation with the moment it was minted and the moment it
# arrived. Round 7 finding 8 is that aggregate counts could not prove which
# ids fell inside the claimed windows.


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _node_of(entry: Mapping) -> str:
    node = entry.get("node")
    if not isinstance(node, str) or not node:
        raise LedgerError(
            "a ledger with no node name cannot be attributed to a vehicle, "
            "and every number in this block is per vehicle before it is "
            "summed")
    return node


def generated_rows(router_ledgers: Iterable[Mapping]) -> Dict[str, float]:
    """Every observation id in the run, against the time it was minted.

    Refuses two nodes claiming one id. Identity is (origin, sequence) and the
    origin is in the id, so a collision means two processes minting from one
    counter, which is what happens when a vehicle runs a router and a survey
    executor that both generate.
    """
    out: Dict[str, float] = {}
    for entry in router_ledgers:
        node = _node_of(entry)
        minted = entry.get("generated_at")
        ids = entry.get("generated_ids") or []
        if not isinstance(minted, Mapping):
            minted = {}
        for identity in ids:
            identity = str(identity)
            if identity in out:
                raise LedgerError(
                    f"{identity} is claimed by two nodes, the second being "
                    f"{node}. An observation id carries its origin, so a "
                    f"collision means two processes minting from one counter")
            when = _number(minted.get(identity))
            if when is None:
                raise LedgerError(
                    f"{node} says it generated {identity} and gives no time "
                    f"for it. Every window in this block is decided by when "
                    f"an observation was minted, and a missing time would be "
                    f"counted as outside every one of them")
            out[identity] = when
    return out


def delivered_rows(gcs_ledger: Mapping) -> Dict[str, float]:
    """Every accepted observation, against the time it arrived.

    Built from the destination's own per delivery rows. A duplicate arrival
    keeps the first time: the ground station accepted the identity once, and
    that is the moment it was delivered.
    """
    rows = gcs_ledger.get("ledger")
    if not isinstance(rows, (list, tuple)):
        raise LedgerError(
            "the ground station ledger carries no per delivery rows. Every "
            "time in this block comes from them, and aggregate counts cannot "
            "say which ids arrived inside a window")
    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise LedgerError(f"a delivery row is {row!r} and must be a mapping")
        identity = row.get("id")
        when = _number(row.get("delivered_at"))
        if not isinstance(identity, str) or not identity:
            raise LedgerError(f"a delivery row has no id: {row!r}")
        if when is None:
            raise LedgerError(
                f"the row for {identity} has no delivery time. It is the row "
                f"that says the observation arrived, so a row without one is "
                f"a delivery nobody can place in the run")
        if identity in out:
            out[identity] = min(out[identity], when)
        else:
            out[identity] = when
    return out


# Scenario time zero. The comms nodes come up before it, by the settle the
# runner gives them to find each other's topics, and they mint observations in
# that window. Those are delivered and the run record's delivery ratio counts
# them; they are not part of the outage arithmetic, and the schema puts a
# minimum of zero on every creation time in this block.
RUN_START_S = 0.0


def created_rows(gcs_ledger: Mapping) -> Dict[str, float]:
    """When each accepted observation says it was made, per the destination.

    The packet carries its own creation stamp, so this is the destination's
    copy of a time the origin also reported. It is what scopes the delivered
    set to the run without scoping it to the generated set: an id nobody
    minted has a creation time too, and it has to stay visible as unexpected
    rather than disappear for being absent from the other list.
    """
    rows = gcs_ledger.get("ledger")
    if not isinstance(rows, (list, tuple)):
        return {}
    out: Dict[str, float] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identity = row.get("id")
        when = _number(row.get("created_at"))
        if isinstance(identity, str) and identity and when is not None:
            out.setdefault(identity, when)
    return out


def backlog_custodian(router_ledgers: Sequence[Mapping],
                      outage_ids: Iterable[str]) -> Optional[str]:
    """The member a cut off component named, and that then held somebody else's traffic.

    architecture.md section 3: a disconnected component funnels its backlog to
    one member so the depth the store and forward design is sized for is
    actually reached. Two things are asked of the answer and neither is enough
    on its own. The node has to have been named by a router that had no route,
    which is the rule firing, and it has to have ended up holding observations
    it did not mint, which is the rule having an effect.

    Custody alone named uav_1 in the accepted relay_kill record: the anchor
    holds ids it did not mint on every run there is, because forwarding is
    what an anchor does, and it wins a lowest id tie against every vehicle.
    Being named alone is no better, since a component of one names itself and
    holds nothing but its own.

    Naming is weighed in vehicle-seconds rather than counted. Every node is
    without a route for the first seconds of a run while the link state
    travels, and the first queue_drain has uav_1 naming itself for 2.1 s of
    bring-up against uav_3 named for 83.9 by the two vehicles that were
    actually cut off. A rule that took the lowest id of everyone ever named
    reports the anchor again.

    A ledger written before chunk 4.4 carries no name, and for those the
    custody half stands alone, which is what the earlier records were read by.
    """
    wanted = set(outage_ids)
    if not wanted:
        return None
    holders = []
    named: Dict[str, float] = {}
    for entry in router_ledgers:
        node = _node_of(entry)
        held = set(str(i) for i in (entry.get("custodied_ids") or []))
        minted = set(str(i) for i in (entry.get("generated_ids") or []))
        if held & (wanted - minted):
            holders.append(node)
        who = entry.get("custodian_named")
        if isinstance(who, str) and who:
            seconds = _number(entry.get("custodian_named_s"))
            named[who] = named.get(who, 0.0) + (seconds or 0.0)
    if not holders:
        return None
    claimed = sorted(set(holders) & set(named))
    if not claimed:
        return min(holders)
    return min(claimed, key=lambda node: (-named[node], node))


def observations(router_ledgers: Sequence[Mapping], gcs_ledger: Mapping,
                 outage_start_s: float, outage_end_s: float,
                 drain_start_s: Optional[float] = None,
                 epoch_s: float = 0.0,
                 destroyed: Sequence[str] = (),
                 drain_end_s: Optional[float] = None,
                 drain_by_node: Optional[Mapping] = None,
                 backlog_by_node: Optional[Mapping] = None) -> dict:
    """The whole observations block, from the ledgers and the outage window.

    The window comes from the run rather than from the files: the moment a
    vehicle was killed or gated is the runner's knowledge, and a block that
    inferred it from a gap in the deliveries would be deciding when the
    failure happened from the evidence that the failure happened.

    `epoch_s` is the simulated time the scenario started at. Every node stamps
    its file in simulated seconds since the simulator came up, which is a
    couple of minutes of bring-up before the run, and every window here
    arrives in seconds since the run began. Without the offset the two are
    off by the bring-up and every observation lands outside every window.

    `destroyed` is the vehicles this run killed. See `_lost_with`: what those
    aircraft were still holding alone went down with them, and the delivered
    set cannot contain data that stopped existing.

    Both sets are scoped to the run. The radio is up before scenario time
    zero, by the settle the nodes need to find each other, and the traffic in
    that window belongs to the delivery ratio rather than to the outage
    arithmetic. See RUN_START_S.

    `drain_end_s` is when the queues this outage filled had emptied, worked
    out by the caller from the route episode each member was in. A store that
    ran empty on a later episode, or the gated vehicle's own store when its
    radio came back two minutes afterwards, is a different event with a
    different cause, and folding them together reports a 117 second drain for
    a design that promises 2.25. Without it the last drain any node recorded
    is used, which is right for a run with one outage in it.
    """
    router_ledgers = list(router_ledgers)
    start, end = _number(outage_start_s), _number(outage_end_s)
    if start is None or end is None:
        raise LedgerError(
            f"the outage window is ({outage_start_s!r}, {outage_end_s!r}) and "
            f"both ends have to be times")
    if end < start:
        raise LedgerError(
            f"the outage ends at {end} and starts at {start}. A window that "
            f"runs backwards puts every observation outside it")

    offset = _number(epoch_s)
    if offset is None:
        raise LedgerError(
            f"epoch_s is {epoch_s!r}, not the simulated time the scenario "
            f"started at")
    # Round 9 finding 4, found by the checker it added. Every time in this
    # block is written to three decimals and the window ends were not, so a
    # record said 440 observations were minted inside an outage whose own rows
    # showed 436: four of them were created at 121.1000000000001, went into
    # the count at full precision, and came out of the ledger rounded to
    # 121.1, which is below a start of 121.10000000000001. A record has to be
    # checkable from its own contents, so the window and the rows are put on
    # one grid here rather than compared across two.
    minted = {i: round(when - offset, 3) for i, when in
              generated_rows(router_ledgers).items()}
    arrived = {i: round(when - offset, 3) for i, when in
               delivered_rows(gcs_ledger).items()}
    born = {i: round(when - offset, 3) for i, when in
            created_rows(gcs_ledger).items()}
    start, end = round(start, 3), round(end, 3)

    # Scoped to the run, on both sides and by the same rule. See RUN_START_S.
    # The delivered side is scoped by the destination's own copy of the
    # creation stamp rather than by membership of the generated set, so an id
    # nobody minted is still counted as unexpected.
    before_run = sorted(i for i, when in minted.items() if when < RUN_START_S)
    minted = {i: when for i, when in minted.items() if when >= RUN_START_S}
    arrived = {i: when for i, when in arrived.items()
               if born.get(i, RUN_START_S) >= RUN_START_S}

    lost = {node: [i for i in ids if i in minted]
            for node, ids in _lost_with(router_ledgers, destroyed,
                                        set(arrived)).items()}
    lost = {node: ids for node, ids in lost.items() if ids}
    for ids in lost.values():
        for identity in ids:
            minted.pop(identity, None)

    generated_ids = sorted(minted)
    delivered_ids = sorted(arrived)
    missing = sorted(set(generated_ids) - set(delivered_ids))
    unexpected = sorted(set(delivered_ids) - set(generated_ids))

    during = sorted(i for i, when in minted.items() if start <= when < end)
    drain_start = _number(drain_start_s) if drain_start_s is not None else end
    if drain_start is None:
        raise LedgerError(f"drain_start_s is {drain_start_s!r}, not a time")
    if drain_start < end:
        raise LedgerError(
            f"the drain starts at {drain_start} and the outage ends at {end}. "
            f"A queue cannot begin draining before its route comes back")

    # The drain is a claim about a queue, so it is measured at the queue. Only
    # emptyings at or after the route returned count: every node's store runs
    # empty constantly in a healthy run.
    by_node = {}
    for entry in router_ledgers:
        when = _number(entry.get("drain_end_at"))
        if when is not None:
            by_node[_node_of(entry)] = round(when - offset, 3)
    counted = []
    if isinstance(drain_by_node, Mapping):
        for node, when in drain_by_node.items():
            if _number(when) is None:
                continue
            by_node[str(node)] = round(float(when), 3)
            counted.append(str(node))
    given = _number(drain_end_s)
    if given is not None:
        drain_end = given
    else:
        ends = [when for node, when in by_node.items()
                if when >= drain_start and (not counted or node in counted)]
        drain_end = max(ends) if ends else drain_start

    custodian = backlog_custodian(router_ledgers, during)
    held_by_custodian = []
    if custodian is not None:
        for entry in router_ledgers:
            if _node_of(entry) != custodian:
                continue
            held = set(str(i) for i in (entry.get("custodied_ids") or []))
            held_by_custodian = sorted(held & set(during))

    after_restore = [i for i in during
                     if i in arrived and arrived[i] >= drain_start]
    complete = max((arrived[i] for i in during if i in arrived),
                   default=drain_end)

    out = {
        "generated_ids": generated_ids,
        "delivered_ids": delivered_ids,
        "generated": len(generated_ids),
        "unique_delivered": len(delivered_ids),
        "duplicated": int(gcs_ledger.get("duplicated") or 0),
        "expired": sum(int(e.get("expired") or 0) for e in router_ledgers),
        "evicted": sum(int(e.get("evicted") or 0) for e in router_ledgers),
        # Pushed out of a queue and not lost, because the node that minted it
        # still held it. Reported beside evicted rather than folded into it:
        # a queue too small for a two minute outage and a design that loses
        # data produce the same number otherwise.
        "deferred": sum(int(e.get("deferred") or 0) for e in router_ledgers),
        "peak_queue_depth": max(
            [int(e.get("peak_queue_depth") or 0) for e in router_ledgers]
            or [0]),
        "control_queue_max_delay_s": max(
            [_number(e.get("control_queue_max_delay_s")) or 0.0
             for e in list(router_ledgers) + [gcs_ledger]] or [0.0]),
        "outage_start_s": start,
        "outage_end_s": end,
        "generated_during_outage": len(during),
        "delivered_after_restore": len(after_restore),
        "drain_start_s": drain_start,
        "drain_end_s": drain_end,
        "backlog_drain_s": drain_end - drain_start,
        "delivery_complete_s": complete,
        "missing_ids": missing,
        "unexpected_ids": unexpected,
        # The gate reads counts and the schema keeps the lists. Both, because
        # a count with no list cannot be argued with and a list nobody counted
        # is not a threshold.
        "missing_count": len(missing),
        "unexpected_count": len(unexpected),
        # What the run started after. Reported rather than silently dropped:
        # a block that quietly narrowed its own denominator is the shape this
        # project has spent four weeks refusing.
        "generated_before_run": len(before_run),
        # When each node's store first ran empty after its own route came
        # back, and which of those the bound was taken over. Everything is
        # here, including the gated vehicle whose own store empties two
        # minutes later, because a number left out of the record is a number
        # nobody can argue with.
        "drain_by_node": dict(sorted(by_node.items())),
        "drain_counted_for": sorted(counted),
        # How many observations each cut off member was still holding when it
        # got a route again. The bound above is the time the largest of those
        # took to leave, and this is the size it was measured over.
        "backlog_by_node": {str(node): int(size) for node, size
                            in sorted((backlog_by_node or {}).items())},
        "backlog": max([int(v) for v in (backlog_by_node or {}).values()]
                       or [0]),
        "ledger": [{"id": i, "created_at_s": round(minted[i], 3),
                    "delivered_at_s": (None if i not in arrived
                                       else round(arrived[i], 3))}
                   for i in generated_ids],
    }
    if custodian is not None:
        out["backlog_custodian"] = custodian
        out["custodied_ids"] = held_by_custodian
        out["custodied"] = len(held_by_custodian)
    if lost:
        out["lost_with_vehicle"] = {node: list(ids)
                                    for node, ids in sorted(lost.items())}
        out["lost_with_vehicle_count"] = sum(len(v) for v in lost.values())
    return out


def _lost_with(router_ledgers: Sequence[Mapping], destroyed: Sequence[str],
               arrived: set) -> Dict[str, List[str]]:
    """What each destroyed vehicle took with it, from its own file.

    An observation this design loses for good is one that was still only on
    the aircraft that minted it. Everything else survives the airframe: a
    packet handed to a neighbour is still retained by its origin until the
    destination acknowledges it, and the origin re-sends it the moment a
    route exists again, so a relay dying with a full queue costs nothing but
    the time to repair the path.

    Three things keep this from being a way to make a bad run look clean. The
    ids come from the dead vehicle's own ledger and nowhere else, so the
    runner cannot widen the set. Anything the ground station received is
    excluded, because an observation that arrived was not lost. And only a
    vehicle this run actually destroyed can be named, which the runner takes
    from the injected events it watched land.
    """
    if not destroyed:
        return {}
    by_node = {_node_of(entry): entry for entry in router_ledgers}
    out: Dict[str, List[str]] = {}
    for node in destroyed:
        entry = by_node.get(node)
        if entry is None:
            raise LedgerError(
                f"{node} was destroyed in this run and wrote no ledger, so "
                f"nothing says what it had minted. Every observation the "
                f"ground station accepted from it would then be an identity "
                f"nobody generated")
        held = entry.get("unacknowledged_ids")
        if not isinstance(held, (list, tuple)):
            raise LedgerError(
                f"{node} was destroyed and its ledger does not say what it "
                f"was still holding. Without that list its undelivered "
                f"observations cannot be told from the swarm losing them")
        ids = sorted(str(i) for i in held if str(i) not in arrived)
        if ids:
            out[node] = ids
    return out


def observations_set_equal(block: Mapping) -> bool:
    """Whether every observation generated was delivered, and only those.

    The gate asserts this on its own because it is the claim the whole
    delivered-once design is for, and because a ratio of 1.0 can be reached
    with an id nobody generated making up for one that went missing.
    """
    return (not block.get("missing_ids")) and (not block.get("unexpected_ids"))


def generated_by_node(ledgers: Iterable[Mapping]) -> Dict[str, List[str]]:
    """Every identity each vehicle says it produced, from the per node files.

    The origins are the only honest source for the denominator. The ground
    station knows what arrived and cannot know what did not, and a denominator
    the destination invents is the shape of a delivery ratio that is always
    one.
    """
    out: Dict[str, List[str]] = {}
    for entry in ledgers:
        node = entry.get("node")
        if not isinstance(node, str) or not node:
            continue
        ids = entry.get("generated_ids")
        if not isinstance(ids, (list, tuple)):
            continue
        out[node] = [str(i) for i in ids]
    return dict(sorted(out.items()))
