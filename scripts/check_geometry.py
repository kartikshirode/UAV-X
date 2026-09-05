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
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

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

# Round 5 finding 1: with hop count alone the recovered path and the relay path
# tie, a tie never wins route hysteresis, and the relay could therefore never be
# released. The first fix was a scalar surcharge of 0.5 per temporary relay on
# the path, and round 6 finding 9 is right that it only behaves for one relay.
# Two relays on a three hop path cost 3 + 2(0.5) = 4, which ties a four hop path
# carrying none; three relays lose to it outright. Stage 1 never builds a relay
# chain, so nothing here would have caught it, and the rule as frozen would have
# been wrong the moment a second disturbance did.
#
# The route key is a pair instead, compared left to right, which is what
# `route_key` returns. Fewer hops always wins. Relay count only ever decides a
# tie. No weight to pick, and nothing to be wrong at a larger relay count.
MIN_SEPARATION = 10.0

# A relay slot has to clear every vehicle that is still flying, by more than the
# separation floor. The extra 5 m is for staleness: a node that has lost its
# radio sends no HELLO, so the swarm is steering around where it last saw it.
SLOT_CLEARANCE = 15.0
SLOT_RAISE_STEP = 5.0
SLOT_CEILING = 95.0

# Round 5 finding 4: raising the slot only when it happens to collide is a rule
# no accepted scenario ever exercised, and its 5 m staleness allowance was never
# derived. A silent vehicle sends no HELLO, so its horizontal position is a
# guess that gets worse at 10 m/s; 5 m covers half a second of that.
#
# So separation is taken vertically instead, where it does not depend on knowing
# where anyone drifted. Every relay slot sits in a band reserved for relays,
# SLOT_CLEARANCE above the highest mission altitude, and no mission corridor may
# enter it. That holds however lost the silent vehicle is, as long as it holds
# altitude, which is the last thing a comms failure affects: PX4 keeps flying
# whatever the radio does.
RELAY_BAND = 75.0

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


def all_paths(pos: dict, src: str, dst: str) -> list:
    """Every loop-free path, so a tie can be seen rather than hidden by BFS.

    `path_to` returns one shortest path. When two are equally short it silently
    picks by neighbour order, which is exactly how the handback tie stayed
    invisible.
    """
    adj = adjacency(pos)
    out, stack = [], [[src]]
    while stack:
        p = stack.pop()
        if p[-1] == dst:
            out.append(p)
            continue
        if len(p) > len(pos):
            continue
        for n in sorted(adj[p[-1]]):
            if n not in p:
                stack.append(p + [n])
    return sorted(out, key=lambda p: (len(p), p))


def route_key(path: list, relays: set) -> tuple:
    """(hops, temporary relays on the path). Lower wins, compared left to right.

    Hop count alone cannot express "this route is as short as the other one but
    it costs me a surveyor", and that is the whole handback problem. A pair says
    it without having to price a relay against a hop: Dijkstra compares the
    tuple, so a shorter path can never lose however many relays sit on the
    alternative.
    """
    return (len(path) - 1, len(set(path) & relays))


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


def banded_slot(anchor: tuple, work: list) -> tuple:
    """The balance point, in the reserved relay band.

    Same minimax rule as before, solved on the horizontal projection with the
    altitude fixed. Costs a little link budget and buys separation that does not
    depend on knowing where a silent vehicle went.
    """
    c = tuple(sum(p[i] for p in work) / len(work) for i in range(3))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        m = (lo + hi) / 2
        s = (anchor[0] + m * (c[0] - anchor[0]),
             anchor[1] + m * (c[1] - anchor[1]), RELAY_BAND)
        if math.dist(anchor, s) < max(math.dist(s, p) for p in work):
            lo = m
        else:
            hi = m
    m = (lo + hi) / 2
    return (anchor[0] + m * (c[0] - anchor[0]),
            anchor[1] + m * (c[1] - anchor[1]), RELAY_BAND)


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
    out("\nthe reserved relay band")
    mission_alts = sorted({START[v][2] for v in ("uav_1", "uav_2", "uav_3", "uav_4")}
                          | set(SURVEY_ALT.values()))
    gap = note("relay_band_gap", RELAY_BAND - max(mission_alts))
    out(f"    mission altitudes {mission_alts}, band at {RELAY_BAND:.0f} m")
    if gap >= SLOT_CLEARANCE:
        ok(f"the band clears the highest mission corridor by {gap:.0f} m, at or "
           f"above the {SLOT_CLEARANCE:.0f} m a relay slot must hold, so a "
           f"silent vehicle's horizontal drift cannot close it")
    else:
        fail(f"the relay band is {gap:.0f} m above the highest mission "
             f"altitude, under the {SLOT_CLEARANCE:.0f} m clearance rule")

    out("\nrelay_kill: election and slot, recomputed")
    attach = "uav_1"
    cands = sorted(("uav_3", "uav_4"),
                   key=lambda n: (math.dist(START[n], START[attach]), n))
    mover, stays = cands[0], cands[1]
    out("    distance to " + attach + ": " + ", ".join(
        f"{n} {math.dist(START[n], START[attach]):.1f} m" for n in ("uav_3", "uav_4")))
    out(f"    mover = {mover}, remaining = {stays}")

    slot = banded_slot(START[attach], [START[stays]])
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
    check_route_key(out, ok)
    check_link_loss(out, ok)
    check_rejected(out, ok, build_integrated())
    check_encounter(out, ok)
    check_survey_baseline(out, ok)
    check_relay_scenarios(out, ok)
    check_fault_scenarios(out, ok)
    check_encounter_files(out, ok)
    check_integrated_file(out, ok)

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
    slot = banded_slot(START["uav_1"], corners)
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


