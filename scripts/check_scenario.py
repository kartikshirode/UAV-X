#!/usr/bin/env python3
"""Check a scenario file is the one a gate was written for.

Round 7 finding 1: W1's chunks ran `scenarios/harness_check.yaml`, no chunk
produced it, and nothing said what it had to contain. A scenario is an input
to four chunk gates, so its shape is part of the contract in the same way the
run record's is.

The gate asserts on numbers that only mean something if the scenario supplies
them. `pose_sample_count>=100` is a claim about a 60 second run at the sampling
rate; against a 5 second scenario it is a different and much weaker claim, and
nothing would have said so.

    python3 scripts/check_scenario.py scenarios/harness_check.yaml \\
        --duration 60 --vehicles 4 --needs-injected-event

Exit 0 if the file matches, 1 if it does not, 2 if it cannot be read.
"""

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--vehicles", type=int)
    ap.add_argument("--needs-injected-event", action="store_true")
    a = ap.parse_args()

    path = Path(a.scenario)
    if not path.is_absolute():
        path = REPO / path
    if not path.is_file():
        print(f"  FAIL  no scenario at {a.scenario}")
        return 2

    try:
        import yaml
    except ImportError:
        print("  FAIL  pyyaml is not installed, so the scenario cannot be read")
        return 2
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"  FAIL  {a.scenario} is not valid YAML: {exc}")
        return 2
    if not isinstance(doc, dict):
        print(f"  FAIL  {a.scenario} does not parse to a mapping")
        return 2

    bad = 0
    if a.duration is not None:
        got = doc.get("duration_s")
        if got != a.duration:
            print(f"  FAIL  duration_s is {got!r}; the gate is written for "
                  f"{a.duration!r}, and its thresholds only mean what they say "
                  f"at that length")
            bad += 1
    if a.vehicles is not None:
        got = doc.get("vehicles")
        n = len(got) if isinstance(got, list) else got
        if n != a.vehicles:
            print(f"  FAIL  the scenario flies {n!r} vehicles, the gate is "
                  f"written for {a.vehicles}")
            bad += 1
    if a.needs_injected_event and not doc.get("injected_events"):
        print(f"  FAIL  {a.scenario} injects no events, so the chunk that "
              f"proves the injector works has nothing to observe")
        bad += 1

    if bad:
        return 1
    print(f"  ok    {path.name} is the scenario the gate was written for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
