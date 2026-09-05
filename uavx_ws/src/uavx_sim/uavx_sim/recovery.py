"""Chunk 4.2: what a run that lost a vehicle reports, with no ROS in it.

`comms.py` turned the files the nodes wrote into the delivery numbers week 3
was scored on. This is the same job for a run with a fault in it, and the
questions are harder because every one of them is about time:

    safety_from_payload  the four separation fields, off the collector
    destroyed_by         which vehicles this run killed and watched die
    fault_at             when the fault landed, from the injector's own record
    outage_window        when the swarm lost its route and when it had one again
    outage_block         the observations block, over that window
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

**Nothing is inferred from the absence of evidence.** A reconnect time is
measured over routers that say they lost a route and got it back. A run where
no router lost anything does not produce a reconnect time of zero, it is
refused: zero would be the best possible number and it would mean the fault
never landed.
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

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
def lost_route(router_ledgers: Sequence[Mapping], after_s: float,
               epoch_s: float = 0.0) -> Dict[str, Tuple[float, Optional[float]]]:
    """The routers that lost their route after the fault and got one back.

    `route_returned_at` moves only on a transition from having no route to
    having one, so for a node that was connected the whole run it is a moment
    early in the bring-up. One later than the fault is that node saying it
    was cut off and is not any more.
    """
    offset = _offset(epoch_s)
    start = _number(after_s)
    if start is None:
        raise RecoveryError(f"after_s is {after_s!r}, not a time")
    out: Dict[str, Tuple[float, Optional[float]]] = {}
    for entry in router_ledgers:
        node = entry.get("node")
        if not isinstance(node, str) or not node:
            raise RecoveryError(
                "a router ledger with no node name cannot be attributed to a "
                "vehicle")
        returned = _number(entry.get("route_returned_at"))
        if returned is None or returned - offset < start:
            continue
        recovered = _number(entry.get("recovered_at"))
        out[node] = (returned - offset,
                     None if recovered is None else recovered - offset)
    return out


def outage_window(router_ledgers: Sequence[Mapping], fault_at_s: float,
                  epoch_s: float = 0.0) -> Tuple[float, float]:
    """When the swarm lost its route, and when the last cut off node had one.

    The end is `route_returned_at` rather than the confirmed recovery, because
    it is the moment a queue had somewhere to drain to. The confirmation is
    one stability window later and is what the reconnect time is measured to.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s)
    if not lost:
        raise RecoveryError(
            "no router reports losing its route after the fault landed. "
            "Either the fault removed a vehicle nothing was routing through, "
            "or the mesh never noticed, and an outage window taken from the "
            "scenario instead would be a window nothing measured")
    end = max(returned for returned, _ in lost.values())
    return float(fault_at_s), end


def reconnect_s(router_ledgers: Sequence[Mapping], fault_at_s: float,
                epoch_s: float = 0.0) -> float:
    """From the fault landing to the last cut off node confirming a route.

    Confirmed, not merely present. A route that appears and drops again
    inside the stability window is the mesh flapping, and a reconnect time
    that stopped at the first appearance would report the flap as a recovery.
    """
    lost = lost_route(router_ledgers, fault_at_s, epoch_s)
    if not lost:
        raise RecoveryError(
            "no router lost its route after the fault, so a reconnect time "
            "would be measured over a swarm that never disconnected. Zero is "
            "the best number the gate can read and it would mean the fault "
            "did nothing")
    late = []
    for node, (returned, recovered) in sorted(lost.items()):
        if recovered is None:
            raise RecoveryError(
                f"{node} got its route back at {returned:.1f}s and never held "
                f"it for the stability window, so the run ended with the mesh "
                f"still flapping")
        late.append(recovered)
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
                 destroyed: Sequence[str] = ()) -> dict:
    """The observations block for a run with a fault in it.

    The window is measured rather than declared: it opens when the fault was
    seen to land and closes when the last cut off node had a route again. A
    window taken from the scenario would be the two numbers somebody typed,
    and the drain bound the gate reads is the difference between them.
    """
    start, end = outage_window(router_ledgers, fault_at_s, epoch_s)
    try:
        return led.observations(router_ledgers, gcs_ledger, start, end,
                                epoch_s=epoch_s, destroyed=destroyed)
    except led.LedgerError as exc:
        raise RecoveryError(str(exc)) from exc


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
                   epoch_s: float = 0.0) -> dict:
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

    _, end = outage_window(router_ledgers, fault_at_s, epoch_s)
    out["time_to_reconnect_s"] = round(
        reconnect_s(router_ledgers, fault_at_s, epoch_s), 3)
    out["delivery_ratio_after_recovery"] = ratio_after(
        router_ledgers, gcs_ledger, end, epoch_s)
    out["relay_slot"] = relay_slot(router_ledgers)
    return out
