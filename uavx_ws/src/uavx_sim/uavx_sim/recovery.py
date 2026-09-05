"""Chunk 4.2: what a run that lost a vehicle reports, with no ROS in it.

`comms.py` turned the files the nodes wrote into the delivery numbers week 3
was scored on. This is the same job for a run with a fault in it, and the
questions are harder because every one of them is about time:

    safety_from_payload  the four separation fields, off the collector
    destroyed_by         which vehicles this run killed and watched die
    targets_of           which vehicles a fault was applied to at all
    fault_at             when the fault landed, from the injector's own record
    outage_window        when the swarm lost its route and when it had one again
    outage_block         the observations block, over that window
    handback_block       the transaction that gave the vehicle back, and the
                         gap in the traffic it cost
    route_restored       whether the swarm has a route again and kept it
    outages_after        how many times anything lost its route after a moment
    reconnect_s          how long that took, against the 45 s the gate allows
    relay_slot           where the component sent the mover, from its own file
    ratio_after          what fraction of the traffic minted after the repair
                         arrived
    recovery_block       all six recovery fields, assembled once

Two rules run through the whole file.

**Every clock here is the scenario's.** A node stamps its ledger in simulated
seconds since the simulator came up, and that is two minutes of bring-up
before the run starts. The runner knows the offset, it is subtracted once at
every point a file is read, and the rest of the module works in seconds since
the scenario began, which is what the record means by `_s`.

**The vehicle a fault was applied to is not evidence of recovery.** A gated
radio comes back when the scenario says so, not when the swarm does anything,
and a reconnect time measured over it is a stopwatch on the fault rather than
on the response to it. Every recovery number here is computed over the
routers the fault happened to and never over the one it happened at.

**Nothing is inferred from the absence of evidence.** A reconnect time is
measured over routers that say they lost a route and got it back. A run where
no router lost anything does not produce a reconnect time of zero, it is
refused: zero would be the best possible number and it would mean the fault
never landed.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from uavx_comms import params as comms_params
from uavx_gcs import ledger as led
from uavx_roles import trace as tr

# What the record carries about keeping vehicles apart, and what the
# collector's payload has to supply. The gate reads all four at the top level
# of the record, so that is where the runner puts them.
SAFETY_KEYS = ("min_pairwise_separation_m", "separation_violations",
               "collision_contacts", "contact_monitor_samples")

# The times in a role manager's file, in the node's clock.
ROLE_TIME_KEYS = ("granted_at", "arrived_at", "released_at", "lapsed_at",
                  "returned_at")

KILL = "kill"

# What counts as a hole in the delivered stream during a handback.
#
# The swarm delivers at the frozen observation rate times the number of
# origins, which is fifteen a second in the common geometry, so a whole second
# without one is fifteen consecutive losses rather than a fade. It is also the
# period the mesh itself uses to decide whether a neighbour is still there,
# which makes it the shortest gap the design is entitled to call an outage.
DELIVERY_GAP_S = comms_params.HELLO_PERIOD_S

# The nine fields the schema requires of a handback, and the reason for the
# object: without timestamps and a named path, break before make and make
# before break produce identical records. Round 5 finding 1.
HANDBACK_KEYS = ("epoch", "epoch_owner", "staying_member", "prepared_path",
                 "confirmed_observation_id", "release_sender", "confirmed_at",
                 "release_at", "observation_gap_count")

# The times in it, in the node's clock.
HANDBACK_TIME_KEYS = ("confirmed_at", "release_at")


class RecoveryError(ValueError):
    """A recovery the runner will not write down, naming the reason."""


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _offset(epoch_s) -> float:
    value = _number(epoch_s)
    if value is None:
        raise RecoveryError(
            f"epoch_s is {epoch_s!r}, not the simulated time the scenario "
            f"started at. Without it every time read off a node's file is "
            f"the bring-up plus the run rather than the run")
    return value


# ---------------------------------------------------------------- the safety
def safety_from_payload(payload: Mapping) -> dict:
    """The four separation fields, off the collector's last payload.

    The collector is the only process that saw ground truth, so the numbers
    are its. All four or none: `contact_monitor_samples` is what tells zero
    violations from a monitor that never ran, and round 3 finding 8 is that
    the second reads exactly like the first.
    """
    if not isinstance(payload, Mapping):
        raise RecoveryError(
            "the collector produced no payload, so this run has nothing to "
            "say about how close the vehicles came to each other")
    missing = [key for key in SAFETY_KEYS if key not in payload]
    if missing:
        raise RecoveryError(
            f"the collector's payload has no {', '.join(missing)}. Either it "
            f"saw fewer than two vehicles for the whole run or it is a "
            f"version that does not report contacts")
    return {key: payload[key] for key in SAFETY_KEYS}


# ------------------------------------------------------------ the fault
def destroyed_by(events: Iterable[Mapping]) -> Tuple[str, ...]:
    """The vehicles this run killed and then watched stop.

    A kill that was requested and never observed removed nothing, and naming
    its target here would excuse the observations of a vehicle that is still
    flying and still delivering.
    """
    out = set()
    for row in events or ():
        if not isinstance(row, Mapping) or row.get("type") != KILL:
            continue
        if _number(row.get("observed_t")) is None:
            continue
        target = row.get("target")
        if not isinstance(target, str) or not target:
            raise RecoveryError(f"an injected kill names no target: {row!r}")
        out.add(target)
    return tuple(sorted(out))


def targets_of(events: Iterable[Mapping]) -> Tuple[str, ...]:
    """Every vehicle a fault of this run was applied to and seen to land.

    Not the same question as `destroyed_by`. That one asks which vehicles
    stopped existing, and this one asks which ones the run did something to,
    because neither a killed relay nor a gated one is evidence about how the
    rest of the swarm recovered.
    """
    out = set()
    for row in events or ():
        if not isinstance(row, Mapping):
            continue
        if _number(row.get("observed_t")) is None:
            continue
        target = row.get("target")
        if isinstance(target, str) and target:
            out.add(target)
    return tuple(sorted(out))


def fault_at(events: Iterable[Mapping]) -> float:
    """When the first fault of this run was seen to land, in scenario seconds.

    The injector's `observed_t` and never the scenario's `at_s`. One is what
    the runner asked for and the other is when two witnesses on the target
    agreed it had happened, and a recovery measured from the request would
    include however long the effect took to become visible.
    """
    times = []
    for row in events or ():
        if not isinstance(row, Mapping):
            continue
        when = _number(row.get("observed_t"))
        if when is not None:
            times.append(when)
    if not times:
        raise RecoveryError(
            "no injected event was observed to land, so there is no moment "
            "for a recovery to be measured from. A time taken from the "
            "scenario instead would be the moment the runner asked")
    return min(times)


# ------------------------------------------------------------- the outage
def _episodes(entry: Mapping, offset: float) -> list:
    """One node's route history, in the scenario's clock.

    A ledger written before chunk 4.3 has only the three scalars, which are
    the latest of each rather than the history. Read as one episode, which is
    what they describe when a node lost its route once.
    """
    rows = entry.get("route_episodes")
    if isinstance(rows, (list, tuple)) and rows:
        out = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            returned = _number(row.get("returned_at"))
            if returned is None:
                continue
            out.append({
                "returned_at": returned - offset,
                "recovered_at": (None if _number(row.get("recovered_at")) is None
                                 else _number(row["recovered_at"]) - offset),
                "drained_at": (None if _number(row.get("drained_at")) is None
                               else _number(row["drained_at"]) - offset),
                "lost_at": (None if _number(row.get("lost_at")) is None
                            else _number(row["lost_at"]) - offset),
            })
        return sorted(out, key=lambda row: row["returned_at"])

    returned = _number(entry.get("route_returned_at"))
    if returned is None:
        return []
    recovered = _number(entry.get("recovered_at"))
    drained = _number(entry.get("drain_end_at"))
    return [{"returned_at": returned - offset,
             "recovered_at": None if recovered is None else recovered - offset,
             "drained_at": None if drained is None else drained - offset,
             "lost_at": None}]


def lost_route(router_ledgers: Sequence[Mapping], after_s: float,
               epoch_s: float = 0.0,
               exclude: Sequence[str] = ()) -> Dict[str, dict]:
    """The routers that lost their route after the fault and got one back.

    The first episode after the fault, and never the latest one. uav_3 got
    its route back at 140 s of the first complete link_loss, and then
    withdrew and recomputed once more while it was flying home at 257. A
    field holding the latest of those read as a swarm that took 137 seconds
    to reconnect.

    `exclude` is the vehicles the fault was applied to. A gated radio loses
    its own route the moment it is gated and gets it back the moment the
    scenario's hold runs out, and neither of those is the swarm recovering
    from anything.
    """
    offset = _offset(epoch_s)
    skip = set(exclude)
    start = _number(after_s)
    if start is None:
        raise RecoveryError(f"after_s is {after_s!r}, not a time")
    out: Dict[str, dict] = {}
    for entry in router_ledgers:
        node = entry.get("node")
        if not isinstance(node, str) or not node:
            raise RecoveryError(
                "a router ledger with no node name cannot be attributed to a "
                "vehicle")
        if node in skip:
            continue
        after = [row for row in _episodes(entry, offset)
                 if row["returned_at"] >= start]
        if not after:
            continue
        out[node] = after[0]
    return out


def outage_window(router_ledgers: Sequence[Mapping], fault_at_s: float,
                  epoch_s: float = 0.0,
                  exclude: Sequence[str] = ()) -> Tuple[float, float]:
    """When the swarm lost its route, and when the last cut off node had one.

    The end is `route_returned_at` rather than the confirmed recovery, because
    it is the moment a queue had somewhere to drain to. The confirmation is
    one stability window later and is what the reconnect time is measured to.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s, exclude)
    if not lost:
        raise RecoveryError(
            "no router reports losing its route after the fault landed. "
            "Either the fault removed a vehicle nothing was routing through, "
            "or the mesh never noticed, and an outage window taken from the "
            "scenario instead would be a window nothing measured")
    end = max(row["returned_at"] for row in lost.values())
    return float(fault_at_s), end


