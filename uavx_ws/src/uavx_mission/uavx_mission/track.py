"""One straight line at one speed, which is the whole of a track flight.

Chunk 4.5. `encounter.yaml` is the only scenario that flies these, and the
reason it exists is that altitude layers would otherwise keep every pair apart
and leave the yield rule as code nothing runs. Two vehicles are commanded into
one layer on crossing paths, both legs are 240 m and both start together, so
neither arrives first and the safe outcome cannot come from one of them
happening to be late.

The geometry lives here rather than in the scenario package because two things
need it and they need the same answer. `uavx_sim.work` reads a scenario and
validates the pair; the mission executor flies one. A second copy of the
interpolation would agree with the first until somebody fixed a rounding in one
of them, and the run would then be scored against a line it did not fly.

`position_at` is the commanded path and never the flown one. A vehicle that
yields is behind it, which is the entire difference between `encounter.yaml`
and its control.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

Point = Tuple[float, float, float]


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