def check_route_key(out, ok) -> None:
    """The route rule has to hold for topologies Stage 1 never builds.

    Round 6 finding 9. The scalar surcharge was checked against the one
    situation the frozen scenarios produce, a single temporary relay, and it is
    correct there. It is wrong at two. The rule ends up in the proposal and in
    the implementation, and a later disturbance that elects a second relay while
    the first is still held would silently start routing the long way round.

    So the property gets checked directly, over relay counts Stage 1 will not
    reach, rather than inferred from a constant being under 1.
    """
    out("\n" + "=" * 62)
    out("route key: fewer hops always wins, relays only break ties")
    out("=" * 62)

    # The case the old rule got wrong. Three hops through two temporary relays
    # against four hops through none.
    short = ["uav_4", "uav_5", "uav_6", "gcs"]
    long_ = ["uav_4", "uav_2", "uav_1", "uav_0", "gcs"]
    two = {"uav_5", "uav_6"}
    out(f"    {' -> '.join(short):<38} key {route_key(short, two)}")
    out(f"    {' -> '.join(long_):<38} key {route_key(long_, two)}")
    out(f"    the same two under the old scalar rule: "
        f"{(len(short) - 1) + 0.5 * len(set(short) & two):.1f} and "
        f"{(len(long_) - 1) + 0.5 * len(set(long_) & two):.1f}")
    if route_key(short, two) < route_key(long_, two):
        ok("the three hop path through two relays still beats the four hop path "
           "through none. A scalar surcharge of 0.5 ties them here, and loses "
           "outright at three relays")
    else:
        fail("a genuinely shorter path lost to a longer one, which is the whole "
             "thing the pair is for")

    # And exhaustively, not just on the one case that motivated it. Every hop
    # count against every hop count, at every relay count a path could carry.
    worst = None
    for hops_a in range(1, 8):
        for hops_b in range(1, 8):
            for relays_a in range(0, hops_a + 1):
                for relays_b in range(0, hops_b + 1):
                    a = (hops_a, relays_a)
                    b = (hops_b, relays_b)
                    if hops_a < hops_b and not a < b:
                        worst = (a, b)
    if worst is None:
        ok("across every hop count to 7 and every relay count a path of that "
           "length could carry, no shorter path ever loses to a longer one")
    else:
        fail(f"{worst[0]} did not beat {worst[1]} despite being shorter")

    # The tie-break half. Equal hops, and the one holding fewer relays wins.
    if (3, 0) < (3, 1) < (3, 2):
        ok("at equal hops the route holding fewer temporary relays wins, which "
           "is what makes the handback release reachable")
    else:
        fail("equal hop routes do not order by relay count")


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
    slot0 = banded_slot(START[attach], [START[stays]])

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

    # The return. Round 5 finding 1, and the check that was supposed to cover it
    # asked about the ORIGINAL topology, with the mover still at its start
    # position. That question has an easy yes and no bearing on the handback at
    # all. The state that matters is the one after recovery: uav_2 healthy again
    # and the mover parked at the slot.
    post = dict(START)
    post[mover] = slot

    print()
    routes = all_paths(post, stays, "gcs")
    for p in routes:
        print(f"    {' -> '.join(p):<34} {len(p) - 1} hops, "
              f"key {route_key(p, {mover})}")

    fewest = min(len(p) - 1 for p in routes)
    shortest = [p for p in routes if len(p) - 1 == fewest]
    if len(shortest) >= 2:
        ok(f"on hop count alone {len(shortest)} routes tie at {fewest} hops, "
           f"which is round 5 finding 1: a tie never wins hysteresis, so the "
           f"installed route through {mover} would never be replaced and the "
           f"release could never fire")
    else:
        fail(f"only one route is shortest now, so the surcharge below is "
             f"arguing against a problem that has moved. Recheck the rule "
             f"before trusting it.")

    ranked = sorted(routes, key=lambda p: route_key(p, {mover}))
    best = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    strict = runner is not None and route_key(best, {mover}) < route_key(runner, {mover})
    if mover not in best and strict:
        ok(f"on the pair the winner is {' -> '.join(best)} at "
           f"{route_key(best, {mover})}, strictly below {route_key(runner, {mover})}, "
           f"so the recovered path installs itself and the release predicate "
           f"becomes reachable")
    else:
        fail(f"the cheapest route is {' -> '.join(best)} at "
             f"{route_key(best, {mover})}. The handback still cannot happen "
             f"without breaking the link first.")

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
    (a0, a1), (b0, b1) = ENCOUNTER_A, ENCOUNTER_B
    t_start = ENCOUNTER_START_S
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


