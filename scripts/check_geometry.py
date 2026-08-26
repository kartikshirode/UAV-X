#!/usr/bin/env python3
"""Prove the frozen topology does what the scenarios claim.

Round 3 finding 1 killed the previous version of this file. It checked eight
hand-picked pairs and reported green, while the two pairs it never listed,
uav_1 to uav_3 and uav_3 to uav_4, were both inside r_full. So the chain
uav_4 -> uav_3 -> uav_1 -> gcs already existed before the relay died, killing
uav_2 caused no outage, no election would have fired, and the 15% fault-recovery
row would have had no evidence behind it. The demo's centrepiece would not have
happened.

The lesson is the interesting part: a checker that only checks what its author
thought to list will confirm whatever its author already believed. So this one
builds the complete adjacency graph over every pair, and answers connectivity by
search rather than by assertion.

    python3 scripts/check_geometry.py

Exit 0 if every claim holds. Needs no ROS, Gazebo or simulator.
"""

import itertools
import math
import sys
from collections import deque

# --- radio, mirroring stage-1/architecture.md section 2 ---------------------
R_FULL = 200.0           # deterministic delivery at or under this
R_MAX = 250.0            # deterministic drop beyond this
USED_LINK_MAX = 175.0    # a link routing depends on: 25 m inside r_full
UNUSED_LINK_MIN = 300.0  # a link that must not exist: 50 m beyond r_max

# --- timings, architecture.md sections 3 and 4 ------------------------------
CRUISE_SPEED = 10.0
NEIGHBOUR_TIMEOUT = 3.0
LSA_PERIOD = 2.0
ELECTION_WINDOW = 1.0
SETTLE_ALLOWANCE = 4.0
STABILITY_WINDOW = 3.0
RECONNECT_GATE = 45.0

# --- frozen positions, architecture.md section 6 ----------------------------
START = {
    "gcs":   (0.0, 0.0, 0.0),
    "uav_1": (165.0, 0.0, 30.0),
    "uav_2": (330.0, 0.0, 40.0),
    "uav_3": (475.0, 75.0, 50.0),
    "uav_4": (475.0, -75.0, 60.0),
}

failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"  FAIL  {msg}")


def band(d: float) -> str:
    if d <= R_FULL:
        return "full"
    if d <= R_MAX:
        return "fade"
    return "out"


def adjacency(pos: dict) -> dict:
    """Links that exist at all, meaning anything not beyond r_max."""
    adj = {k: set() for k in pos}
    for a, b in itertools.combinations(pos, 2):
        if math.dist(pos[a], pos[b]) <= R_MAX:
            adj[a].add(b)
            adj[b].add(a)
    return adj


def path_to(pos: dict, src: str, dst: str) -> list | None:
    adj = adjacency(pos)
    seen, q = {src}, deque([[src]])
    while q:
        p = q.popleft()
        if p[-1] == dst:
            return p
        for n in sorted(adj[p[-1]]):
            if n not in seen:
                seen.add(n)
                q.append(p + [n])
    return None


def dump_matrix(pos: dict, title: str) -> None:
    print(f"\n{title}")
    for a, b in itertools.combinations(sorted(pos), 2):
        d = math.dist(pos[a], pos[b])
        print(f"    {a:<6} {b:<6} {d:8.1f} m   {band(d)}")


# ---------------------------------------------------------------- placement
print(f"radio: full <= {R_FULL:.0f} m, fade to {R_MAX:.0f} m, out beyond")
print(f"placement: used <= {USED_LINK_MAX:.0f} m, unused >= {UNUSED_LINK_MIN:.0f} m")

dump_matrix(START, "complete distance matrix at start (all 10 pairs)")

# Every pair must be unambiguous. A pair sitting in the fade band makes a code
# failure and an unlucky draw produce the same result, which is untestable.
print("\nno pair may sit in the fade band, or in the gap between the two rules")
for a, b in itertools.combinations(sorted(START), 2):
    d = math.dist(START[a], START[b])
    if d <= USED_LINK_MAX or d >= UNUSED_LINK_MIN:
        continue
    fail(f"{a} to {b} at {d:.1f} m is neither a usable link nor a clearly absent one")
if not failures:
    print("  ok    every pair is decisively present or decisively absent")

# ------------------------------------------------------- topology, as claimed
print("\ntopology before the kill")
p4 = path_to(START, "uav_4", "gcs")
p3 = path_to(START, "uav_3", "gcs")
print(f"    uav_4 to gcs: {' -> '.join(p4) if p4 else 'NO PATH'}")
print(f"    uav_3 to gcs: {' -> '.join(p3) if p3 else 'NO PATH'}")

