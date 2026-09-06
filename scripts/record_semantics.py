"""What a run record has to say about itself, recomputed from its own ledger.

Round 9 finding 4. The week gate and the final package check ran different
amounts of this. `uavx_eval.check` validated a record against the schema and a
handful of time comparisons, then read the gate's requirements straight out of
the summary scalars. `scripts/check_submission.py` recomputed those scalars
from the per observation rows and refused a record whose summary disagreed with
its own ledger. So a chunk and a week could go green on a record the package
would later reject, which is this project's recurring failure wearing its
fourth hat: a check that reports success on a broken system.

Codex demonstrated it rather than argued it. Setting `custodied` to 450 with
ids nothing generated, and typing `backlog_drain_s` down to 2.2, left both the
schema pass and all 22 of chunk 4.4's requirements green while the ledger still
held 900 outage ids.

This module is the answer to both callers. Standard library only, no ROS, no
package imports, so `validate_record.py` can use it inside `uavx_eval` under
the colcon overlay and `check_submission.py` can use it from a bare checkout.

Every function returns a list of strings and raises nothing. A record missing
the field a check is about is not that check's business: the schema says which
fields are required, and a checker that also refused absent fields would report
one fault twice and disagree with the schema about which.
"""

import math

# The observation clocks, in the order a run produces them. The outage opens,
# it closes, the queue starts draining when the route is back, the backlog is
# gone, the destination has everything, and all of it fits inside the run.
CLOCK_ORDER = ("outage_start_s", "outage_end_s", "drain_start_s",
               "drain_end_s", "delivery_complete_s")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def _ledger_rows(block):
    """The per observation rows, keyed by id, or None if they are not usable."""
    rows = block.get("ledger")
    if not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        ident = row.get("id")
        if not isinstance(ident, str) or not ident:
            return None
        out[ident] = row
    return out if len(out) == len(rows) else None


def observation_problems(record) -> list:
    """The observations block against the rows it was counted from.

    The block is a summary and the ledger is the evidence. Every scalar the
    gate reads is recomputed here from the rows, because a summary that was
    written rather than measured is exactly what a tampered or a buggy record
    looks like, and the scalars are what the requirements read.
    """
    block = record.get("observations")
    if not isinstance(block, dict):
        return []
    problems = []

    generated = block.get("generated_ids")
    delivered = block.get("delivered_ids")
    if not isinstance(generated, list) or not isinstance(delivered, list):
        return ["observations carries no id lists, so nothing in it can be "
                "checked against what the run produced"]
    generated_set, delivered_set = set(generated), set(delivered)
    if len(generated_set) != len(generated):
        problems.append("observations.generated_ids holds the same id twice")
    if len(delivered_set) != len(delivered):
        problems.append("observations.delivered_ids holds the same id twice")

    for name, ids in (("generated", generated_set),
                      ("unique_delivered", delivered_set)):
        count = block.get(name)
        if isinstance(count, int) and not isinstance(count, bool) \
                and count != len(ids):
            problems.append(
                f"observations.{name} is {count} and the list it was counted "
                f"from holds {len(ids)} distinct ids. One of the two was "
                f"measured and the other was typed")

    extra = delivered_set - generated_set
    if extra:
        problems.append(
            f"the destination accepted {len(extra)} ids no origin says it "
            f"generated, starting {sorted(extra)[:3]}. A delivered set that "
            f"is not a subset of the generated set is a denominator problem, "
            f"not a delivery")

    rows = _ledger_rows(block)
    if rows is None:
        problems.append(
            "observations.ledger is not one well formed row per observation, "
            "so no count in the block can be recomputed from it")
        return problems
    if set(rows) != generated_set:
        missing = sorted(generated_set - set(rows))
        spare = sorted(set(rows) - generated_set)
        problems.append(
            f"observations.ledger does not cover generated_ids. Missing "
            f"{missing[:3]}, extra {spare[:3]}")
        return problems

    from_rows = {ident for ident, row in rows.items()
                 if row.get("delivered_at_s") is not None}
    if from_rows != delivered_set:
        problems.append(
            "observations.delivered_ids does not match the ids the ledger "
            "gives a delivery time. The list and the rows are two records of "
            "one thing and they disagree")

    problems.extend(_clock_problems(record, block, rows))
    problems.extend(_custody_problems(block, generated_set))
    return problems