# --- the two files chunk 3.4 and 3.5 fly -----------------------------------
# The scenario name against the starting role in the common geometry table of
# architecture.md section 6, in the spelling uavx_comms.router_node accepts.
FROZEN_ROLES = {"uav_1": "gcs_anchor", "uav_2": "relay",
                "uav_3": "survey", "uav_4": "survey"}
RELAY_DURATION = 240.0
APP_PACKET_RATE_HZ = 5.0
APP_PACKET_FLOOR = 1080          # the gate's threshold: 240 x 5, less 10%

# Everything the pair must agree on. The two scenarios are a measurement and
# its control, so the only keys allowed to differ are the file name and the
# one flag being controlled for.
PAIRED_KEYS = ("seed", "duration_s", "vehicles", "injected_events",
               "headless", "hover_altitudes_m")
PAIRED_COMMS_KEYS = ("enabled", "elections_enabled", "roles", "stations")


def _scenario(name: str):
    """One scenario file, or None with a failure already recorded."""
    try:
        import yaml
    except ImportError:
        fail("pyyaml is not installed, so the relay scenarios cannot be read "
             "and the stations they fly are compared with nothing")
        return None
    path = REPO / "scenarios" / f"{name}.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        fail(f"no scenario at {path}. The relay claim is a claim about where "
             f"four vehicles stand, and there is no file saying where")
        return None
    except yaml.YAMLError as exc:
        fail(f"{path} is not valid YAML: {exc}")
        return None
    if not isinstance(doc, dict):
        fail(f"{path} does not parse to a mapping")
        return None
    return doc