def reconnect_s(router_ledgers: Sequence[Mapping], fault_at_s: float,
                epoch_s: float = 0.0,
                exclude: Sequence[str] = ()) -> float:
    """From the fault landing to the last cut off node confirming a route.

    Confirmed, not merely present. A route that appears and drops again
    inside the stability window is the mesh flapping, and a reconnect time
    that stopped at the first appearance would report the flap as a recovery.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s, exclude)
    if not lost:
        raise RecoveryError(
            "no router lost its route after the fault, so a reconnect time "
            "would be measured over a swarm that never disconnected. Zero is "
            "the best number the gate can read and it would mean the fault "
            "did nothing")
    late = []
    for node, row in sorted(lost.items()):
        if row["recovered_at"] is None:
            raise RecoveryError(
                f"{node} got its route back at {row['returned_at']:.1f}s and "
                f"never held it for the stability window, so the run ended "
                f"with the mesh still flapping")
        late.append(row["recovered_at"])
    return max(late) - float(fault_at_s)


# ---------------------------------------------------------------- the slot
def relay_slot(router_ledgers: Sequence[Mapping]) -> dict:
    """Where the component sent the mover, from the file of the node that decided.

    Recomputing it here would be a second answer to the question the gate
    reads, and it would be computed from positions taken at a different
    moment from the ones the election used.
    """
    decided = [(entry.get("node"), entry["relay_slot"])
               for entry in router_ledgers
               if isinstance(entry.get("relay_slot"), Mapping)]
    if not decided:
        raise RecoveryError(
            "no router recorded a relay slot, so no component ever decided "
            "where to send a vehicle. Either the election never opened or it "
            "found nowhere feasible to park")

    points = {tuple(round(float(v), 3) for v in row.get("commanded") or ())
              for _, row in decided}
    if len(points) > 1:
        raise RecoveryError(
            f"{len(points)} different slots were commanded in one run: "
            f"{sorted(points)}. One epoch decides one slot, so two means the "
            f"component was decided twice and the record can only carry one")

    node, row = decided[0]
    if not row.get("commanded"):
        raise RecoveryError(f"{node} recorded a slot with no point in it")
    if _number(row.get("clearance_m")) is None:
        raise RecoveryError(
            f"{node} computed the slot with no other aircraft flying, so its "
            f"clearance is the distance to the nearest vehicle when there was "
            f"none. The gate reads that number against 15 m")
    out = dict(row)
    out["decided_by"] = node
    return out


# ------------------------------------------------------------- the traffic
def ratio_after(router_ledgers: Sequence[Mapping], gcs_ledger: Mapping,
                since_s: float, epoch_s: float = 0.0) -> float:
    """Of the observations minted after the repair, the fraction that arrived.

    The whole run's ratio cannot answer this. It is dominated by the two
    minutes before the fault, when the chain was intact, so a swarm that
    reconnected and then delivered nothing would still read above 0.9.
    """
    offset = _offset(epoch_s)
    start = _number(since_s)
    if start is None:
        raise RecoveryError(f"since_s is {since_s!r}, not a time")
    try:
        minted = led.generated_rows(router_ledgers)
        arrived = set(led.delivered_rows(gcs_ledger))
    except led.LedgerError as exc:
        raise RecoveryError(str(exc)) from exc
    after = [i for i, when in minted.items() if when - offset >= start]
    if not after:
        raise RecoveryError(
            f"nothing was minted after {start:.1f}s, so a post recovery "
            f"delivery ratio would be a fraction of nothing. The run ended "
            f"before the swarm had a chance to show it had recovered")
    return len(set(after) & arrived) / len(after)


def outage_block(router_ledgers: Sequence[Mapping], gcs_ledger: Mapping,
                 fault_at_s: float, epoch_s: float = 0.0,
                 destroyed: Sequence[str] = (),
                 exclude: Sequence[str] = ()) -> dict:
    """The observations block for a run with a fault in it.

    The window is measured rather than declared: it opens when the fault was
    seen to land and closes when the last cut off node had a route again. A
    window taken from the scenario would be the two numbers somebody typed,
    and the drain bound the gate reads is the difference between them.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s, exclude)
    start, end = outage_window(router_ledgers, fault_at_s, epoch_s, exclude)

    # The drain of the queues this outage filled, and of nothing else. A
    # store that ran empty on a later episode of the same node's route, or on
    # the gated vehicle's own return two minutes afterwards, is a different
    # event with a different cause.
    drained = {node: row["drained_at"] for node, row in lost.items()}
    never = sorted(node for node, when in drained.items() if when is None)
    if never:
        raise RecoveryError(
            f"{', '.join(never)} got the route back and never ran the store "
            f"empty on it, so there is no moment at which the backlog this "
            f"outage built had finished draining")
    try:
        return led.observations(
            router_ledgers, gcs_ledger, start, end, epoch_s=epoch_s,
            destroyed=destroyed, drain_end_s=max(drained.values()),
            drain_by_node=drained)
    except led.LedgerError as exc:
        raise RecoveryError(str(exc)) from exc


