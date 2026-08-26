#!/usr/bin/env python3
"""Check the frozen scenario geometry against the placement rule.

Round 2 finding 6: the plan's thresholds were asserted rather than derived, and
one of them could not have been met by a correct implementation. Numbers that
matter should not live only in prose where nobody re-checks them, so the
constants from stage-1/architecture.md live here too and this script proves they
are still consistent.

Run it standalone, or from the W3 and W4 gates once scenarios exist:

    python3 scripts/check_geometry.py

Exits non-zero on any violation. It does not need ROS, Gazebo or a simulator.
"""

import itertools
import math
import sys

# --- constants, mirroring stage-1/architecture.md sections 2, 4, 5 -----------
R_FULL = 400.0          # deterministic delivery at or under this
R_MAX = 500.0           # deterministic drop beyond this
USED_LINK_MAX = 350.0   # a link routing depends on: 50 m inside r_full
UNUSED_LINK_MIN = 600.0 # a link that must not exist: 100 m beyond r_max

CRUISE_SPEED = 10.0     # m/s
NEIGHBOUR_TIMEOUT = 3.0
LSA_PERIOD = 2.0
ELECTION_WINDOW = 1.0
SETTLE_ALLOWANCE = 4.0
STABILITY_WINDOW = 3.0
RECONNECT_GATE = 45.0   # what scripts/gate.sh asserts

POSITIONS = {
    "gcs":   (0.0, 0.0, 0.0),
    "uav_1": (330.0, 0.0, 30.0),
    "uav_2": (660.0, 0.0, 40.0),
    "uav_3": (640.0, 200.0, 50.0),
    "uav_4": (980.0, 0.0, 60.0),
}

# Links the routing depends on in the common geometry.
MUST_USE = [("gcs", "uav_1"), ("uav_1", "uav_2"), ("uav_2", "uav_4"), ("uav_2", "uav_3")]
# Links whose absence is what makes the relay necessary.
MUST_NOT_EXIST = [("gcs", "uav_2"), ("gcs", "uav_3"), ("gcs", "uav_4"), ("uav_1", "uav_4")]

failures = []


def dist(a, b):
    return math.dist(POSITIONS[a], POSITIONS[b])


def report(label, value, ok, unit="m"):
    print(f"  {'ok  ' if ok else 'FAIL'}  {label:<28} {value:8.1f} {unit}")
    if not ok:
        failures.append(label)


print(f"placement rule: used <= {USED_LINK_MAX:.0f} m, unused >= {UNUSED_LINK_MIN:.0f} m")
print(f"bands: full <= {R_FULL:.0f} m, fade to {R_MAX:.0f} m, out beyond\n")

print("links the routing must use")
for a, b in MUST_USE:
    d = dist(a, b)
    report(f"{a} to {b}", d, d <= USED_LINK_MAX)

print("\nlinks that must not exist")
for a, b in MUST_NOT_EXIST:
    d = dist(a, b)
    report(f"{a} to {b}", d, d >= UNUSED_LINK_MIN)

# Nothing a gate asserts on may sit in the fade band, because probabilistic
# delivery there turns a code failure and an unlucky draw into the same result.
print("\nno asserted pair may sit in the fade band")
asserted = set(tuple(sorted(p)) for p in MUST_USE + MUST_NOT_EXIST)
fade = []
for a, b in itertools.combinations(POSITIONS, 2):
    d = dist(a, b)
    if R_FULL < d <= R_MAX and tuple(sorted((a, b))) in asserted:
        fade.append((a, b, d))
        failures.append(f"{a}-{b} in fade band")
if fade:
    for a, b, d in fade:
        print(f"  FAIL  {a} to {b} at {d:.1f} m is in the fade band")
else:
    print("  ok    none")

# The relay slot is a computed midpoint, not a chosen point. Recompute it.
print("\nrelay_kill: computed slot and the recovery budget")
slot = tuple((POSITIONS["uav_1"][i] + POSITIONS["uav_4"][i]) / 2 for i in range(3))
print(f"        slot = ({slot[0]:.0f}, {slot[1]:.0f}, {slot[2]:.0f})")
d1 = math.dist(POSITIONS["uav_1"], slot)
d2 = math.dist(slot, POSITIONS["uav_4"])
report("uav_1 to slot", d1, d1 <= USED_LINK_MAX)
report("slot to uav_4", d2, d2 <= USED_LINK_MAX)

flight = math.dist(POSITIONS["uav_3"], slot)
flight_time = flight / CRUISE_SPEED
report("uav_3 flight to slot", flight, True)

budget = (
    NEIGHBOUR_TIMEOUT + LSA_PERIOD + ELECTION_WINDOW
    + flight_time + SETTLE_ALLOWANCE + STABILITY_WINDOW
)
print(f"\n  derived reconnect budget  {budget:.1f} s")
print(f"  gate asserts              {RECONNECT_GATE:.1f} s")
margin = (RECONNECT_GATE - budget) / budget * 100
report("margin over derived budget", margin, RECONNECT_GATE > budget, unit="%")

if failures:
    print(f"\nFAILED: {len(failures)} geometry constraint(s) violated")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("\nall geometry constraints satisfied")
sys.exit(0)