def check_relay_scenarios(out, ok) -> None:
    """relay_required and direct_only: the stations, and the one difference.

    Round 4's lesson one level down again. Until this chunk the geometry
    lived in START above and in the scenario files, and nothing compared the
    two. A station typed 10 m out would fly, deliver packets and write a
    record, and the only thing wrong with the run would be that it was not
    the run architecture.md describes.
    """
    out("")
    out("=" * 62)
    out("relay_required and direct_only: stations, roles and the one flag")
    out("=" * 62)

    relay = _scenario("relay_required")
    direct = _scenario("direct_only")
    if relay is None or direct is None:
        return

    for name, doc in (("relay_required", relay), ("direct_only", direct)):
        comms = doc.get("comms")
        if not isinstance(comms, dict):
            fail(f"{name} carries no comms block, so it flies no radio")
            continue
        stations = comms.get("stations")
        if not isinstance(stations, dict):
            fail(f"{name} names no stations")
            continue
        for vehicle in sorted(v for v in START if v != "gcs"):
            got = stations.get(vehicle)
            want = START[vehicle]
            if (not isinstance(got, list) or len(got) != 3
                    or any(not isinstance(v, (int, float))
                           or isinstance(v, bool) for v in got)):
                fail(f"{name} station for {vehicle} is {got!r}, not three numbers")
                continue
            drift = math.dist([float(v) for v in got], want)
            if drift > 1e-6:
                fail(f"{name} stands {vehicle} at {tuple(got)}, "
                     f"{drift:.3f} m from the frozen {want}")
        if len(stations) == 4 and not failures:
            ok(f"{name} stands all four vehicles on the frozen table")

        alts = doc.get("hover_altitudes_m")
        if not isinstance(alts, dict):
            fail(f"{name} names no hover altitudes")
        else:
            for vehicle in sorted(v for v in START if v != "gcs"):
                if abs(float(alts.get(vehicle, -1)) - START[vehicle][2]) > 1e-6:
                    fail(f"{name} climbs {vehicle} to {alts.get(vehicle)!r} and "
                         f"the frozen layer is {START[vehicle][2]}")

        roles = comms.get("roles")
        if roles != FROZEN_ROLES:
            fail(f"{name} starts the swarm as {roles!r}, and the common "
                 f"geometry table says {FROZEN_ROLES!r}")

        if float(doc.get("duration_s", 0)) != RELAY_DURATION:
            fail(f"{name} runs {doc.get('duration_s')!r} s and the gate's "
                 f"packet threshold is written for {RELAY_DURATION:.0f} s")

    # ------------------------------------------------- the one difference
    for key in PAIRED_KEYS:
        if relay.get(key) != direct.get(key):
            fail(f"the pair disagree on {key}: {relay.get(key)!r} against "
                 f"{direct.get(key)!r}. A control that differs in two things "
                 f"measures neither of them")
    rc, dc = relay.get("comms") or {}, direct.get("comms") or {}
    for key in PAIRED_COMMS_KEYS:
        if rc.get(key) != dc.get(key):
            fail(f"the pair disagree on comms.{key}")
    if rc.get("forwarding") is not True or dc.get("forwarding") is not False:
        fail(f"forwarding is {rc.get('forwarding')!r} in relay_required and "
             f"{dc.get('forwarding')!r} in direct_only; the control is the "
             f"same scenario with forwarding off and nothing else")
    elif not failures:
        ok("the pair differ in comms.forwarding and in nothing else")

    # ------------------------------------------- what the gate can assert
    expected = RELAY_DURATION * APP_PACKET_RATE_HZ
    note("relay:app_packets_per_node", expected, "packets")
    if expected < APP_PACKET_FLOOR:
        fail(f"{RELAY_DURATION:.0f} s at {APP_PACKET_RATE_HZ:.0f} Hz is "
             f"{expected:.0f} packets and the gate asks for {APP_PACKET_FLOOR}")
    else:
        ok(f"{expected:.0f} application packets per node, gate floor "
           f"{APP_PACKET_FLOOR}, margin "
           f"{(expected - APP_PACKET_FLOOR) / expected * 100:.0f}%")

    # The control's whole claim: uav_4 cannot reach the ground station on its
    # own. Recomputed here rather than quoted, from the same START the matrix
    # above enumerates.
    far = math.dist(START["uav_4"], START["gcs"])
    if far <= R_MAX:
        fail(f"uav_4 is {far:.1f} m from gcs, inside r_max of {R_MAX:.0f} m, "
             f"so direct_only would deliver without any relay and the control "
             f"would prove nothing")
    else:
        ok(f"uav_4 to gcs is {far:.1f} m, beyond r_max of {R_MAX:.0f} m, so a "
           f"delivery of 0 in direct_only is the only honest outcome")


# --- the six files week 4 flies --------------------------------------------
#
# Chunk 3.2's lesson, six more times. Every number below already appears above
# as a constant the trajectory walk uses, and until this section existed it
# also appeared in a YAML file that nothing compared it with. A kill typed at
# 130 s instead of 120 would fly, fail over, recover and write a record, and
# the only thing wrong with the run would be that it was not the run
# architecture.md describes.
KILL_AT_S = 120.0
KILL_DURATION = 300.0

BLACKOUT_AT_S = 120.0
BLACKOUT_RESTORE_S = 240.0
LINK_LOSS_DURATION = 360.0

DRAIN_AT_S = 60.0
DRAIN_RESTORE_S = 105.0
DRAIN_DURATION = 180.0
DRAIN_ORIGINS = 2                # uav_3 and uav_4, the cut off component
DRAIN_BOUND_S = 2.25             # the gate's drain bound, derived below

ENCOUNTER_START_S = 20.0
ENCOUNTER_DURATION = 90.0
ENCOUNTER_ALT = 45.0
ENCOUNTER_A = ((250.0, -120.0, ENCOUNTER_ALT), (250.0, 120.0, ENCOUNTER_ALT))
ENCOUNTER_B = ((130.0, 0.0, ENCOUNTER_ALT), (370.0, 0.0, ENCOUNTER_ALT))

