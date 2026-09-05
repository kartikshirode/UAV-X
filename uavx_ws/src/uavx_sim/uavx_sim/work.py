"""Chunk 4.1: what each vehicle in a scenario has been given to do.

Weeks 2 and 3 each had one answer and neither needed this module. Every
vehicle in `survey_baseline` flies a strip, and every vehicle in
`relay_required` holds a point, so "what is this vehicle for" was a property
of the scenario rather than of the vehicle.

Week 4 breaks that in two places. `mission_integrated` has two vehicles
surveying while two hold the chain up, and `encounter` has two vehicles flying
straight lines that cross. So the question is now per vehicle, and the answer
has to be exactly one of three things.

Exactly one, and this is the whole point of the module. A vehicle with no work
would take off and hover somewhere the design never mentions while the record
said nothing about it. A vehicle with two would be given two places to be, and
which one it flew to would come down to which block the runner happened to
read first.

    assignments   one of station, track or survey for every vehicle, refused
                  if any vehicle has none or more than one
    tracks_of     the frozen straight lines of the encounter pair
    yield_enabled whether the yield rule is armed

Nothing here starts a process or reads a clock. The runner asks these
questions before it launches anything, so a scenario that contradicts itself
costs a file read rather than a bring-up.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Mapping, Sequence, Tuple

Point = Tuple[float, float, float]

STATION = "station"
TRACK = "track"
SURVEY = "survey"

# The fourth answer, and it is only ever right for one scenario.
# `harness_check` flies four vehicles up, holds them and brings them down to
# prove the harness works, and it is never cited as evidence for anything.
# A vehicle with nothing to do is a defect in every scenario that claims a
# topology and it is the entire content of that one.
HOVER = "hover"

# The order they are reported in, which is also the order a reader of a
# failure message meets them.
KINDS = (STATION, TRACK, SURVEY, HOVER)

# A track whose two ends are this close is not a line, it is a point with two
# spellings, and the vehicle would be commanded to fly nowhere at a speed.
MIN_TRACK_M = 1.0


class WorkError(ValueError):
    """A scenario that gives a vehicle no work, or more than one job."""


def _finite(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class Track:
    """A straight line at a constant speed, started at a stated time.

    The encounter pair is frozen as two of these. Both are 240 m and both
    start together, so neither vehicle arrives at the crossing point first and
    the safe outcome cannot come from one of them happening to be late.
    """

    start: Point
    end: Point
    start_s: float
    speed_mps: float

    @property
    def length_m(self) -> float:
        return math.dist(self.start, self.end)

    @property
    def duration_s(self) -> float:
        return self.length_m / self.speed_mps

    @property
    def arrival_s(self) -> float:
        return self.start_s + self.duration_s

    def position_at(self, t_s: float) -> Point:
        """Where an unimpeded vehicle would be at `t_s`.

        Unimpeded is the word that matters. This is the commanded path and
        not the flown one: a vehicle that yields is behind it, which is what
        makes the encounter run different from its control.
        """
        if t_s <= self.start_s:
            return self.start
        travelled = (t_s - self.start_s) * self.speed_mps
        if travelled >= self.length_m:
            return self.end
        f = travelled / self.length_m
        return tuple(a + (b - a) * f            # type: ignore[return-value]
                     for a, b in zip(self.start, self.end))

    def as_record(self) -> dict:
        return {
            "start_enu": list(self.start),
            "end_enu": list(self.end),
            "start_s": self.start_s,
            "speed_mps": self.speed_mps,
            "length_m": round(self.length_m, 3),
            "arrival_s": round(self.arrival_s, 3),
        }


def _point(raw, where: str) -> Point:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise WorkError(f"{where} is {raw!r} and must be three numbers")
    if not all(_finite(v) for v in raw):
        raise WorkError(
            f"{where} is {raw!r}; every component has to be a finite number "
            f"of metres")
    return (float(raw[0]), float(raw[1]), float(raw[2]))


def tracks_of(raw: Mapping, vehicles: Sequence[str],
              altitudes: Mapping) -> Dict[str, Track]:
    """The `tracks` block, or an empty mapping when there is none."""
    block = raw.get("tracks") if isinstance(raw, Mapping) else None
    if block is None:
        return {}
    if not isinstance(block, Mapping):
        raise WorkError(f"tracks is {block!r} and must be a mapping of "
                        f"vehicle id to a straight line")

    extra = sorted(set(block) - set(vehicles))
    if extra:
        raise WorkError(
            f"tracks names {', '.join(extra)}, which the scenario does not "
            f"fly. A path for an absent vehicle is a flight this run cannot "
            f"produce")

    out: Dict[str, Track] = {}
    for vehicle in vehicles:
        line = block.get(vehicle)
        if line is None:
            continue
        if not isinstance(line, Mapping):
            raise WorkError(f"tracks[{vehicle!r}] is {line!r} and must be a "
                            f"mapping")
        missing = [k for k in ("start_enu", "end_enu", "start_s", "speed_mps")
                   if k not in line]
        if missing:
            raise WorkError(
                f"tracks[{vehicle!r}] misses {', '.join(missing)}. A path with "
                f"a default in it is a path nobody wrote down")

        start = _point(line["start_enu"], f"tracks[{vehicle!r}].start_enu")
        end = _point(line["end_enu"], f"tracks[{vehicle!r}].end_enu")

        start_s = line["start_s"]
        if not _finite(start_s) or float(start_s) < 0:
            raise WorkError(
                f"tracks[{vehicle!r}].start_s is {start_s!r} and must be a "
                f"time at or after zero")
        speed = line["speed_mps"]
        if not _finite(speed) or float(speed) <= 0:
            raise WorkError(
                f"tracks[{vehicle!r}].speed_mps is {speed!r} and must be a "
                f"positive speed")

        track = Track(start=start, end=end, start_s=float(start_s),
                      speed_mps=float(speed))
        if track.length_m < MIN_TRACK_M:
            raise WorkError(
                f"tracks[{vehicle!r}] runs {track.length_m:.3f} m from "
                f"{start} to {end}. That is a point with two spellings, and "
                f"a vehicle commanded along it flies nowhere at "
                f"{track.speed_mps} m/s")

        layer = altitudes.get(vehicle) if isinstance(altitudes, Mapping) else None
        if layer is None:
            raise WorkError(
                f"{vehicle} has a track and no hover altitude. The runner "
                f"climbs to one and the track names another, and with only "
                f"one of them present nothing compares the two")
        if not _finite(layer):
            raise WorkError(
                f"hover_altitudes_m[{vehicle!r}] is {layer!r}, not a number")
        for name, point in (("start_enu", start), ("end_enu", end)):
            if abs(point[2] - float(layer)) > 1e-6:
                raise WorkError(
                    f"{vehicle} climbs to {float(layer)} m and its "
                    f"{name} is at {point[2]} m. One of the two is what the "
                    f"run flies and the record cannot say which")
        out[vehicle] = track
    return out


def survey_vehicles_of(raw: Mapping, vehicles: Sequence[str]) -> Tuple[str, ...]:
    """Which vehicles fly the survey box, or all of them, or none.

    A `survey` block with no vehicle list means every vehicle in the scenario,
    which is what `survey_baseline` says by saying nothing. `mission_integrated`
    names two because the other two are holding the chain up.
    """
    block = raw.get("survey") if isinstance(raw, Mapping) else None
    if block is None:
        return ()
    if not isinstance(block, Mapping):
        raise WorkError(f"survey is {block!r} and must be a mapping")
    listed = block.get("vehicles")
    if listed is None:
        return tuple(vehicles)
    if not isinstance(listed, (list, tuple)) or not listed:
        raise WorkError(
            f"survey.vehicles is {listed!r} and must be a non-empty list of "
            f"vehicle ids. A survey nobody flies is not a survey")
    extra = sorted(set(listed) - set(vehicles))
    if extra:
        raise WorkError(
            f"survey.vehicles names {', '.join(extra)}, which the scenario "
            f"does not fly")
    duplicates = sorted({v for v in listed if list(listed).count(v) > 1})
    if duplicates:
        raise WorkError(
            f"survey.vehicles repeats {', '.join(duplicates)}. One vehicle "
            f"flies one strip, and a repeat means two strips assigned to one "
            f"aircraft")
    return tuple(v for v in vehicles if v in set(listed))


def stationed_of(raw: Mapping, vehicles: Sequence[str]) -> Tuple[str, ...]:
    """Which vehicles the comms block gives a point to hold."""
    block = raw.get("comms") if isinstance(raw, Mapping) else None
    if not isinstance(block, Mapping):
        return ()
    stations = block.get("stations")
    if not isinstance(stations, Mapping):
        return ()
    return tuple(v for v in vehicles if v in stations)


def claims_topology(raw: Mapping) -> bool:
    """Whether this scenario asserts anything about where vehicles are.

    A scenario with its radio on is making a claim about a graph, and every
    node of that graph has to be somewhere the design put it. A scenario with
    no radio is claiming nothing, which is the only reason `harness_check` is
    allowed four vehicles that just hover.
    """
    block = raw.get("comms") if isinstance(raw, Mapping) else None
    return isinstance(block, Mapping) and block.get("enabled") is True


def assignments(raw: Mapping, vehicles: Sequence[str],
                altitudes: Mapping) -> Dict[str, str]:
    """One job per vehicle, refused if any vehicle has none or two.

    The three sources are three different blocks of the scenario and none of
    them knows about the others, so this is the only place the question gets a
    single answer.
    """
    if not vehicles:
        raise WorkError("a scenario with no vehicles has no work to assign")

    held = set(stationed_of(raw, vehicles))
    flown = set(tracks_of(raw, vehicles, altitudes))
    surveyed = set(survey_vehicles_of(raw, vehicles))
    topology = claims_topology(raw)

    out: Dict[str, str] = {}
    for vehicle in vehicles:
        jobs = [kind for kind, group in
                ((STATION, held), (TRACK, flown), (SURVEY, surveyed))
                if vehicle in group]
        if not jobs:
            if not topology:
                out[vehicle] = HOVER
                continue
            raise WorkError(
                f"{vehicle} has no work. Every vehicle in a scenario with a "
                f"radio holds a station, flies a track or surveys, and one "
                f"with none of the three takes off, hovers where the design "
                f"never put it, and joins a graph the run then reports "
                f"delivery numbers over")
        if len(jobs) > 1:
            raise WorkError(
                f"{vehicle} is given {' and '.join(jobs)}. A vehicle with two "
                f"jobs has two places to be, and which one it flies to comes "
                f"down to the order the runner reads the blocks in")
        out[vehicle] = jobs[0]
    return out


def yield_enabled(raw: Mapping) -> bool:
    """Whether the yield rule is armed for this run.

    Absent means on. The encounter pair states it both ways because the pair
    differ in this flag and in nothing else, and a scenario that says nothing
    gets the safe answer rather than a swarm flying without its safety rule
    because a line was left out.
    """
    block = raw.get("safety") if isinstance(raw, Mapping) else None
    if block is None:
        return True
    if not isinstance(block, Mapping):
        raise WorkError(f"safety is {block!r} and must be a mapping")
    if "yield_enabled" not in block:
        return True
    value = block["yield_enabled"]
    if not isinstance(value, bool):
        raise WorkError(
            f"safety.yield_enabled is {value!r} and must be true or false. "
            f"encounter_noyield differs from encounter in exactly this flag, "
            f"so it may not be left to a default")
    return bool(value)
