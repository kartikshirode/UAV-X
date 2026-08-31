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
import math
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VEHICLE_ID = re.compile(r"^uav_[0-9]+$")
EVENT_TYPES = {"kill", "comms_blackout", "gps_degrade"}


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
    # Round 8, found while closing the message and runner contract. The old
    # checker only looked at values named by one W1 gate, so a malformed event,
    # duplicate vehicle id or a non-headless scenario could pass and leave the
    # runner to invent its meaning. These are the stable keys every Stage 1
    # scenario has to carry. Extra keys remain available for later stages.
    required = ("name", "seed", "duration_s", "vehicles",
                "injected_events", "headless")
    for key in required:
        if key not in doc:
            print(f"  FAIL  {a.scenario} has no required key {key!r}")
            bad += 1

    name = doc.get("name")
    if not isinstance(name, str) or not name:
        print(f"  FAIL  name must be a non-empty string, got {name!r}")
        bad += 1
    elif name != path.stem:
        print(f"  FAIL  scenario name {name!r} does not match {path.stem!r}")
        bad += 1

    seed = doc.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        print(f"  FAIL  seed must be an integer, got {seed!r}")
        bad += 1

    duration = doc.get("duration_s")
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(float(duration)) or duration <= 0):
        print(f"  FAIL  duration_s must be a positive finite number, got {duration!r}")
        bad += 1

    vehicles = doc.get("vehicles")
    if not isinstance(vehicles, list) or not vehicles:
        print(f"  FAIL  vehicles must be a non-empty list, got {vehicles!r}")
        bad += 1
        vehicle_set = set()
    else:
        string_vehicles = [v for v in vehicles if isinstance(v, str)]
        if len(set(string_vehicles)) != len(string_vehicles):
            print("  FAIL  vehicles contains duplicate ids")
            bad += 1
        for vehicle in vehicles:
            if not isinstance(vehicle, str) or not VEHICLE_ID.fullmatch(vehicle):
                print(f"  FAIL  invalid vehicle id {vehicle!r}")
                bad += 1
        vehicle_set = set(string_vehicles)

    events = doc.get("injected_events")
    if not isinstance(events, list):
        print(f"  FAIL  injected_events must be a list, got {events!r}")
        bad += 1
        events = []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            print(f"  FAIL  injected_events[{index}] is not a mapping")
            bad += 1
            continue
        missing = [key for key in ("type", "target", "at_s") if key not in event]
        if missing:
            print(f"  FAIL  injected_events[{index}] misses {', '.join(missing)}")
            bad += 1
            continue
        if event.get("type") not in EVENT_TYPES:
            print(f"  FAIL  injected_events[{index}] has unknown type {event.get('type')!r}")
            bad += 1
        if event.get("target") not in vehicle_set:
            print(f"  FAIL  injected_events[{index}] targets {event.get('target')!r}, not a listed vehicle")
            bad += 1
        at = event.get("at_s")
        if (isinstance(at, bool) or not isinstance(at, (int, float))
                or not math.isfinite(float(at)) or at < 0
                or (isinstance(duration, (int, float)) and at >= duration)):
            print(f"  FAIL  injected_events[{index}] at_s is outside the scenario: {at!r}")
            bad += 1

    if doc.get("headless") is not True:
        print("  FAIL  headless must be true. The GUI path is not supported by this stack")
        bad += 1

    if a.duration is not None:
        got = duration
        if got != a.duration:
            print(f"  FAIL  duration_s is {got!r}; the gate is written for "
                  f"{a.duration!r}, and its thresholds only mean what they say "
                  f"at that length")
            bad += 1
    if a.vehicles is not None:
        got = vehicles
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