# relay_kill and link_loss are one fault told twice, once as a vehicle that is
# gone and once as one that is only quiet. They may differ in the event and in
# the room the run leaves after it, and in nothing else.
FAULT_PAIRED_KEYS = ("seed", "vehicles", "headless", "hover_altitudes_m",
                     "comms")

# The encounter pair may differ in one flag, inside `safety`.
ENCOUNTER_PAIRED_KEYS = ("seed", "duration_s", "vehicles", "injected_events",
                         "headless", "hover_altitudes_m", "tracks", "comms")

STATIC = ("uav_1", "uav_2")
SURVEYORS = ("uav_3", "uav_4")


def frozen_params():
    """The protocol constants, from the one file that defines them.

    Appended to the path, never inserted, and params.py imports nothing, so
    this works on a bare checkout with no overlay built. Restating 512 and 200
    here would give the queue two homes and the drain bound would then be
    derived from whichever one somebody edited last.
    """
    root = REPO / "uavx_ws" / "src" / "uavx_comms"
    if str(root) not in sys.path:
        sys.path.append(str(root))
    from uavx_comms import params
    return params


def _event(doc, index=0):
    events = doc.get("injected_events")
    if not isinstance(events, list) or len(events) <= index:
        return {}
    event = events[index]
    return event if isinstance(event, dict) else {}


def _check_common_geometry(name, doc, ok) -> None:
    """The stations, altitudes and roles of a common geometry scenario."""
    comms = doc.get("comms")
    if not isinstance(comms, dict):
        fail(f"{name} carries no comms block, so it flies no radio")
        return
    stations = comms.get("stations")
    if not isinstance(stations, dict):
        fail(f"{name} names no stations")
        return
    before = len(failures)
    for vehicle in sorted(v for v in START if v != "gcs"):
        got = stations.get(vehicle)
        want = START[vehicle]
        if (not isinstance(got, list) or len(got) != 3
                or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                       for v in got)):
            fail(f"{name} station for {vehicle} is {got!r}, not three numbers")
            continue
        drift = math.dist([float(v) for v in got], want)
        if drift > 1e-6:
            fail(f"{name} stands {vehicle} at {tuple(got)}, {drift:.3f} m "
                 f"from the frozen {want}")
    alts = doc.get("hover_altitudes_m")
    if not isinstance(alts, dict):
        fail(f"{name} names no hover altitudes")
    else:
        for vehicle in sorted(v for v in START if v != "gcs"):
            if abs(float(alts.get(vehicle, -1)) - START[vehicle][2]) > 1e-6:
                fail(f"{name} climbs {vehicle} to {alts.get(vehicle)!r} and "
                     f"the frozen layer is {START[vehicle][2]}")
    if comms.get("roles") != FROZEN_ROLES:
        fail(f"{name} starts the swarm as {comms.get('roles')!r}, and the "
             f"common geometry table says {FROZEN_ROLES!r}")
    if len(failures) == before:
        ok(f"{name} stands all four vehicles on the frozen table")


