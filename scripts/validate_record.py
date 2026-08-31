#!/usr/bin/env python3
"""Validate a run record against the schema, with nothing else in the way.

W1.3 needs to prove the record writer produces something the provenance
contract accepts. `uavx_eval.check` does that too, and more, but it does not
exist until W2, so the first week that writes a record would have had nothing
to check it with and the chunk would have been a promise.

This reads the schema and says yes or no. It deliberately does not look at a
single metric: metrics are meaningless until provenance holds, and provenance
is all a record writer is responsible for.

    python3 scripts/validate_record.py runs/latest.jsonl
    python3 scripts/validate_record.py runs/latest.jsonl \
        --require "completion==complete" --require "pose_sample_count>=100"

Round 7 finding 1: W1's chunks asserted their fields through `check_run`,
which runs `uavx_eval.check`, and `uavx_eval` is not built until W2.2. An
otherwise correct W1 could not pass its own gates. So --require lives here
too, with the same grammar, and W1 depends on nothing W1 does not build.

The two are not redundant. This checks a record against the schema and the
values asked of it. `uavx_eval.check` additionally verifies provenance against
the working tree and the scenario, which is what W2 onward needs and what
chunk 2.2 proves by making it reject a record.

Exit 0 valid, 1 invalid, 2 unreadable.
"""

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from jsonschema_mini import validate                        # noqa: E402

SCHEMA = REPO / "scenarios" / "run-record.schema.json"


# ------------------------------------------------------------ the grammar
# Round 7 finding 7: `--require` was used in four shapes across gate.sh and
# defined nowhere, so two implementations of the evaluator would disagree about
# what a gate asserts. This is the definition. `uavx_eval.check` must read the
# same shapes, and scripts/test_require_grammar.py holds both to it.
#
#   key==value      equal. Numeric if both sides parse as numbers, else string.
#   key!=value      not equal.
#   key>=n  key<=n  key>n  key<n     numeric only.
#   key=~regex      the value matches this Python regular expression.
#   key<other.key   either side may be a dotted path into the record. If the
#                   right side resolves to a field, the two fields are compared.
#
# `key` is a dotted path: `resources.peak_rss_mib`. A list value is compared as
# its comma-joined form, so a path is written `uav_4,uav_2,uav_1,gcs`. Commas,
# not `>`, because `>` is an operator and `a==x>y` would be ambiguous.
#
# Longest operators first, so `>=` is never read as `>`.
COMPARISONS = ["==", "!=", ">=", "<=", "=~", ">", "<"]


def dig(record, path: str):
    cur = record
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None, False
        cur = cur[part]
    return cur, True


def flat(value):
    """A list is its comma-joined form. Everything else is itself."""
    if isinstance(value, (list, tuple)):
        return ",".join(str(x) for x in value)
    return value


def finite_number(value):
    """Return a float for a finite number, or None for a non-number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def check_require(record: dict, expr: str) -> str:
    for op in COMPARISONS:
        if op not in expr:
            continue
        key, want = expr.split(op, 1)
        key, want = key.strip(), want.strip()
        got, found = dig(record, key)
        if not found:
            return f"{key} is not in the record"
        got = flat(got)

        if op == "=~":
            if re.search(want, str(got)):
                return ""
            return f"{key} is {str(got)!r}, which does not match /{want}/"

        # The right side may name another field. That is how the gate asserts
        # confirmed_at < release_at, which is the whole make-before-break claim
        # and cannot be written against a literal.
        other, other_found = dig(record, want)
        if other_found:
            want_val = flat(other)
            label = f"{want} ({want_val!r})"
        else:
            want_val = want
            label = repr(want)

        got_n, want_n = finite_number(got), finite_number(want_val)
        if op in (">=", "<=", ">", "<"):
            if got_n is None or want_n is None:
                return (f"{key} and {label} must be finite numbers for "
                        f"the {op} comparison")
            ok = {">=": got_n >= want_n, "<=": got_n <= want_n,
                  ">": got_n > want_n, "<": got_n < want_n}[op]
            shown = got_n
        elif got_n is not None and want_n is not None:
            ok = {"==": got_n == want_n, "!=": got_n != want_n}[op]
            shown = got_n
        else:
            got_s, want_s = str(got), str(want_val)
            ok = {"==": got_s == want_s, "!=": got_s != want_s}[op]
            shown = got_s
        if ok:
            return ""
        return f"{key} is {shown!r}, wanted {op} {label}"
    return (f"{expr!r} has no comparison in it. One of "
            f"{' '.join(COMPARISONS)}.")


def semantic_errors(record: dict) -> list[str]:
    """Checks JSON Schema cannot express in the small local validator."""
    errors = []
    requested = finite_number(record.get("requested_duration_s"))
    elapsed = finite_number(record.get("elapsed_sim_s"))
    if (record.get("completion") == "complete" and requested is not None
            and elapsed is not None and elapsed < 0.95 * requested):
        errors.append(f"complete run covered {elapsed}s of requested {requested}s, "
                      f"below the accepted 95 percent duration")

    try:
        started = datetime.fromisoformat(
            str(record.get("started_at", "")).replace("Z", "+00:00"))
        ended = datetime.fromisoformat(
            str(record.get("ended_at", "")).replace("Z", "+00:00"))
        if ended < started:
            errors.append("ended_at is earlier than started_at")
    except (TypeError, ValueError):
        pass

    vehicles = record.get("vehicle_ids_observed")
    if isinstance(vehicles, list) and len(set(vehicles)) != len(vehicles):
        errors.append("vehicle_ids_observed contains duplicate ids")

    if requested is not None:
        for index, event in enumerate(record.get("injected_events") or []):
            if not isinstance(event, dict):
                continue
            requested_t = finite_number(event.get("requested_t"))
            observed_t = finite_number(event.get("observed_t"))
            if requested_t is not None and not 0 <= requested_t < requested:
                errors.append(f"injected_events[{index}].requested_t is outside "
                              f"the requested run")
            if event.get("observed_t") is not None and observed_t is not None:
                if elapsed is not None and not 0 <= observed_t <= elapsed:
                    errors.append(f"injected_events[{index}].observed_t is outside "
                                  f"the elapsed run")
    return errors


def main() -> int:
    args = sys.argv[1:]
    requires = []
    while "--require" in args:
        i = args.index("--require")
        if i + 1 >= len(args):
            print("--require needs an expression")
            return 2
        requires.append(args[i + 1])
        del args[i:i + 2]
    if len(args) != 1:
        print("usage: validate_record.py <record.jsonl> [--require EXPR ...]")
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"  FAIL  no record at {path}")
        return 2
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  FAIL  {path.name} is not valid JSON: {exc}")
        return 2
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  FAIL  cannot read {SCHEMA}: {exc}")
        return 2

    errors = validate(record, schema)
    errors += semantic_errors(record)
    if errors:
        print(f"  FAIL  {path.name} does not satisfy the run record contract:")
        for e in errors[:12]:
            print(f"          {e}")
        if len(errors) > 12:
            print(f"          and {len(errors) - 12} more")
        return 1

    print(f"  ok    {path.name} satisfies scenarios/run-record.schema.json, "
          f"run {record.get('run_id')}")

    bad = 0
    for expr in requires:
        why = check_require(record, expr)
        if why:
            print(f"  FAIL  {expr}: {why}")
            bad += 1
        else:
            print(f"  ok    {expr}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