for name, p in (("uav_4", p4), ("uav_3", p3)):
    if p is None:
        fail(f"{name} has no path to gcs before the kill; it should reach it via uav_2")
    elif "uav_2" not in p:
        fail(f"{name} reaches gcs without uav_2 ({' -> '.join(p)}), so killing the relay proves nothing")
    else:
        print(f"  ok    {name} depends on uav_2")

# --------------------------------------------------- topology after the kill
after = {k: v for k, v in START.items() if k != "uav_2"}
dump_matrix(after, "complete distance matrix after uav_2 is killed")

print("\ntopology after the kill, before anyone moves")
for name in ("uav_3", "uav_4"):
    p = path_to(after, name, "gcs")
    if p is None:
        print(f"  ok    {name} is disconnected, as intended")
    else:
        fail(f"{name} still reaches gcs as {' -> '.join(p)}. The kill caused no outage.")

# The disconnected side has to be able to talk among itself, or it cannot run
# an election and nothing will ever move.
d34 = math.dist(START["uav_3"], START["uav_4"])
if d34 <= USED_LINK_MAX:
    print(f"  ok    uav_3 and uav_4 can still hear each other at {d34:.1f} m, so they can elect")
else:
    fail(f"uav_3 to uav_4 is {d34:.1f} m, so the disconnected side cannot run an election")

if path_to(after, "uav_1", "gcs") is None:
    fail("uav_1 lost the gcs link too, so there is nothing to reconnect to")
else:
    print("  ok    uav_1 still holds the gcs link")

# ------------------------------------------------------------ the recovery
# The mover is the disconnected member nearest the attachment node. Ties break
# by lowest id. Recomputed here rather than asserted.
print("\nelection and slot, recomputed")
attach = "uav_1"
cands = sorted(("uav_3", "uav_4"), key=lambda n: (math.dist(START[n], START[attach]), n))
mover, stays = cands[0], cands[1]
print(f"    distance to {attach}: " + ", ".join(
    f"{n} {math.dist(START[n], START[attach]):.1f} m" for n in ("uav_3", "uav_4")))
print(f"    mover = {mover}, remaining = {stays}")

slot = tuple((START[attach][i] + START[stays][i]) / 2 for i in range(3))
print(f"    slot = ({slot[0]:.1f}, {slot[1]:.1f}, {slot[2]:.1f})")

d_up = math.dist(START[attach], slot)
d_dn = math.dist(slot, START[stays])
for label, d in ((f"{attach} to slot", d_up), (f"slot to {stays}", d_dn)):
    if d <= USED_LINK_MAX:
        print(f"  ok    {label:<20} {d:8.1f} m")
    else:
        fail(f"{label} is {d:.1f} m, beyond the {USED_LINK_MAX:.0f} m usable limit")

# Prove the moved topology actually restores the named path.
moved = {k: v for k, v in after.items()}
moved[mover] = slot
p = path_to(moved, stays, "gcs")
expected = [stays, mover, attach, "gcs"]
if p == expected:
    print(f"  ok    recovery path is {' -> '.join(p)}")
else:
    fail(f"after the move, {stays} reaches gcs as {p}, expected {expected}")

# ---------------------------------------------------------------- budget
flight = math.dist(START[mover], slot)
flight_time = flight / CRUISE_SPEED
budget = (NEIGHBOUR_TIMEOUT + LSA_PERIOD + ELECTION_WINDOW
          + flight_time + SETTLE_ALLOWANCE + STABILITY_WINDOW)

print("\nreconnect budget, derived")
print(f"    detect {NEIGHBOUR_TIMEOUT:.1f} + converge {LSA_PERIOD:.1f} + elect {ELECTION_WINDOW:.1f}")
print(f"    + fly {flight:.1f} m at {CRUISE_SPEED:.0f} m/s = {flight_time:.1f}")
print(f"    + settle {SETTLE_ALLOWANCE:.1f} + stable {STABILITY_WINDOW:.1f}")
print(f"    = {budget:.1f} s, gate asserts {RECONNECT_GATE:.0f} s")
if RECONNECT_GATE > budget:
    print(f"  ok    margin {(RECONNECT_GATE - budget) / budget * 100:.0f}%")
else:
    fail(f"gate of {RECONNECT_GATE:.0f} s is below the derived {budget:.1f} s and would fail correct code")

# ------------------------------------------------------------------- verdict
if failures:
    print(f"\nFAILED: {len(failures)} constraint(s) violated")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("\nall topology claims verified")
sys.exit(0)