def check_fault_scenarios(out, ok) -> None:
    """relay_kill, link_loss and queue_drain: the fault and its timing."""
    out("")
    out("=" * 62)
    out("relay_kill, link_loss and queue_drain: the fault, and when")
    out("=" * 62)

    kill = _scenario("relay_kill")
    loss = _scenario("link_loss")
    drain = _scenario("queue_drain")
    if kill is None or loss is None or drain is None:
        return

    for name, doc in (("relay_kill", kill), ("link_loss", loss),
                      ("queue_drain", drain)):
        _check_common_geometry(name, doc, ok)

    # ------------------------------------------------------- the three events
    event = _event(kill)
    if event.get("type") != "kill" or event.get("target") != "uav_2":
        fail(f"relay_kill injects {event.get('type')!r} on "
             f"{event.get('target')!r}; the scenario is the relay dying")
    elif float(event.get("at_s", -1)) != KILL_AT_S:
        fail(f"relay_kill kills at {event.get('at_s')!r} s and the design "
             f"says {KILL_AT_S:.0f} s")
    elif float(kill.get("duration_s", 0)) != KILL_DURATION:
        fail(f"relay_kill runs {kill.get('duration_s')!r} s against the "
             f"frozen {KILL_DURATION:.0f} s")
    else:
        left = KILL_DURATION - KILL_AT_S
        slot = banded_slot(START["uav_1"], [START["uav_4"]])
        budget = reconnect_budget(math.dist(START["uav_3"], slot))
        ok(f"relay_kill kills the relay at {KILL_AT_S:.0f} s and leaves "
           f"{left:.0f} s, which is {left / budget:.1f} times the derived "
           f"{budget:.1f} s budget")

    event = _event(loss)
    if event.get("type") != "comms_blackout" or event.get("target") != "uav_2":
        fail(f"link_loss injects {event.get('type')!r} on "
             f"{event.get('target')!r}; the scenario is the relay going quiet "
             f"while it keeps flying")
    elif float(event.get("at_s", -1)) != BLACKOUT_AT_S:
        fail(f"link_loss gates the radio at {event.get('at_s')!r} s and the "
             f"design says {BLACKOUT_AT_S:.0f} s")
    elif float(event.get("restore_at_s", -1)) != BLACKOUT_RESTORE_S:
        fail(f"link_loss restores at {event.get('restore_at_s')!r} s and the "
             f"design says {BLACKOUT_RESTORE_S:.0f} s. A vehicle that never "
             f"comes back is relay_kill with extra steps")
    elif float(loss.get("duration_s", 0)) != LINK_LOSS_DURATION:
        fail(f"link_loss runs {loss.get('duration_s')!r} s against the frozen "
             f"{LINK_LOSS_DURATION:.0f} s")
    else:
        after = LINK_LOSS_DURATION - BLACKOUT_RESTORE_S
        ok(f"link_loss gates the radio for "
           f"{BLACKOUT_RESTORE_S - BLACKOUT_AT_S:.0f} s and leaves {after:.0f} s "
           f"for the handback, which is the only thing this scenario tests "
           f"that relay_kill does not")

    # ----------------------------------------------- one fault, told twice
    for key in FAULT_PAIRED_KEYS:
        if kill.get(key) != loss.get(key):
            fail(f"relay_kill and link_loss disagree on {key}. They differ in "
                 f"the event and in the room left after it, and a pair that "
                 f"differs in a third thing compares two runs rather than two "
                 f"faults")
    if not failures:
        ok("relay_kill and link_loss share their seed, vehicles, stations, "
           "roles and flags")

    # -------------------------------------------------- the 45 second hold
    event = _event(drain)
    outage = float(event.get("restore_at_s", 0)) - float(event.get("at_s", -1))
    note("queue_drain:outage_s", outage, "s")
    if event.get("type") != "comms_blackout" or event.get("target") != "uav_2":
        fail(f"queue_drain injects {event.get('type')!r} on "
             f"{event.get('target')!r}")
    elif float(event.get("at_s", -1)) != DRAIN_AT_S:
        fail(f"queue_drain gates the radio at {event.get('at_s')!r} s and the "
             f"gate asserts outage_start_s == {DRAIN_AT_S:.0f}")
    elif float(event.get("restore_at_s", -1)) != DRAIN_RESTORE_S:
        fail(f"queue_drain restores at {event.get('restore_at_s')!r} s and "
             f"the gate asserts outage_end_s == {DRAIN_RESTORE_S:.0f}")
    elif float(drain.get("duration_s", 0)) != DRAIN_DURATION:
        fail(f"queue_drain runs {drain.get('duration_s')!r} s against the "
             f"frozen {DRAIN_DURATION:.0f} s")
    else:
        ok(f"queue_drain holds the route down for {outage:.0f} s, which is "
           f"the depth the queue is sized for")

    if (drain.get("comms") or {}).get("elections_enabled") is not False:
        fail("queue_drain leaves elections on. An election puts a relay in "
             "the air inside the reconnect budget and the queue never reaches "
             "the depth this scenario exists to reach")
    elif (kill.get("comms") or {}).get("elections_enabled") is not True:
        fail("relay_kill has elections off, so nothing would replace the relay")
    else:
        ok("queue_drain is the only one of the three with elections off")

    # ------------------------------------------ the arithmetic off that hold
    params = frozen_params()
    generated = outage * params.APP_PACKET_RATE_HZ * DRAIN_ORIGINS
    note("queue_drain:generated", generated, "packets")
    drain_s = note("queue_drain:drain_s",
                   generated / params.FORWARD_RATE_PPS, "s")
    out(f"    {outage:.0f} s x {params.APP_PACKET_RATE_HZ:.0f} Hz x "
        f"{DRAIN_ORIGINS} origins = {generated:.0f} packets")
    out(f"    {generated:.0f} at {params.FORWARD_RATE_PPS:.0f} per second = "
        f"{drain_s:.2f} s")
    if generated > params.QUEUE_CAPACITY:
        fail(f"{generated:.0f} packets overflow the {params.QUEUE_CAPACITY} "
             f"the queue holds, so the scenario would evict and the gate "
             f"asserts nothing was evicted")
    elif abs(drain_s - DRAIN_BOUND_S) > 1e-9:
        fail(f"the drain works out at {drain_s:.3f} s and the gate asserts "
             f"{DRAIN_BOUND_S}. One of the two was derived and the other was "
             f"typed")
    else:
        headroom = params.QUEUE_CAPACITY - generated
        ok(f"{generated:.0f} packets buffered, {headroom:.0f} short of the "
           f"{params.QUEUE_CAPACITY} capacity, drained in {drain_s:.2f} s")