def outages_after(router_ledgers: Sequence[Mapping], since_s: float,
                  epoch_s: float = 0.0) -> int:
    """How many times any node declared itself cut off after a moment.

    The handback claim is that giving the vehicle back broke nothing. This is
    the number that would make it false, and it is counted from the moments
    the routers wrote down rather than from their state at shutdown, which
    says only where they ended up.
    """
    offset = _offset(epoch_s)
    start = _number(since_s)
    if start is None:
        raise RecoveryError(f"since_s is {since_s!r}, not a time")
    count = 0
    for entry in router_ledgers:
        for when in entry.get("outages") or ():
            value = _number(when)
            if value is not None and value - offset > start:
                count += 1
    return count


def route_restored(router_ledgers: Sequence[Mapping], fault_at_s: float,
                   epoch_s: float = 0.0,
                   exclude: Sequence[str] = ()) -> bool:
    """Whether the swarm has a route again and still had one at the end.

    Two things, because either alone is satisfied by a run that does not
    deserve it. A node that recovered and then lost the route again ends
    disconnected, and a node that never lost it proves nothing about a
    recovery.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s, exclude)
    if not lost:
        return False
    if any(row["recovered_at"] is None for row in lost.values()):
        return False
    return all(entry.get("route_status") == "route_up"
               for entry in router_ledgers)


def delivery_gaps(gcs_ledger: Mapping, start_s: float, end_s: float,
                  epoch_s: float = 0.0,
                  gap_s: float = DELIVERY_GAP_S) -> int:
    """Holes in the delivered stream between two moments.

    The handback is the one transaction in this design that can break a link
    on purpose, so what it has to be measured on is whether the traffic
    stopped. Counted at the destination, because that is the only place that
    knows whether an observation arrived.
    """
    offset = _offset(epoch_s)
    first, last = _number(start_s), _number(end_s)
    if first is None or last is None:
        raise RecoveryError(
            f"the handback window is ({start_s!r}, {end_s!r}) and both ends "
            f"have to be times")
    try:
        arrived = led.delivered_rows(gcs_ledger)
    except led.LedgerError as exc:
        raise RecoveryError(str(exc)) from exc
    times = sorted(when - offset for when in arrived.values()
                   if first <= when - offset <= last)
    if not times:
        # Nothing arrived in the whole window, which is not a gap. It is the
        # swarm delivering nothing at all, and it has to be visible as that
        # rather than as a clean handback.
        raise RecoveryError(
            f"nothing was delivered between {first:.1f}s and {last:.1f}s, so "
            f"the handback cannot be said to have carried traffic through")
    gaps = sum(1 for a, b in zip(times, times[1:]) if b - a > gap_s)
    if times[0] - first > gap_s:
        gaps += 1
    if last - times[-1] > gap_s:
        gaps += 1
    return gaps


def handback_block(router_ledgers: Sequence[Mapping], gcs_ledger: Mapping,
                   epoch_s: float = 0.0,
                   until_s: Optional[float] = None) -> Optional[dict]:
    """The transaction that gave the vehicle back, from the node that ran it.

    None when nothing prepared a path, which is every scenario but link_loss
    and the integrated mission: relay_kill's relay never comes back, so there
    is nothing to hand anything back to.

    A prepared transaction that did not finish is refused rather than reported
    as nothing. A swarm that parked a vehicle and never collected it is a
    result, and a record that omitted it would read like a run where the
    handback was never attempted.
    """
    offset = _offset(epoch_s)
    traces = [(entry.get("node"), entry["handback"]) for entry in router_ledgers
              if isinstance(entry.get("handback"), Mapping)
              and entry["handback"].get("prepared_path")]
    if not traces:
        return None

    complete = [(node, row) for node, row in traces
                if _number(row.get("confirmed_at")) is not None
                and _number(row.get("release_at")) is not None]
    if not complete:
        prepared = ", ".join(str(node) for node, _ in traces)
        raise RecoveryError(
            f"{prepared} prepared a path to hand the relay back on and no "
            f"node has both the confirmation and the release. The swarm "
            f"parked a vehicle and did not collect it, which is a result and "
            f"not an absence")
    if len(complete) > 1:
        raise RecoveryError(
            f"{len(complete)} nodes report running the handback: "
            f"{', '.join(str(node) for node, _ in complete)}. One epoch has "
            f"one owner, so two means the transaction was run twice")

    node, row = complete[0]
    out = dict(row)
    for key in HANDBACK_TIME_KEYS:
        when = _number(out.get(key))
        out[key] = None if when is None else round(when - offset, 3)
    out["reported_by"] = node

    end = _number(until_s)
    if end is None:
        try:
            arrived = led.delivered_rows(gcs_ledger)
        except led.LedgerError as exc:
            raise RecoveryError(str(exc)) from exc
        end = max(arrived.values(), default=out["release_at"]) - offset
    out["observation_gap_count"] = delivery_gaps(
        gcs_ledger, out["confirmed_at"], end, epoch_s)
    out["observation_gap_s"] = DELIVERY_GAP_S
    out["observation_window_end_s"] = round(end, 3)

    missing = [key for key in HANDBACK_KEYS if out.get(key) is None]
    if missing:
        raise RecoveryError(
            f"the handback {node} reports has no {', '.join(missing)}. Every "
            f"one of them is in the schema because without it break before "
            f"make and make before break produce the same record")
    return out


# ------------------------------------------------------------ the assembly
def _rebased(row: Mapping, offset: float) -> dict:
    out = dict(row)
    for key in ROLE_TIME_KEYS:
        when = _number(out.get(key))
        out[key] = None if when is None else round(when - offset, 3)
    return out


def recovery_block(router_ledgers: Sequence[Mapping],
                   role_ledgers: Sequence[Mapping],
                   gcs_ledger: Mapping, fault_at_s: float,
                   epoch_s: float = 0.0,
                   exclude: Sequence[str] = ()) -> dict:
    """The six recovery fields and the slot, from the files and the fault.

    The role half is read from the vehicles' own role managers rather than
    from the router that ran the election, for the reason the delivery ratio
    is read off the ledgers: the node that would benefit from the answer
    being yes is not the one that gets to give it.
    """
    offset = _offset(epoch_s)
    if not role_ledgers:
        raise RecoveryError(
            "no role manager wrote a file, so nothing on any vehicle says "
            "whether it was granted the relay role or flew anywhere")
    try:
        out = tr.swarm_trace([_rebased(row, offset) for row in role_ledgers])
    except ValueError as exc:
        raise RecoveryError(str(exc)) from exc

    _, end = outage_window(router_ledgers, fault_at_s, epoch_s, exclude)
    out["time_to_reconnect_s"] = round(
        reconnect_s(router_ledgers, fault_at_s, epoch_s, exclude), 3)
    out["delivery_ratio_after_recovery"] = ratio_after(
        router_ledgers, gcs_ledger, end, epoch_s)
    out["relay_slot"] = relay_slot(router_ledgers)

    # Chunk 4.3. Only a run where the vehicle came back has these.
    handback = handback_block(router_ledgers, gcs_ledger, epoch_s)
    if handback is not None:
        out["handback"] = handback
        out["outage_count_after_release"] = outages_after(
            router_ledgers, handback["release_at"], epoch_s)
    out["route_restored_after_blackout"] = route_restored(
        router_ledgers, fault_at_s, epoch_s, exclude)
    return out
