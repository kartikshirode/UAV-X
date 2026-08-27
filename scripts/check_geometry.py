#!/usr/bin/env python3
"""Prove the frozen topology does what the scenarios claim, including in motion.

Round 3 finding 1 killed the first version of this file. It checked eight
hand-picked pairs and reported green, while the two pairs it never listed,
uav_1 to uav_3 and uav_3 to uav_4, were both inside r_full. So the chain
uav_4 -> uav_3 -> uav_1 -> gcs already existed before the relay died, killing
uav_2 caused no outage, and the fault-recovery row would have had no evidence
behind it.

Round 4 finding 2 killed the second version the same way one level up. The fix
for round 3 enumerated all ten pairs of the STATIONARY layout and stopped there,
so the integrated mission, where two vehicles move for four minutes, was never
checked at all. Its survey box came within 240.8 m of uav_1, inside r_max, which
put a forbidden link in the fade band and made the relay kill a coin toss.

The lesson has now cost two rounds: a checker only checks the thing its author
thought to enumerate. So this one enumerates every pair at every sampled instant
of every frozen trajectory, and answers connectivity by search rather than by
assertion.

    python3 scripts/check_geometry.py

Exit 0 if every claim holds. Needs no ROS, Gazebo or simulator.

check_docs.py imports DERIVED_DISTANCES from here so that a number quoted in
prose is checked against the arithmetic rather than against a list somebody
maintained by hand.
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

# --- timings, architecture.md sections 3, 4 and 5 ---------------------------
CRUISE_SPEED = 10.0
SURVEY_SPEED = 3.0
NEIGHBOUR_TIMEOUT = 3.0
LSA_PERIOD = 2.0
ELECTION_WINDOW = 1.0
SETTLE_ALLOWANCE = 4.0
STABILITY_WINDOW = 3.0
RECONNECT_GATE = 45.0
MIN_SEPARATION = 10.0

# A relay slot has to clear every vehicle that is still flying, by more than the
# separation floor. The extra 5 m is for staleness: a node that has lost its
# radio sends no HELLO, so the swarm is steering around where it last saw it.
SLOT_CLEARANCE = 15.0
SLOT_RAISE_STEP = 5.0
SLOT_CEILING = 80.0

# The margin an election must win by. Two candidates a metre apart is a
# deterministic result on paper and a coin toss in SITL, where position hold
# carries its own error.
ELECTION_MARGIN_MIN = 5.0

# --- frozen positions, architecture.md section 6 ----------------------------
START = {
    "gcs":   (0.0, 0.0, 0.0),
    "uav_1": (165.0, 0.0, 30.0),
    "uav_2": (330.0, 0.0, 40.0),
    "uav_3": (475.0, 75.0, 50.0),
    "uav_4": (475.0, -75.0, 60.0),
}

# --- mission_integrated, architecture.md section 6 --------------------------
BOX_X0, BOX_X1 = 465.0, 490.0
BOX_Y0, BOX_Y1 = -60.0, 60.0
CELL = 5.0
SENSOR_R = 6.0
LANE_X = {"uav_3": (468.125, 474.375), "uav_4": (480.625, 486.875)}
SURVEY_ALT = {"uav_3": 50.0, "uav_4": 60.0}
T_TAKEOFF = 20.0
T_SURVEY = 25.0
T_KILL = 70.0
MISSION_DURATION = 240.0
SAMPLE_DT = 0.1

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


class Track:
    """A frozen trajectory: timed straight legs, linearly interpolated."""

    def __init__(self, start: tuple):
        self.legs: list[tuple] = []
        self.t = 0.0
        self.p = start

    def hold_until(self, t: float) -> "Track":
        if t > self.t:
            self.legs.append((self.t, t, self.p, self.p))
            self.t = t
        return self

    def go(self, p: tuple, speed: float) -> "Track":
        d = math.dist(self.p, p)
        t1 = self.t + d / speed
        self.legs.append((self.t, t1, self.p, p))
        self.t, self.p = t1, p
        return self

    def pos(self, t: float) -> tuple:
        if not self.legs or t <= self.legs[0][0]:
            return self.legs[0][2] if self.legs else self.p
        for t0, t1, p0, p1 in self.legs:
            if t0 <= t <= t1:
                f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                return tuple(p0[i] + f * (p1[i] - p0[i]) for i in range(3))
        return self.legs[-1][3]

    def flown_by(self, t: float) -> float:
        """Distance travelled up to t. Used to show work was left unfinished."""
        total = 0.0
        for t0, t1, p0, p1 in self.legs:
            if t <= t0:
                break
            leg = math.dist(p0, p1)
            total += leg if t >= t1 else leg * (t - t0) / (t1 - t0)
        return total


def minimax_slot(anchor: tuple, work: list) -> tuple:
    """The slot rule from architecture.md section 4.

    On the segment from the attachment node to the centroid of the component's
    work, at the point where the hop back equals the longest hop forward. When
    the work is one stationary point, that point is the midpoint, which is why
    relay_kill's answer does not change.
    """
    c = tuple(sum(p[i] for p in work) / len(work) for i in range(3))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        s = tuple(anchor[i] + mid * (c[i] - anchor[i]) for i in range(3))
        if math.dist(anchor, s) < max(math.dist(s, p) for p in work):
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2
    return tuple(anchor[i] + t * (c[i] - anchor[i]) for i in range(3))


def clear_slot(slot: tuple, live: list) -> tuple | None:
    """Raise the slot until it clears every vehicle that is still flying.

    The balance rule alone answers a routing question and says nothing about
    airspace. In the integrated mission it puts the relay 6.8 m from uav_2,
    inside the 10 m floor, and that goes unnoticed only because uav_2 is dead in
    every scenario that computes a slot. A radio failure leaves the vehicle
    flying, and then the swarm sends its replacement to sit on top of it.

    Raising rather than sliding along the segment: the hops here are about 170 m
    horizontally, so ten metres of altitude costs almost nothing in link budget,
    while sliding costs it directly. Raising rather than lowering because down
    is towards terrain and towards the anchor's own layer.
    """
    s = slot
    while any(math.dist(s, p) < SLOT_CLEARANCE for p in live):
        s = (s[0], s[1], s[2] + SLOT_RAISE_STEP)
        if s[2] > SLOT_CEILING:
            return None
    return s


def lane_path(vehicle: str, reverse_first: bool) -> list:
    """The boustrophedon one surveyor flies over its own strip.

    Two lanes the length of the box, joined by one turn. Lane spacing is 6.25 m
    against a 6 m sensor radius, so consecutive swaths overlap and the strip has
    no gap down its middle.
    """
    z = SURVEY_ALT[vehicle]
    x_a, x_b = LANE_X[vehicle]
    y_far, y_near = (BOX_Y1, BOX_Y0) if reverse_first else (BOX_Y0, BOX_Y1)
    return [
        (x_a, y_far, z),
        (x_a, y_near, z),
        (x_b, y_near, z),
        (x_b, y_far, z),
    ]


def build_integrated() -> dict:
    """Every frozen trajectory in mission_integrated, as timed tracks.

    uav_3 works the west strip from the north end, uav_4 the east strip from the
    south end, so at every instant they are mirrored in y and exactly 12.5 m
    apart in x. That is not decoration. It is what makes the election result
    hold by a usable margin instead of by the 0.8 m that separates the two
    station-keeping candidates in relay_kill.
    """
    tracks = {}

    # uav_1 and uav_2 station-keep at their common positions throughout.
    for v in ("gcs", "uav_1", "uav_2"):
        tracks[v] = Track(START[v]).hold_until(MISSION_DURATION)

    u3_wps = lane_path("uav_3", reverse_first=True)
    u4_wps = lane_path("uav_4", reverse_first=False)

    t3 = Track(START["uav_3"]).hold_until(T_TAKEOFF).go(u3_wps[0], CRUISE_SPEED)
    t3.hold_until(T_SURVEY)
    for wp in u3_wps[1:]:
        t3.go(wp, SURVEY_SPEED)

    t4 = Track(START["uav_4"]).hold_until(T_TAKEOFF).go(u4_wps[0], CRUISE_SPEED)
    t4.hold_until(T_SURVEY)
    for wp in u4_wps[1:]:
        t4.go(wp, SURVEY_SPEED)

    tracks["uav_3_survey_only"] = t3
    tracks["uav_4_survey_only"] = t4
    return tracks


DERIVED: dict[str, tuple] = {}


def note(name: str, value: float, unit: str = "m") -> float:
    """Record a derived figure so prose quoting it can be checked against it."""
    DERIVED[name] = (round(value, 1), unit)
    return value


def derived_distances() -> dict:
    """Every distance this file computes, in metres, for check_docs.py to read.

    Round 4 finding 9: check_docs.py carried a hand-maintained tuple of allowed
    numbers, so the stale-number class it exists to catch could walk straight
    back in behind any new derived figure. It imports this instead, and the unit
    tag keeps a count of seconds from licensing a wrong distance.
    """
    if not DERIVED:
        run(quiet=True)
    return {k: v for k, (v, u) in DERIVED.items() if u == "m"}


def run(quiet: bool = False) -> int:
    out = (lambda *a: None) if quiet else print
    ok = (lambda m: None) if quiet else (lambda m: print(f"  ok    {m}"))

    # ------------------------------------------------------------ placement
    out(f"radio: full <= {R_FULL:.0f} m, fade to {R_MAX:.0f} m, out beyond")
    out(f"placement: used <= {USED_LINK_MAX:.0f} m, unused >= {UNUSED_LINK_MIN:.0f} m")

    if not quiet:
        dump_matrix(START, "complete distance matrix at start (all 10 pairs)")
    for a, b in itertools.combinations(sorted(START), 2):
        note(f"pair:{a}-{b}", math.dist(START[a], START[b]))

    out("\nno pair may sit in the fade band, or in the gap between the two rules")
    before = len(failures)
    for a, b in itertools.combinations(sorted(START), 2):
        d = math.dist(START[a], START[b])
        if d <= USED_LINK_MAX or d >= UNUSED_LINK_MIN:
            continue
        fail(f"{a} to {b} at {d:.1f} m is neither a usable link nor a clearly absent one")
    if len(failures) == before:
        ok("every pair is decisively present or decisively absent")

    # ---------------------------------------------------- topology as claimed
    out("\ntopology before the kill")
    p4 = path_to(START, "uav_4", "gcs")
    p3 = path_to(START, "uav_3", "gcs")
    out(f"    uav_4 to gcs: {' -> '.join(p4) if p4 else 'NO PATH'}")
    out(f"    uav_3 to gcs: {' -> '.join(p3) if p3 else 'NO PATH'}")
    for name, p in (("uav_4", p4), ("uav_3", p3)):
        if p is None:
            fail(f"{name} has no path to gcs before the kill; it should reach it via uav_2")
        elif "uav_2" not in p:
            fail(f"{name} reaches gcs without uav_2 ({' -> '.join(p)}), so killing the relay proves nothing")
        else:
            ok(f"{name} depends on uav_2")

    after = {k: v for k, v in START.items() if k != "uav_2"}
    if not quiet:
        dump_matrix(after, "complete distance matrix after uav_2 is killed")

    out("\ntopology after the kill, before anyone moves")
    for name in ("uav_3", "uav_4"):
        p = path_to(after, name, "gcs")
        if p is None:
            ok(f"{name} is disconnected, as intended")
        else:
            fail(f"{name} still reaches gcs as {' -> '.join(p)}. The kill caused no outage.")

    d34 = note("relay_kill:uav_3-uav_4", math.dist(START["uav_3"], START["uav_4"]))
    if d34 <= USED_LINK_MAX:
        ok(f"uav_3 and uav_4 can still hear each other at {d34:.1f} m, so they can elect")
    else:
        fail(f"uav_3 to uav_4 is {d34:.1f} m, so the disconnected side cannot run an election")

    if path_to(after, "uav_1", "gcs") is None:
        fail("uav_1 lost the gcs link too, so there is nothing to reconnect to")
    else:
        ok("uav_1 still holds the gcs link")

    # -------------------------------------------------- relay_kill recovery
    out("\nrelay_kill: election and slot, recomputed")
    attach = "uav_1"
    cands = sorted(("uav_3", "uav_4"),
                   key=lambda n: (math.dist(START[n], START[attach]), n))
    mover, stays = cands[0], cands[1]
    out("    distance to " + attach + ": " + ", ".join(
        f"{n} {math.dist(START[n], START[attach]):.1f} m" for n in ("uav_3", "uav_4")))
    out(f"    mover = {mover}, remaining = {stays}")

    slot = minimax_slot(START[attach], [START[stays]])
    out(f"    slot = ({slot[0]:.1f}, {slot[1]:.1f}, {slot[2]:.1f})")

    d_up = note("relay_kill:uav_1-slot", math.dist(START[attach], slot))
    d_dn = note("relay_kill:slot-uav_4", math.dist(slot, START[stays]))
    for label, d in ((f"{attach} to slot", d_up), (f"slot to {stays}", d_dn)):
        if d <= USED_LINK_MAX:
            ok(f"{label:<20} {d:8.1f} m")
        else:
            fail(f"{label} is {d:.1f} m, beyond the {USED_LINK_MAX:.0f} m usable limit")

    moved = dict(after)
    moved[mover] = slot
    p = path_to(moved, stays, "gcs")
    expected = [stays, mover, attach, "gcs"]
    if p == expected:
        ok(f"recovery path is {' -> '.join(p)}")
    else:
        fail(f"after the move, {stays} reaches gcs as {p}, expected {expected}")

    flight = note("relay_kill:mover_flight", math.dist(START[mover], slot))
    budget = note("relay_kill:budget_s", reconnect_budget(flight), "s")
    out("\nrelay_kill reconnect budget, derived")
    out(f"    detect {NEIGHBOUR_TIMEOUT:.1f} + converge {LSA_PERIOD:.1f} + elect {ELECTION_WINDOW:.1f}")
    out(f"    + fly {flight:.1f} m at {CRUISE_SPEED:.0f} m/s = {flight / CRUISE_SPEED:.1f}")
    out(f"    + settle {SETTLE_ALLOWANCE:.1f} + stable {STABILITY_WINDOW:.1f}")
    out(f"    = {budget:.1f} s, gate asserts {RECONNECT_GATE:.0f} s")
    if RECONNECT_GATE > budget:
        ok(f"margin {(RECONNECT_GATE - budget) / budget * 100:.0f}%")
    else:
        fail(f"gate of {RECONNECT_GATE:.0f} s is below the derived {budget:.1f} s and would fail correct code")

    check_integrated(out, ok, quiet)
    check_link_loss(out, ok)
    check_rejected(out, ok, build_integrated())
    check_encounter(out, ok)
    check_survey_baseline(out, ok)

    return len(failures)


def reconnect_budget(flight_m: float) -> float:
    return (NEIGHBOUR_TIMEOUT + LSA_PERIOD + ELECTION_WINDOW
            + flight_m / CRUISE_SPEED + SETTLE_ALLOWANCE + STABILITY_WINDOW)


def check_integrated(out, ok, quiet: bool) -> None:
    """mission_integrated, checked in motion rather than at one instant."""
    out("\n" + "=" * 62)
    out("mission_integrated: the only scenario that proves the whole swarm")
    out("=" * 62)

    tracks = build_integrated()
    t3, t4 = tracks["uav_3_survey_only"], tracks["uav_4_survey_only"]
    survey_end = note("integrated:strip_seconds", t3.t, "s")
    out(f"    survey box   x {BOX_X0:.0f} to {BOX_X1:.0f}, y {BOX_Y0:.0f} to {BOX_Y1:.0f}"
        f"  ({BOX_X1 - BOX_X0:.0f} m by {BOX_Y1 - BOX_Y0:.0f} m)")
    out(f"    strip path   {t3.flown_by(1e9):.2f} m each, done at t = {survey_end:.1f} s")

    # 1. Coverage is geometrically reachable. A gate asking for 95% of cells is
    #    only fair if the frozen lanes can actually see them.
    before = len(failures)
    uncovered = []
    for cx in frange(BOX_X0 + CELL / 2, BOX_X1, CELL):
        for cy in frange(BOX_Y0 + CELL / 2, BOX_Y1, CELL):
            lanes = [x for v in LANE_X for x in LANE_X[v]]
            if min(abs(cx - lx) for lx in lanes) > SENSOR_R:
                uncovered.append((cx, cy))
    cells = int((BOX_X1 - BOX_X0) / CELL) * int((BOX_Y1 - BOX_Y0) / CELL)
    if uncovered:
        fail(f"{len(uncovered)} of {cells} cells are further than the {SENSOR_R:.0f} m "
             f"sensor radius from every lane, so 100% coverage is unreachable")
    else:
        ok(f"all {cells} cells lie within {SENSOR_R:.0f} m of a lane")

    # 2. Every pair, every 0.1 s, up to the kill. This is the check whose
    #    absence let a 240.8 m forbidden link into the frozen design.
    before = len(failures)
    worst_used, worst_absent = {}, {}
    for t in frange(0.0, T_KILL + SAMPLE_DT, SAMPLE_DT):
        pos = {v: tracks[v].pos(t) for v in ("gcs", "uav_1", "uav_2")}
        pos["uav_3"] = t3.pos(t)
        pos["uav_4"] = t4.pos(t)
        for a, b in itertools.combinations(sorted(pos), 2):
            d = math.dist(pos[a], pos[b])
            key = f"{a}-{b}"
            if key in ("uav_1-uav_2", "gcs-uav_1", "uav_2-uav_3", "uav_2-uav_4",
                       "uav_3-uav_4"):
                worst_used[key] = max(worst_used.get(key, 0.0), d)
            else:
                worst_absent[key] = min(worst_absent.get(key, 1e9), d)

    for key, d in sorted(worst_used.items()):
        note(f"integrated:max:{key}", d)
        if d <= USED_LINK_MAX:
            ok(f"{key:<14} peaks at {d:7.1f} m, inside the {USED_LINK_MAX:.0f} m usable limit")
        else:
            fail(f"{key} reaches {d:.1f} m during the mission, past the {USED_LINK_MAX:.0f} m "
                 f"limit for a link the routing depends on")
    for key, d in sorted(worst_absent.items()):
        note(f"integrated:min:{key}", d)
        if d >= UNUSED_LINK_MIN:
            ok(f"{key:<14} never closer than {d:7.1f} m, so it cannot appear")
        else:
            fail(f"{key} closes to {d:.1f} m during the mission. A link that must not exist "
                 f"is inside the {UNUSED_LINK_MIN:.0f} m rule and, at {band(d)}, is "
                 f"{'live' if d <= R_MAX else 'borderline'}")

    # 3. Separation, over the same samples.
    before = len(failures)
    sep = min(math.dist(t3.pos(t), t4.pos(t))
              for t in frange(0.0, min(t3.t, t4.t), SAMPLE_DT))
    note("integrated:min_separation", sep)
    if sep >= MIN_SEPARATION:
        ok(f"surveyors never closer than {sep:.1f} m, above the {MIN_SEPARATION:.0f} m floor")
    else:
        fail(f"surveyors close to {sep:.1f} m, below the {MIN_SEPARATION:.0f} m floor")

    # 4. The kill lands while there is still work to do. Round 4 finding 2: the
    #    previous kill time fell after the survey would already have finished,
    #    so nothing proved the swarm recovered a mission rather than a corpse.
    flown_at_kill = t3.flown_by(T_KILL)
    frac = flown_at_kill / t3.flown_by(1e9)
    note("integrated:coverage_at_kill", frac * 100, "pct")
    if 0.2 <= frac <= 0.8:
        ok(f"at the kill each surveyor has flown {frac * 100:.0f}% of its strip, so the "
           f"mission is genuinely unfinished")
    else:
        fail(f"at t={T_KILL:.0f} s each surveyor has flown {frac * 100:.0f}% of its strip. "
             f"The kill must land with work left and work done.")

    # 5. The election, at the moment it actually runs, and by how much.
    t_assign = T_KILL + NEIGHBOUR_TIMEOUT + LSA_PERIOD + ELECTION_WINDOW
    margin_min = 1e9
    wrong = 0
    for t in frange(T_SURVEY, min(t_assign, t3.t) + SAMPLE_DT, SAMPLE_DT):
        d3 = math.dist(t3.pos(t), START["uav_1"])
        d4 = math.dist(t4.pos(t), START["uav_1"])
        if d3 >= d4:
            wrong += 1
        margin_min = min(margin_min, d4 - d3)
    note("integrated:election_margin", margin_min)
    if wrong:
        fail(f"uav_4 is nearer the attachment node than uav_3 at {wrong} sampled instants, "
             f"so the winner depends on when the election happens")
    elif margin_min < ELECTION_MARGIN_MIN:
        fail(f"the election is decided by {margin_min:.1f} m at its closest, under the "
             f"{ELECTION_MARGIN_MIN:.0f} m a SITL run can be trusted to hold")
    else:
        ok(f"uav_3 is nearer the attachment node throughout, by at least {margin_min:.1f} m")

    p3_assign = t3.pos(t_assign)
    p4_assign = t4.pos(t_assign)
    note("integrated:uav_3_at_assign", math.dist(p3_assign, START["uav_1"]))
    note("integrated:uav_4_at_assign", math.dist(p4_assign, START["uav_1"]))
    out(f"    at the assign, t = {t_assign:.1f} s")
    out(f"      uav_3 ({p3_assign[0]:.2f}, {p3_assign[1]:.2f}, {p3_assign[2]:.0f}), "
        f"{math.dist(p3_assign, START['uav_1']):.1f} m from uav_1")
    out(f"      uav_4 ({p4_assign[0]:.2f}, {p4_assign[1]:.2f}, {p4_assign[2]:.0f}), "
        f"{math.dist(p4_assign, START['uav_1']):.1f} m from uav_1")

    # 6. The slot, from the same rule relay_kill uses. The staying member has an
    #    area rather than a position, so the work is the whole assigned box at
    #    its cruise altitude: the relay has to cover wherever uav_4 goes next,
    #    not where it happened to be when the relay died.
    z4 = SURVEY_ALT["uav_4"]
    corners = [(x, y, z4) for x in (BOX_X0, BOX_X1) for y in (BOX_Y0, BOX_Y1)]
    slot = minimax_slot(START["uav_1"], corners)
    hop_back = note("integrated:uav_1-slot", math.dist(START["uav_1"], slot))
    hop_fwd = note("integrated:slot-box", max(math.dist(slot, c) for c in corners))
    out(f"    slot = ({slot[0]:.1f}, {slot[1]:.1f}, {slot[2]:.1f}), balancing both hops")
    for label, d in (("uav_1 to slot", hop_back), ("slot to worst box corner", hop_fwd)):
        if d <= USED_LINK_MAX:
            ok(f"{label:<26} {d:7.1f} m")
        else:
            fail(f"{label} is {d:.1f} m, beyond the {USED_LINK_MAX:.0f} m usable limit")

    d_slot_gcs = note("integrated:slot-gcs", math.dist(slot, (0.0, 0.0, 0.0)))
    if d_slot_gcs >= UNUSED_LINK_MIN:
        ok(f"slot to gcs {d_slot_gcs:.1f} m, so the relay gains no accidental direct link")
    else:
        fail(f"slot is {d_slot_gcs:.1f} m from the gcs, close enough to reach it directly, "
             f"which would make the two-hop claim false")

    # 7. The recovery budget, from this scenario's own flight rather than
    #    relay_kill's. Round 4 finding 2: the two were quietly assumed equal.
    mover_flight = note("integrated:mover_flight", math.dist(p3_assign, slot))
    budget = note("integrated:budget_s", reconnect_budget(mover_flight), "s")
    out("\n    reconnect budget for this scenario")
    out(f"      fly {mover_flight:.1f} m at {CRUISE_SPEED:.0f} m/s = {mover_flight / CRUISE_SPEED:.1f} s")
    out(f"      total {budget:.1f} s, gate asserts {RECONNECT_GATE:.0f} s")
    if RECONNECT_GATE > budget:
        ok(f"margin {(RECONNECT_GATE - budget) / budget * 100:.0f}%")
    else:
        fail(f"gate of {RECONNECT_GATE:.0f} s is below the derived {budget:.1f} s")

    # 8. Everything uav_4 still has to fly stays in range of the slot, including
    #    the strip it inherits. The relay has to hold the link for the rest of
    #    the mission, not just at the moment it arrives.
    outage_end = t_assign + mover_flight / CRUISE_SPEED + SETTLE_ALLOWANCE + STABILITY_WINDOW
    note("integrated:outage_s", outage_end - T_KILL, "s")
    handover = t3.pos(t_assign)
    inherited = [handover, (handover[0], BOX_Y1, z4)]
    t4_full = Track(START["uav_4"]).hold_until(T_TAKEOFF)
    for wp in lane_path("uav_4", reverse_first=False):
        t4_full.go(wp, SURVEY_SPEED if t4_full.t >= T_SURVEY else CRUISE_SPEED)
        if t4_full.t < T_SURVEY:
            t4_full.hold_until(T_SURVEY)
    t4_full.go((inherited[0][0], inherited[0][1], z4), CRUISE_SPEED)
    t4_full.go((inherited[1][0], inherited[1][1], z4), SURVEY_SPEED)
    note("integrated:mission_complete_s", t4_full.t, "s")

    before = len(failures)
    worst_hop = 0.0
    worst_anchor = 1e9
    for t in frange(T_KILL, t4_full.t + SAMPLE_DT, SAMPLE_DT):
        p = t4_full.pos(t)
        worst_hop = max(worst_hop, math.dist(p, slot))
        worst_anchor = min(worst_anchor, math.dist(p, START["uav_1"]))
    note("integrated:worst_relay_hop", worst_hop)
    note("integrated:uav_4-uav_1_min", worst_anchor)
    if worst_hop <= USED_LINK_MAX:
        ok(f"uav_4 stays within {worst_hop:.1f} m of the slot for the rest of the mission, "
           f"inherited strip included")
    else:
        fail(f"uav_4 reaches {worst_hop:.1f} m from the slot while finishing the survey, so "
             f"the restored route breaks again")
    if worst_anchor >= UNUSED_LINK_MIN:
        ok(f"uav_4 never gets closer than {worst_anchor:.1f} m to uav_1, so the recovery is "
           f"the relay's doing and not a lucky direct link")
    else:
        fail(f"uav_4 closes to {worst_anchor:.1f} m of uav_1 while surveying, which would "
             f"restore the route without any relay and prove nothing")

    out(f"\n    mission finishes at t = {t4_full.t:.1f} s of {MISSION_DURATION:.0f} s")
    if t4_full.t <= MISSION_DURATION * 0.9:
        ok(f"the frozen duration leaves {MISSION_DURATION - t4_full.t:.0f} s of slack")
    else:
        fail(f"the survey finishes at {t4_full.t:.1f} s against a {MISSION_DURATION:.0f} s run, "
             f"which is not enough room for a slow SITL")

    # 9. Buffering. Round 4 finding 3: the queue held 10 seconds of observations
    #    against an outage measured in tens of seconds, so the frozen design
    #    guaranteed the no-loss claim was false.
    gen_rate = 5.0
    worst_outage = RECONNECT_GATE
    need = math.ceil(gen_rate * worst_outage)
    note("integrated:buffer_needed", need, "packets")
    out(f"\n    outage {outage_end - T_KILL:.1f} s derived, {RECONNECT_GATE:.0f} s permitted "
        f"by the gate")
    out(f"    a surveyor generating {gen_rate:.0f} observations per second must queue "
        f"{need} of them")


def check_link_loss(out, ok) -> None:
    """link_loss.yaml: the failure the challenge names that a kill cannot show.

    The organisers say it twice, in the challenge statement and again in the
    FAQ: the swarm reconfigures as UAVs fail OR LOSE CONNECTIVITY. Those are
    different faults. A killed vehicle is gone. A vehicle that has lost its
    radio is still in the air, still occupies space, and comes back.

    Same common geometry as relay_kill on purpose, so the two runs differ in
    exactly one thing and the comparison carries the argument.
    """
    out("\n" + "=" * 62)
    out("link_loss: uav_2 goes quiet rather than dying, then comes back")
    out("=" * 62)

    attach, mover, stays = "uav_1", "uav_3", "uav_4"
    slot0 = minimax_slot(START[attach], [START[stays]])

    # uav_2 is FLYING here. That is the whole difference from relay_kill.
    live = [START[v] for v in ("uav_1", "uav_2", "uav_4")]
    d_raw = note("link_loss:slot_to_live_uav_2", math.dist(slot0, START["uav_2"]))
    out(f"    balance-point slot ({slot0[0]:.1f}, {slot0[1]:.1f}, {slot0[2]:.1f})")
    out(f"    distance to uav_2, which is alive: {d_raw:.1f} m")

    slot = clear_slot(slot0, live)
    if slot is None:
        fail("no slot altitude clears the live vehicles, so the component would "
             "report RELAY_INFEASIBLE and this scenario proves nothing")
        return
    if slot == slot0:
        ok(f"the slot already clears every flying vehicle by {d_raw:.1f} m, "
           f"above the {SLOT_CLEARANCE:.0f} m required, so no raise is needed")
    else:
        ok(f"the slot raised to {slot[2]:.1f} m to clear a flying vehicle")

    # Routing has to hold with uav_2 in the graph but unreachable.
    hop_up = note("link_loss:uav_1-slot", math.dist(START[attach], slot))
    hop_dn = note("link_loss:slot-uav_4", math.dist(slot, START[stays]))
    for label, d in ((f"{attach} to slot", hop_up), (f"slot to {stays}", hop_dn)):
        if d <= USED_LINK_MAX:
            ok(f"{label:<20} {d:8.1f} m")
        else:
            fail(f"{label} is {d:.1f} m, beyond the {USED_LINK_MAX:.0f} m limit")

    flight = note("link_loss:mover_flight", math.dist(START[mover], slot))
    budget = note("link_loss:budget_s", reconnect_budget(flight), "s")
    out(f"    outbound flight {flight:.1f} m, reconnect budget {budget:.1f} s, "
        f"gate asserts {RECONNECT_GATE:.0f} s")
    if RECONNECT_GATE <= budget:
        fail(f"gate of {RECONNECT_GATE:.0f} s is below the derived {budget:.1f} s")

    # The return. When uav_2's radio comes back the component has a route that
    # does not use the mover, so the mover is released and flies home. The
    # alternate path must already be up, or releasing it causes a second outage.
    alt = path_to({k: v for k, v in START.items()}, stays, "gcs")
    if alt and mover not in alt:
        ok(f"once uav_2 is back, {stays} reaches gcs as {' -> '.join(alt)}, "
           f"without {mover}, so releasing it costs no second outage")
    else:
        fail(f"after the radio returns, {stays} still needs {mover}: {alt}. "
             f"Releasing the relay would drop the link again.")

    # The way home must not fly through anybody.
    worst = 1e9
    for i in range(1001):
        f = i / 1000.0
        p = tuple(slot[j] + f * (START[mover][j] - slot[j]) for j in range(3))
        for v in ("uav_1", "uav_2", "uav_4"):
            worst = min(worst, math.dist(p, START[v]))
    note("link_loss:return_clearance", worst)
    if worst >= MIN_SEPARATION:
        ok(f"the flight out and home never comes within {worst:.1f} m of another "
           f"vehicle, against a {MIN_SEPARATION:.0f} m floor")
    else:
        fail(f"the mover passes {worst:.1f} m from another vehicle on its way to "
             f"the slot, under the {MIN_SEPARATION:.0f} m floor")


def check_rejected(out, ok, tracks) -> None:
    """Keep the two designs round 4 rejected, and prove they were wrong.

    Both numbers get quoted in architecture.md to explain why the current design
    is shaped as it is, and a number quoted from memory rots. Worse, a rejected
    design nobody rechecks can quietly become correct again after some other
    parameter moves, and then the paragraph explaining the choice is arguing
    against nothing. So the rejections are assertions, not history.
    """
    out("\n" + "=" * 62)
    out("what round 4 rejected, and why it stays rejected")
    out("=" * 62)

    # The old integrated survey box, 50 m by 210 m at (405, -105). The claim was
    # that every point sat at least 250 m from uav_1. The closest point is the
    # middle of the near edge, not a corner, and nobody checked the middle.
    old = (405.0, 0.0, 50.0)
    d_old = note("rejected:old_box_near_edge", math.dist(old, START["uav_1"]))
    out(f"    old box near-edge midpoint to uav_1: {d_old:.1f} m")
    if d_old < UNUSED_LINK_MIN:
        ok(f"the old box really did put a forbidden link at {d_old:.1f} m, "
           f"{band(d_old)} band, so the relay kill was seed-dependent")
    else:
        fail(f"the old box measures {d_old:.1f} m, which does not reproduce the "
             f"round 4 finding. Either the finding or this check is wrong.")

    # The old slot rule: midpoint between the attachment node and wherever the
    # surviving member happened to be. Applied to a member that keeps flying a
    # survey box, it leaves part of that box out of range.
    t3, t4 = tracks["uav_3_survey_only"], tracks["uav_4_survey_only"]
    t_assign = T_KILL + NEIGHBOUR_TIMEOUT + LSA_PERIOD + ELECTION_WINDOW
    stay = t4.pos(t_assign)
    mid = tuple((START["uav_1"][i] + stay[i]) / 2 for i in range(3))
    z4 = SURVEY_ALT["uav_4"]
    corners = [(x, y, z4) for x in (BOX_X0, BOX_X1) for y in (BOX_Y0, BOX_Y1)]
    worst = note("rejected:midpoint_slot_hop",
                 max(math.dist(mid, c) for c in corners))
    out(f"    midpoint slot ({mid[0]:.1f}, {mid[1]:.1f}, {mid[2]:.1f}), "
        f"worst hop {worst:.1f} m")
    if worst > USED_LINK_MAX:
        ok(f"the midpoint rule really does leave the box {worst:.1f} m away, "
           f"past the {USED_LINK_MAX:.0f} m limit, so balancing the hops is not "
           f"decoration")
    else:
        fail(f"the midpoint rule now reaches every corner within {worst:.1f} m, "
             f"so architecture.md is arguing against a problem that no longer "
             f"exists. Simplify the rule or fix the paragraph.")

    # The balance rule with no clearance constraint. It answers a routing
    # question and says nothing about airspace, and in this geometry the answer
    # is a point 6.8 m from a vehicle. Nothing catches it today only because
    # uav_2 is dead in every scenario that computes a slot, which is a property
    # of the scenario list rather than of the rule.
    corners_box = [(x, y, z4) for x in (BOX_X0, BOX_X1) for y in (BOX_Y0, BOX_Y1)]
    bare = minimax_slot(START["uav_1"], corners_box)
    d_bare = note("rejected:uncleared_slot_to_uav_2",
                  math.dist(bare, START["uav_2"]))
    out(f"    balance point alone puts the relay {d_bare:.1f} m from uav_2")
    if d_bare < MIN_SEPARATION:
        ok(f"unconstrained, the rule really does violate the {MIN_SEPARATION:.0f} m "
           f"floor, so the clearance step is load bearing")
    else:
        fail(f"the balance point is now {d_bare:.1f} m from uav_2, so the "
             f"clearance step has nothing to prevent here. Find a case where it "
             f"binds or drop it.")
    raised = clear_slot(bare, [START["uav_2"]])
    if raised is None:
        fail("the clearance step cannot resolve its own worked example")
    else:
        h1 = math.dist(START["uav_1"], raised)
        h2 = max(math.dist(raised, c) for c in corners_box)
        note("rejected:cleared_slot_hop_anchor", h1)
        note("rejected:cleared_slot_hop_box", h2)
        out(f"    raised to {raised[2]:.1f} m: clearance "
            f"{math.dist(raised, START['uav_2']):.1f} m, hops {h1:.1f} and {h2:.1f}")
        if max(h1, h2) <= USED_LINK_MAX:
            ok("raising to clear the vehicle keeps both hops inside the limit")
        else:
            fail(f"clearing the vehicle pushes a hop to {max(h1, h2):.1f} m, past "
                 f"the {USED_LINK_MAX:.0f} m limit")

    # The alternative nobody should take: clear the vehicle by sliding further
    # along the segment instead of climbing. It works and it spends the link
    # budget doing it, which is the argument for raising.
    anchor, cen = START["uav_1"], tuple(
        sum(c[i] for c in corners_box) / len(corners_box) for i in range(3))
    slid = None
    for i in range(1, 2001):
        t = i / 2000.0
        s = tuple(anchor[j] + t * (cen[j] - anchor[j]) for j in range(3))
        if math.dist(s, START["uav_2"]) >= SLOT_CLEARANCE and t > 0.5:
            slid = s
            break
    if slid is None:
        fail("sliding along the segment never clears the vehicle, so the "
             "comparison in architecture.md cannot be checked")
    else:
        h_slid = note("rejected:slid_slot_hop_anchor", math.dist(anchor, slid))
        out(f"    sliding instead of climbing needs a {h_slid:.1f} m anchor hop")
        if h_slid > math.dist(START["uav_1"], raised):
            ok(f"sliding costs {h_slid - math.dist(START['uav_1'], raised):.1f} m "
               f"more hop than climbing, which is why the rule climbs")
        else:
            fail("sliding is now cheaper than climbing, so the rule should slide")
    _ = t3


def check_encounter(out, ok) -> None:
    """encounter.yaml: the two paths must actually conflict."""
    out("\n" + "=" * 62)
    out("encounter: the yield rule has to be forced, not hoped for")
    out("=" * 62)
    a0, a1 = (250.0, -120.0, 45.0), (250.0, 120.0, 45.0)
    b0, b1 = (130.0, 0.0, 45.0), (370.0, 0.0, 45.0)
    t_start = 20.0
    la, lb = math.dist(a0, a1), math.dist(b0, b1)
    note("encounter:path", la)
    if abs(la - lb) < 1e-6:
        ok(f"both paths are {la:.0f} m, so neither vehicle arrives first by construction")
    else:
        fail(f"paths differ, {la:.1f} m against {lb:.1f} m, so the conflict is not symmetric")

    ta = Track(a0).hold_until(t_start).go(a1, CRUISE_SPEED)
    tb = Track(b0).hold_until(t_start).go(b1, CRUISE_SPEED)
    worst = min(math.dist(ta.pos(t), tb.pos(t))
                for t in frange(t_start, ta.t + SAMPLE_DT, SAMPLE_DT))
    t_cross = t_start + la / 2 / CRUISE_SPEED
    note("encounter:crossing_s", t_cross, "s")
    if worst < MIN_SEPARATION:
        ok(f"with nobody yielding they pass within {worst:.1f} m at t = {t_cross:.0f} s, "
           f"under the {MIN_SEPARATION:.0f} m floor, so the control run must record a violation")
    else:
        fail(f"unyielding separation bottoms out at {worst:.1f} m, above the floor. The "
             f"negative control would pass and prove nothing.")


def check_survey_baseline(out, ok) -> None:
    """survey_baseline.yaml: four strips, four altitudes, comms off."""
    out("\n" + "=" * 62)
    out("survey_baseline: partition and separation")
    out("=" * 62)
    x0, y0, side, strips = 375.0, -100.0, 200.0, 4
    width = side / strips
    note("baseline:strip_width", width)
    alts = sorted(START[v][2] for v in ("uav_1", "uav_2", "uav_3", "uav_4"))
    gaps = [b - a for a, b in zip(alts, alts[1:])]
    if min(gaps) >= MIN_SEPARATION:
        ok(f"four strips of {width:.0f} m, one vehicle each, altitudes {alts} "
           f"separated by {min(gaps):.0f} m")
    else:
        fail(f"altitude layers are {min(gaps):.0f} m apart, under the "
             f"{MIN_SEPARATION:.0f} m floor, so two surveyors could meet")
    if abs(width * strips - side) > 1e-9:
        fail(f"{strips} strips of {width} m do not tile a {side} m box")
    _ = (x0, y0)


def frange(a: float, b: float, step: float):
    n = int(math.floor((b - a) / step + 1e-9)) + 1
    return (a + i * step for i in range(max(n, 0)))


if __name__ == "__main__":
    n = run()
    if n:
        print(f"\nFAILED: {n} constraint(s) violated")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nall topology claims verified, stationary and in motion")
    sys.exit(0)