def check_encounter_files(out, ok) -> None:
    """encounter and encounter_noyield: the tracks, and the one flag."""
    out("")
    out("=" * 62)
    out("encounter and encounter_noyield: the tracks, and the one flag")
    out("=" * 62)

    run = _scenario("encounter")
    control = _scenario("encounter_noyield")
    if run is None or control is None:
        return

    want = {"uav_3": ENCOUNTER_A, "uav_4": ENCOUNTER_B}
    for name, doc in (("encounter", run), ("encounter_noyield", control)):
        tracks = doc.get("tracks")
        if not isinstance(tracks, dict):
            fail(f"{name} names no tracks, so nothing says where the two "
                 f"vehicles fly")
            continue
        before = len(failures)
        for vehicle, (start, end) in sorted(want.items()):
            line = tracks.get(vehicle)
            if not isinstance(line, dict):
                fail(f"{name} gives {vehicle} no track")
                continue
            for key, frozen in (("start_enu", start), ("end_enu", end)):
                got = line.get(key)
                if (not isinstance(got, list) or len(got) != 3
                        or any(not isinstance(v, (int, float))
                               or isinstance(v, bool) for v in got)):
                    fail(f"{name} {vehicle} {key} is {got!r}, not three "
                         f"numbers")
                    continue
                drift = math.dist([float(v) for v in got], frozen)
                if drift > 1e-6:
                    fail(f"{name} flies {vehicle} {key} to {tuple(got)}, "
                         f"{drift:.3f} m from the frozen {frozen}")
            if float(line.get("start_s", -1)) != ENCOUNTER_START_S:
                fail(f"{name} starts {vehicle} at {line.get('start_s')!r} s. "
                     f"Both start together or one of them arrives first and "
                     f"the conflict is not the one the design describes")
            if float(line.get("speed_mps", -1)) != CRUISE_SPEED:
                fail(f"{name} flies {vehicle} at {line.get('speed_mps')!r} m/s "
                     f"against the frozen cruise speed of {CRUISE_SPEED:.0f}")
        alts = doc.get("hover_altitudes_m") or {}
        for vehicle in sorted(want):
            if abs(float(alts.get(vehicle, -1)) - ENCOUNTER_ALT) > 1e-6:
                fail(f"{name} puts {vehicle} at {alts.get(vehicle)!r} m. Both "
                     f"vehicles are commanded to {ENCOUNTER_ALT:.0f} m on "
                     f"purpose, so the altitude layers cannot save them and "
                     f"the yield rule has to act")
        if float(doc.get("duration_s", 0)) != ENCOUNTER_DURATION:
            fail(f"{name} runs {doc.get('duration_s')!r} s against the frozen "
                 f"{ENCOUNTER_DURATION:.0f} s")
        if len(failures) == before:
            ok(f"{name} flies the two frozen paths at {CRUISE_SPEED:.0f} m/s "
               f"from t = {ENCOUNTER_START_S:.0f} s")

    for key in ENCOUNTER_PAIRED_KEYS:
        if run.get(key) != control.get(key):
            fail(f"the encounter pair disagree on {key}. A control that "
                 f"differs in two things measures neither of them")
    on = (run.get("safety") or {}).get("yield_enabled")
    off = (control.get("safety") or {}).get("yield_enabled")
    if on is not True or off is not False:
        fail(f"yield_enabled is {on!r} in encounter and {off!r} in "
             f"encounter_noyield; the control is the same scenario with the "
             f"rule off and nothing else")
    elif not failures:
        ok("the pair differ in safety.yield_enabled and in nothing else")


