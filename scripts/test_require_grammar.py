#!/usr/bin/env python3
"""The --require grammar, and every expression the gate actually writes.

Round 7 finding 7: `--require` appeared in four shapes across gate.sh and was
defined nowhere. A comparison against another field, a regular expression, a
list path and a plain number all looked alike, so two implementations of the
evaluator would have disagreed about what a gate asserts, and the gate is the
only acceptance contract in this project.

Two halves. The grammar cases below pin the behaviour. Then every `--require`
expression in gate.sh is parsed, so an expression the grammar cannot read is a
failure here rather than a week-three surprise.

    python3 scripts/test_require_grammar.py

Exit 0 if the grammar behaves and every gate expression parses.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from validate_record import COMPARISONS, check_require        # noqa: E402

RECORD = {
    "completion": "complete",
    # Chunk 1.4. Seventeen gate expressions read `==true` and the grammar had
    # no boolean case at all, so every one of them failed against a correct
    # record while `!=false` passed whichever way the flag was set.
    "injected_event_observed": True,
    "observations_set_equal": True,
    "relay_role_released": False,
    "separation_violations": 0,
    "pose_sample_count": 600,
    "elapsed_sim_s": 60.0,
    "vehicle_ids_observed": ["uav_1", "uav_2", "uav_3", "uav_4"],
    "resources": {"peak_rss_mib": 9000.0, "swap_used_mib": 0, "samples": 240},
    "handback": {
        "prepared_path": ["uav_4", "uav_2", "uav_1", "gcs"],
        "epoch_owner": "uav_4",
        "staying_member": "uav_4",
        "release_sender": "uav_4",
        "relay_role_holder": "uav_3",
        "confirmed_at": 241.5,
        "release_at": 243.0,
        "confirmed_observation_id": "uav_4:1182",
        "observation_gap_count": 0,
        "prepared_path_computations": 2,
    },
}

CASES = [
    # Booleans. A flag is not a number and not a string, and both of the
    # other branches got it wrong in a different direction.
    ("injected_event_observed==true", True, "a true flag against true"),
    ("relay_role_released==true", False, "a false flag against true, caught"),
    ("relay_role_released==false", True, "a false flag against false"),
    ("injected_event_observed==false", False, "a true flag against false, caught"),
    ("injected_event_observed!=false", True, "a true flag is not false"),
    ("relay_role_released!=false", False, "!= no longer passes either way"),
    ("injected_event_observed==1", False, "true is not the number one"),
    ("relay_role_released==0", False, "false is not the number zero"),
    ("separation_violations==0", True, "a real zero still compares as a number"),
    ("injected_event_observed>=1", False, "a flag has no order"),
    ("completion==true", False, "a string field against a boolean literal"),
    ("pose_sample_count==true", False, "a number field against a boolean literal"),
    ("observations_set_equal==injected_event_observed", True,
     "two flags that agree"),
    ("observations_set_equal==relay_role_released", False,
     "two flags that disagree, caught"),

    ("completion==complete", True, "string equality"),
    ("completion==crashed", False, "string inequality caught"),
    ("pose_sample_count>=100", True, "numeric floor"),
    ("pose_sample_count>=100000", False, "numeric floor that fails"),
    ("resources.peak_rss_mib>0", True, "a nested key"),
    ("resources.swap_used_mib==0", True, "a nested key at zero"),
    ("vehicle_ids_observed==uav_1,uav_2,uav_3,uav_4", True, "a list, comma joined"),
    ("vehicle_ids_observed==uav_1,uav_2", False, "a list that differs"),
    ("handback.prepared_path==uav_4,uav_2,uav_1,gcs", True, "the handback path"),
    ("handback.prepared_path==uav_4,uav_3,uav_1,gcs", False, "the wrong path caught"),
    ("handback.confirmed_at<handback.release_at", True, "field against field"),
    ("handback.release_at<handback.confirmed_at", False, "the wrong order caught"),
    ("handback.epoch_owner!=handback.relay_role_holder", True, "fields differ"),
    ("completion>crashed", False, "ordered comparison rejects text"),
    ("completion!=crashed", True, "string inequality remains valid"),
    ("resources.peak_rss_mib>nan", False, "non-finite numeric literal rejects"),
    ("handback.confirmed_observation_id=~^uav_4:[0-9]+$", True, "a regular expression"),
    ("handback.confirmed_observation_id=~^uav_9:[0-9]+$", False, "a regex that fails"),
    ("nope.missing==1", False, "an absent key is not a pass"),
    ("handback.nope>=1", False, "an absent nested key is not a pass"),
    ("garbage", False, "no operator at all"),
]


def main() -> int:
    bad = 0

    print("--- the grammar")
    for expr, want_ok, why in CASES:
        got_ok = check_require(RECORD, expr) == ""
        if got_ok != want_ok:
            bad += 1
            print(f"  FAIL  {expr:<48} {why}")
            print(f"           wanted {'a pass' if want_ok else 'a failure'}, "
                  f"got {'a pass' if got_ok else 'a failure'}")
        else:
            print(f"  ok    {expr:<48} {why}")

    # An expression the grammar cannot read would fail a correct run, and the
    # gate is where those expressions live.
    print("\n--- every --require in gate.sh parses")
    gate = (REPO / "scripts" / "gate.sh").read_text(encoding="utf-8")
    exprs = re.findall(r'--require\s+"([^"]+)"', gate)
    if len(exprs) < 30:
        print(f"  FAIL  found only {len(exprs)} --require expressions in "
              f"gate.sh. If they stopped being written this way, this half "
              f"stopped checking anything.")
        return 1
    unreadable = [e for e in exprs
                  if not any(op in e for op in COMPARISONS)]
    for e in unreadable:
        bad += 1
        print(f"  FAIL  {e!r} contains no operator the grammar defines")
    if not unreadable:
        print(f"  ok    all {len(exprs)} expressions use a defined operator")

    # And the one shape that silently means the wrong thing: a list written
    # with > separators parses as a comparison, so it can never match.
    print("\n--- no path written with > separators")
    arrows = [e for e in exprs if re.search(r"==.*>.*>", e)]
    for e in arrows:
        bad += 1
        print(f"  FAIL  {e!r} joins a path with '>', which the grammar reads "
              f"as an operator. Use commas.")
    if not arrows:
        print("  ok    every path is comma joined")

    print()
    if bad:
        print(f"FAILED: {bad} problem(s) in the --require contract")
        return 1
    print(f"the grammar holds, and all {len(exprs)} gate expressions parse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
