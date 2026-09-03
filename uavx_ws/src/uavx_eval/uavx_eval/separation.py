"""The pairwise separation monitor, over sampled ground truth.

stage-1/architecture.md section 5 puts a separation monitor at 20 Hz behind the
altitude layers and the yield rule. Round 3 finding 8 is the reason it lives
here and reports its own sample count: `collision_contacts==0` passes when no
monitor was ever attached, and so does `separation_violations==0`. A zero that
nobody measured reads exactly like a zero that somebody did, so the report
carries how many frames it looked at and the gate asserts that number is above
zero.

Distances are 3D, which is what the coordinate frame section says and what the
altitude layers depend on: two vehicles 10 m apart in altitude and nowhere else
are separated, and a monitor working on the horizontal alone would say they
were not.

The floor is not written down in this file. It is a frozen number with one home
in `scripts/check_geometry.py`, and the caller passes it in, so a copy here
cannot drift away from the geometry that proves it.
"""

from __future__ import annotations

import math


class SeparationError(ValueError):
    """The monitor cannot report on what it was given."""


def pairwise_minimum(poses):
    """The closest pair in one frame, as (distance, id, id).

    Returns None when fewer than two vehicles were seen, because one vehicle
    has no separation and reporting a large number for it would be an invented
    measurement.
    """
    named = sorted((vid, xyz) for vid, xyz in poses.items()
                   if xyz is not None and all(math.isfinite(v) for v in xyz))
    if len(named) < 2:
        return None
    best = None
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            (a_id, a), (b_id, b) = named[i], named[j]
            distance = math.dist(a, b)
            if best is None or distance < best[0]:
                best = (distance, a_id, b_id)
    return best


class SeparationMonitor:
    """Sampled separation across a run.

    `separation_violations` counts sampled frames in which some pair was closer
    than the floor, not episodes and not pairs. The gates that read it ask for
    zero on every flying scenario and for at least one on
    `encounter_noyield.yaml`, and a frame count answers both without needing a
    rule about when one violation ends and the next begins.
    """

    def __init__(self, min_separation_m):
        if not (isinstance(min_separation_m, (int, float))
                and math.isfinite(min_separation_m) and min_separation_m > 0):
            raise SeparationError(
                f"min_separation_m is {min_separation_m!r}. The floor is a "
                f"positive distance frozen in architecture.md section 5 and "
                f"passed in from scripts/check_geometry.py.")
        self.min_separation_m = float(min_separation_m)
        self.samples = 0
        self.violations = 0
        self.closest = None
        self.closest_pair = None
        self.first_violation_s = None

    def offer(self, sim_time_s, poses) -> None:
        """Take one sampled frame of ground-truth positions."""
        found = pairwise_minimum(poses)
        if found is None:
            return
        distance, a_id, b_id = found
        self.samples += 1
        if self.closest is None or distance < self.closest:
            self.closest = distance
            self.closest_pair = (a_id, b_id)
        if distance < self.min_separation_m:
            self.violations += 1
            if self.first_violation_s is None:
                self.first_violation_s = float(sim_time_s)

    def report(self) -> dict:
        """The fields the run record carries for safety.

        `min_pairwise_separation_m` is absent rather than infinite when nothing
        was ever sampled. A run that watched no frames has no minimum, and
        writing one in would be the same defect this file exists to catch.
        """
        out = {
            "contact_monitor_samples": self.samples,
            "separation_violations": self.violations,
        }
        if self.closest is not None:
            out["min_pairwise_separation_m"] = self.closest
            out["min_pairwise_separation_pair"] = list(self.closest_pair)
        if self.first_violation_s is not None:
            out["first_separation_violation_s"] = self.first_violation_s
        return out
