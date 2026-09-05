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

from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

Identity = Tuple[str, int]


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
    """
    arrived = set(delivered_ids)
    return {node: delivery_ratio(ids, arrived)
            for node, ids in sorted(generated_by_node.items())}


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
