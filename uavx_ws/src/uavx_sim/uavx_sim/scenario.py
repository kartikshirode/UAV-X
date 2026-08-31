"""Load a scenario file and say what it means.

This module and scripts/check_scenario.py enforce one contract from two
directions, and that is deliberate. check_scenario.py is the gate's answer to
"is this file the one the gate was written for": it is handed the duration,
vehicle count and injected event a chunk's thresholds assume, it reports every
fault it finds, and it exits with a code. This module is the runner's answer to
"what does this file mean": it turns one file into a frozen object that
uavx_sim.scenario_runner can fly, and it raises on the first thing the contract
forbids rather than carrying on with a value it had to guess.

Neither replaces the other. A gate holding only the loader would learn that a
5 second scenario is well formed and never that `pose_sample_count>=100` is
meaningless against it, which is round 7 finding 1. A runner holding only the
gate would have to reimplement the rules to read the file at all, and the
reimplementation is where the two drift apart.

The contract is architecture.md section 1b, under `scripts/run_scenario.sh`:
required keys `name`, `seed`, `duration_s`, `vehicles`, `injected_events` and
`headless`; every event carries `type`, `target` and `at_s`. Unknown top level
keys are accepted and preserved so Stage 2 can add disturbances, but these keys
cannot change meaning.

No rclpy at module scope, on purpose. The rejections have to be provable by
`python3 -m pytest` on a clean checkout with nothing built, which is how
scripts/gate.sh runs the W1 contract tests.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Tuple

import yaml

VEHICLE_ID = re.compile(r"^uav_[0-9]+$")
EVENT_TYPES = ("kill", "comms_blackout", "gps_degrade")
REQUIRED_KEYS = ("name", "seed", "duration_s", "vehicles",
                 "injected_events", "headless")


class ScenarioError(ValueError):
    """A scenario file the contract forbids.

    The message names the offending key and what was wrong with it. A loader
    that only says "invalid scenario" sends whoever hit it back to reading the
    architecture document, and this is the error a run dies on before any
    simulator starts.
    """


@dataclass(frozen=True)
class Event:
    """One injected event, already checked against the scenario it belongs to."""

    type: str
    target: str
    at_s: float
    raw: Mapping[str, Any]


@dataclass(frozen=True)
class Scenario:
    """A scenario file that satisfied the contract.

    Frozen, because the runner passes it from the launcher to the injector to
    the record writer. A field edited along the way would leave the record
    describing something other than what flew.
    """

    name: str
    seed: int
    duration_s: float
    vehicles: Tuple[str, ...]
    injected_events: Tuple[Event, ...]
    headless: bool
    raw: Mapping[str, Any]


def _finite_number(value: Any) -> bool:
    """True for a real YAML number.

    `bool` is a subclass of `int` in Python, so without the first clause a
    seed of `true` reads as the integer 1 and an `at_s` of `false` as time
    zero. Both would load a scenario nobody wrote.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def load(path) -> Scenario:
    """Read `path` and return the Scenario it describes, or raise ScenarioError."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScenarioError(f"cannot read scenario {path}: {exc}") from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise ScenarioError(
            f"{path} does not parse to a mapping, got {type(doc).__name__}")
    return _from_mapping(doc, stem=path.stem, where=str(path))


def _from_mapping(doc: dict, stem: str, where: str) -> Scenario:
    # Same order as check_scenario.py, so on a file with several faults the two
    # report the same one first. The gate lists them all; this stops at one.
    for key in REQUIRED_KEYS:
        if key not in doc:
            raise ScenarioError(f"{where}: missing required key {key!r}")

    name = doc["name"]
    if not isinstance(name, str) or not name:
        raise ScenarioError(
            f"{where}: name must be a non-empty string, got {name!r}")
    if name != stem:
        raise ScenarioError(
            f"{where}: name {name!r} does not match the file stem {stem!r}. "
            f"A record cites its scenario by name, so a file carrying another "
            f"scenario's name produces a record pointing at the wrong run")

    seed = doc["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScenarioError(
            f"{where}: seed must be an integer, got {seed!r}. The link model is "
            f"seeded per run from this file, and a run nobody can replay is not "
            f"evidence")

    duration = doc["duration_s"]
    # Not coerced. A quoted 60 is an error and never a 60: the gate's
    # thresholds are written for a number, and quietly accepting the string
    # would hide a hand-edited file behind a passing run.
    if not _finite_number(duration) or duration <= 0:
        raise ScenarioError(
            f"{where}: duration_s must be a positive finite number, "
            f"got {duration!r}")

    vehicles = doc["vehicles"]
    if not isinstance(vehicles, list) or not vehicles:
        raise ScenarioError(
            f"{where}: vehicles must be a non-empty list, got {vehicles!r}")
    for index, vehicle in enumerate(vehicles):
        if not isinstance(vehicle, str) or not VEHICLE_ID.fullmatch(vehicle):
            raise ScenarioError(
                f"{where}: vehicles[{index}] is not a uav_<digits> id: "
                f"{vehicle!r}")
    duplicates = sorted({v for v in vehicles if vehicles.count(v) > 1})
    if duplicates:
        raise ScenarioError(
            f"{where}: vehicles repeats {', '.join(duplicates)}. Ids index the "
            f"per-vehicle blocks of the run record, so a repeat means two "
            f"vehicles writing one row")

    raw_events = doc["injected_events"]
    if not isinstance(raw_events, list):
        raise ScenarioError(
            f"{where}: injected_events must be a list, got {raw_events!r}")
    listed = set(vehicles)
    events = []
    for index, event in enumerate(raw_events):
        at = f"injected_events[{index}]"
        if not isinstance(event, dict):
            raise ScenarioError(f"{where}: {at} is not a mapping, got {event!r}")
        missing = [key for key in ("type", "target", "at_s") if key not in event]
        if missing:
            raise ScenarioError(f"{where}: {at} misses {', '.join(missing)}")

        event_type = event["type"]
        if event_type not in EVENT_TYPES:
            raise ScenarioError(
                f"{where}: {at} type {event_type!r} is not one of "
                f"{', '.join(EVENT_TYPES)}")

        target = event["target"]
        if not isinstance(target, str) or target not in listed:
            raise ScenarioError(
                f"{where}: {at} target {target!r} is not one of the listed "
                f"vehicles {', '.join(vehicles)}")

        at_s = event["at_s"]
        # Half open on purpose. An event at exactly duration_s fires as the run
        # ends or not at all, and the record cannot say which, so the gate that
        # requires injected_event_observed would be deciding a race.
        if not _finite_number(at_s) or at_s < 0 or at_s >= duration:
            raise ScenarioError(
                f"{where}: {at} at_s {at_s!r} is outside [0, {duration!r}), so "
                f"the event either never fires or fires after the run has ended")

        events.append(Event(type=event_type, target=target, at_s=at_s,
                            raw=MappingProxyType(dict(event))))

    headless = doc["headless"]
    if headless is not True:
        raise ScenarioError(
            f"{where}: headless must be exactly true, got {headless!r}. The GUI "
            f"path is not supported by this stack, and a run that opens one "
            f"measures a machine doing something else")

    # The whole parsed document, unknown keys included. Stage 2 adds its
    # disturbances there, and a loader that dropped what it did not recognise
    # would fly a quieter scenario than the one on disk while the record still
    # named the file.
    return Scenario(
        name=name,
        seed=seed,
        duration_s=duration,
        vehicles=tuple(vehicles),
        injected_events=tuple(events),
        headless=True,
        raw=MappingProxyType(doc),
    )