def _clock_problems(record, block, rows) -> list:
    """The six times, their order, and the two counts taken between them."""
    problems = []
    clocks = {name: _number(block.get(name)) for name in CLOCK_ORDER}
    if any(value is None for value in clocks.values()):
        absent = sorted(name for name, value in clocks.items() if value is None)
        return [f"observations is missing a usable {', '.join(absent)}, and "
                f"the drain bound is measured between them"]

    ordered = [clocks[name] for name in CLOCK_ORDER]
    if not all(a <= b for a, b in zip(ordered, ordered[1:])):
        problems.append(
            "observations clocks run backwards. The outage opens, closes, the "
            "queue starts draining when the route is back, the backlog is "
            "gone, and the destination has everything, in that order: "
            + ", ".join(f"{n}={clocks[n]}" for n in CLOCK_ORDER))
    if clocks["outage_start_s"] >= clocks["outage_end_s"]:
        problems.append(
            f"the outage opens at {clocks['outage_start_s']} and closes at "
            f"{clocks['outage_end_s']}, so nothing was generated inside it")
    elapsed = _number(record.get("elapsed_sim_s"))
    if elapsed is not None and clocks["delivery_complete_s"] > elapsed:
        problems.append(
            f"the last observation arrived at {clocks['delivery_complete_s']}s "
            f"of a run that lasted {elapsed}s")

    measured = _number(block.get("backlog_drain_s"))
    if measured is None:
        problems.append("observations.backlog_drain_s is not a length of time")
    elif not math.isclose(measured, clocks["drain_end_s"] - clocks["drain_start_s"],
                          abs_tol=1e-6):
        problems.append(
            f"observations.backlog_drain_s is {measured} and drain_end_s minus "
            f"drain_start_s is {clocks['drain_end_s'] - clocks['drain_start_s']}")

    # The window counts, recomputed. Half open at the top, the same way
    # check_submission.py has always read it: an observation minted at the
    # instant the outage ended had somewhere to go.
    created = {ident: _number(row.get("created_at_s")) for ident, row in rows.items()}
    if any(value is None for value in created.values()):
        problems.append(
            "observations.ledger has a row with no creation time, so the "
            "outage window cannot be counted from the rows")
        return problems
    during = {ident for ident, when in created.items()
              if clocks["outage_start_s"] <= when < clocks["outage_end_s"]}
    after = {ident for ident in during
             if _number(rows[ident].get("delivered_at_s")) is not None
             and _number(rows[ident]["delivered_at_s"]) >= clocks["drain_start_s"]}
    for name, wanted in (("generated_during_outage", during),
                         ("delivered_after_restore", after)):
        count = block.get(name)
        if isinstance(count, int) and not isinstance(count, bool) \
                and count != len(wanted):
            problems.append(
                f"observations.{name} says {count} and the ledger places "
                f"{len(wanted)} ids there. The gate reads the first and the "
                f"run produced the second")
    return problems


def _custody_problems(block, generated_set) -> list:
    """What the custodian says it held, against what the swarm made.

    The custody claim is the one queue_drain rests on and the one a summary
    can most easily assert. A count with no list cannot be argued with, and a
    list naming ids nothing generated is the shape of a record that was
    edited.
    """
    problems = []
    ids = block.get("custodied_ids")
    count = block.get("custodied")
    if ids is None and count is None:
        return problems
    if not isinstance(ids, list):
        return ["observations.custodied is reported with no custodied_ids, so "
                "the number cannot be checked against anything"]
    held = set(ids)
    if len(held) != len(ids):
        problems.append("observations.custodied_ids holds the same id twice")
    if isinstance(count, int) and not isinstance(count, bool) \
            and count != len(held):
        problems.append(
            f"observations.custodied is {count} and custodied_ids holds "
            f"{len(held)} distinct ids")
    invented = held - generated_set
    if invented:
        problems.append(
            f"observations.custodied_ids names {len(invented)} ids no origin "
            f"generated, starting {sorted(invented)[:3]}. A custodian cannot "
            f"have held an observation the swarm never made")
    return problems