def check_integrated_file(out, ok) -> None:
    """mission_integrated: the box, the lanes and who flies them."""
    out("")
    out("=" * 62)
    out("mission_integrated: the box, the split and the kill")
    out("=" * 62)

    doc = _scenario("mission_integrated")
    if doc is None:
        return

    survey = doc.get("survey")
    if not isinstance(survey, dict):
        fail("mission_integrated carries no survey block, so the run that the "
             "proposal is built on surveys nothing")
        return

    before = len(failures)
    for key, want in (("origin_x", BOX_X0), ("origin_y", BOX_Y0),
                      ("width_m", BOX_X1 - BOX_X0),
                      ("height_m", BOX_Y1 - BOX_Y0),
                      ("cell_m", CELL), ("footprint_m", SENSOR_R),
                      ("cruise_speed_mps", CRUISE_SPEED),
                      ("survey_speed_mps", SURVEY_SPEED),
                      ("start_s", T_SURVEY)):
        got = survey.get(key)
        if got is None or abs(float(got) - want) > 1e-9:
            fail(f"mission_integrated survey.{key} is {got!r} and the design "
                 f"says {want}")
    if len(failures) == before:
        cells = int((BOX_X1 - BOX_X0) / CELL) * int((BOX_Y1 - BOX_Y0) / CELL)
        ok(f"the box is {BOX_X1 - BOX_X0:.0f} m by {BOX_Y1 - BOX_Y0:.0f} m at "
           f"({BOX_X0:.0f}, {BOX_Y0:.0f}), {cells} cells of {CELL:.0f} m")

    flyers = survey.get("vehicles")
    if list(flyers or []) != list(SURVEYORS):
        fail(f"mission_integrated has {flyers!r} flying the box and the design "
             f"says {list(SURVEYORS)}")
    elif survey.get("mirrored") is not True:
        fail("mission_integrated does not fly the two strips mirrored. "
             "Mirroring is what keeps uav_3 nearer the attachment node by at "
             "least 13.0 m for the whole survey instead of by the 0.8 m that "
             "separates the two station-keeping candidates in relay_kill")
    else:
        ok(f"{' and '.join(SURVEYORS)} fly the box mirrored, "
           f"{' and '.join(STATIC)} hold the chain up")

    comms = doc.get("comms")
    stations = (comms or {}).get("stations")
    if not isinstance(stations, dict):
        fail("mission_integrated names no stations")
    else:
        if sorted(stations) != sorted(STATIC):
            fail(f"mission_integrated gives stations to {sorted(stations)} and "
                 f"only {list(STATIC)} hold one. A surveyor with a station has "
                 f"two places to be")
        for vehicle in sorted(set(stations) & set(START)):
            got = stations.get(vehicle)
            if (isinstance(got, list) and len(got) == 3
                    and math.dist([float(v) for v in got],
                                  START[vehicle]) > 1e-6):
                fail(f"mission_integrated stands {vehicle} off the frozen "
                     f"table at {tuple(got)}")
    if (comms or {}).get("roles") != FROZEN_ROLES:
        fail(f"mission_integrated starts the swarm as "
             f"{(comms or {}).get('roles')!r} against {FROZEN_ROLES!r}")

    alts = doc.get("hover_altitudes_m") or {}
    for vehicle in sorted(SURVEYORS):
        if abs(float(alts.get(vehicle, -1)) - SURVEY_ALT[vehicle]) > 1e-6:
            fail(f"mission_integrated surveys {vehicle} at "
                 f"{alts.get(vehicle)!r} m and the design says "
                 f"{SURVEY_ALT[vehicle]}")

    event = _event(doc)
    if event.get("type") != "kill" or event.get("target") != "uav_2":
        fail(f"mission_integrated injects {event.get('type')!r} on "
             f"{event.get('target')!r}")
    elif float(event.get("at_s", -1)) != T_KILL:
        fail(f"mission_integrated kills at {event.get('at_s')!r} s and the "
             f"trajectory walk above puts the surveyors 58% through their "
             f"strips at {T_KILL:.0f} s")
    elif float(doc.get("duration_s", 0)) != MISSION_DURATION:
        fail(f"mission_integrated runs {doc.get('duration_s')!r} s against "
             f"the frozen {MISSION_DURATION:.0f} s")
    else:
        ok(f"the relay dies at {T_KILL:.0f} s of a {MISSION_DURATION:.0f} s "
           f"run, with work unfinished on both sides of the failure")


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
