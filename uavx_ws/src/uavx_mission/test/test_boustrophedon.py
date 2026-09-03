"""What the lawnmower has to be true of, checked against the ground it covers.

The temptation with a path planner is to assert the waypoints it returns. That
is a mirror: it agrees with whatever the planner does, it has to be rewritten
every time the planner changes, and it never once asks the question the
mission is graded on, which is whether the ground under the box was flown
over.

So the assertions here are about coverage, containment and cost:

* every cell centre the metric scores is inside the sensor footprint
  somewhere along the plan, and stays covered when the plan is reduced to
  pose samples rather than an unbroken line
* the plan stays inside its own strip and turns on the strip boundary
* neighbouring lanes overlap, and neighbouring strips clear the separation
  floor
* the plan is longer than the shortest path that could possibly sweep the
  area and not much longer

Three of them are backed by a mutation. A coverage assertion that has never
been seen to fail is the same shape as `collision_contacts==0` passing on a
machine with no contact monitor, which is round 3 finding 8. So a plan with a
lane removed, and a plan whose lanes stop short of the boundary, are both
built here and both required to leave cells uncovered.

The strongest check is the first one. architecture.md freezes
`mission_integrated`'s four lane positions and scripts/check_geometry.py flies
them, both of them written by hand for that one scenario. This planner is
general and knows nothing about that scenario, and it has to produce those
four numbers and that waypoint order exactly. Two independent routes to the
same answer is the one thing a mirror cannot be.
"""

import math

import pytest

# Frozen figures already have homes. mission_integrated's box, its four lane
# positions and its two survey altitudes live in check_geometry.py, which is
# what proves the topology; the separation floor and the two speeds live
# beside them. Week 1 audit finding 2 was a threshold with three hand-written
# copies and no comparison, so these are imported rather than typed again.
import check_geometry
from uavx_sim.scenario_runner import POSE_HZ

from uavx_mission.boustrophedon import (PlanError, lane_count, lane_positions,
                                        lane_spacing, minimum_sweep_length,
                                        path_length, plan_path, sample_path)
from uavx_mission.partition import partition
from uavx_mission.survey_area import BASELINE_DURATION_S, SurveyArea

VEHICLES = ("uav_1", "uav_2", "uav_3", "uav_4")

# A lawnmower cannot reach the sweep floor: the floor assumes no turn, no
# overlap between neighbouring swaths and no lane wider than it needs to be,
# and all three are things a real plan pays for. Twice the floor is the point
# at which the plan is flying a meaningful amount of ground for the second
# time, which is what this bound exists to catch.
MAX_LENGTH_OVER_SWEEP_FLOOR = 2.0


def baseline_area():
    return SurveyArea.baseline()


def integrated_area():
    """`mission_integrated`'s box, taken from the file that flies it."""
    return SurveyArea(check_geometry.BOX_X0, check_geometry.BOX_Y0,
                      check_geometry.BOX_X1, check_geometry.BOX_Y1,
                      check_geometry.CELL, check_geometry.SENSOR_R)


def cell_index(area, x, y):
    return (int(math.floor((x - area.x_min) / area.cell_m)),
            int(math.floor((y - area.y_min) / area.cell_m)))


def covered_indices(area, samples, radius):
    """The cells whose centre fell inside the footprint of any sample.

    This is `coverage_fraction`'s own definition applied to a stream of
    positions, which is what makes it the right question to ask of a plan.
    The metric itself belongs to uavx_eval and reads real poses out of a run
    record; this reads the plan, so the two never stand in for each other.

    Only the cells near a sample are tested, which
    `test_the_coverage_helper_agrees_with_brute_force` holds to an
    exhaustive answer on a small case.
    """
    nx = int(round(area.width / area.cell_m))
    ny = int(round(area.height / area.cell_m))
    r2 = radius * radius
    covered = set()
    for sx, sy, _sz in samples:
        i0 = max(0, int(math.floor((sx - radius - area.x_min) / area.cell_m)))
        i1 = min(nx - 1, int(math.ceil((sx + radius - area.x_min) / area.cell_m)))
        j0 = max(0, int(math.floor((sy - radius - area.y_min) / area.cell_m)))
        j1 = min(ny - 1, int(math.ceil((sy + radius - area.y_min) / area.cell_m)))
        for i in range(i0, i1 + 1):
            cx = area.x_min + area.cell_m * (i + 0.5)
            for j in range(j0, j1 + 1):
                cy = area.y_min + area.cell_m * (j + 0.5)
                if (cx - sx) ** 2 + (cy - sy) ** 2 <= r2:
                    covered.add((i, j))
    return covered


def distance_to_path(path, x, y):
    """The closest the plan ever comes to a point, as a continuous line.

    The sampled answer above can only ever be larger than this, so the two
    together say whether a gap is in the plan or in the sampling.
    """
    best = float("inf")
    for a, b in zip(path, path[1:]):
        ax, ay = a[0], a[1]
        bx, by = b[0], b[1]
        dx, dy = bx - ax, by - ay
        leg2 = dx * dx + dy * dy
        if leg2 == 0.0:
            best = min(best, math.hypot(x - ax, y - ay))
            continue
        f = ((x - ax) * dx + (y - ay) * dy) / leg2
        f = min(1.0, max(0.0, f))
        best = min(best, math.hypot(x - (ax + f * dx), y - (ay + f * dy)))
    return best


def baseline_plans(start_north=False):
    """One plan per vehicle over the frozen box, at that vehicle's layer."""
    area = baseline_area()
    plans = {}
    for strip in partition(area, VEHICLES):
        altitude = check_geometry.START[strip.vehicle_id][2]
        plans[strip.vehicle_id] = (
            strip, plan_path(strip, area.sensor_radius_m, altitude,
                             start_north=start_north))
    return area, plans


# ------------------------------------------------------- the frozen scenario
def test_the_planner_reproduces_the_frozen_mission_integrated_lanes():
    """The general planner against four numbers written by hand for one scenario.

    architecture.md freezes `mission_integrated`'s lanes at x = 468.125,
    474.375, 480.625 and 486.875, and check_geometry.py flies exactly those.
    Nothing in this planner knows that scenario exists. It is handed the box,
    the sensor radius and two vehicles, and it has to arrive at the same four
    lanes and the same waypoint order, including the mirrored start that
    makes the election result hold by a usable margin.
    """
    area = integrated_area()
    strips = partition(area, ("uav_3", "uav_4"))

    for strip in strips:
        frozen = check_geometry.LANE_X[strip.vehicle_id]
        planned = lane_positions(strip, area.sensor_radius_m)
        assert len(planned) == len(frozen)
        for got, want in zip(planned, frozen):
            assert got == pytest.approx(want, abs=1e-9), (
                f"{strip.vehicle_id}: planner puts a lane at {got}, "
                f"architecture.md freezes {want}")

    # And the whole path, in order, against the trajectory the checker flies.
    # uav_3 works its strip from the north end and uav_4 from the south, which
    # is the mirroring architecture.md requires of this scenario.
    for vehicle, start_north in (("uav_3", True), ("uav_4", False)):
        strip = next(s for s in strips if s.vehicle_id == vehicle)
        planned = plan_path(strip, area.sensor_radius_m,
                            check_geometry.SURVEY_ALT[vehicle],
                            start_north=start_north)
        frozen = check_geometry.lane_path(vehicle, reverse_first=start_north)
        assert len(planned) == len(frozen)
        for got, want in zip(planned, frozen):
            assert got == pytest.approx(want, abs=1e-9)


def test_the_integrated_box_is_the_one_architecture_freezes():
    """Guards the import above, so the cross-check cannot pass on a stale box."""
    area = integrated_area()
    assert (area.width, area.height) == (25.0, 120.0)
    assert area.cell_count == 120


# -------------------------------------------------------------- containment
def test_the_plan_never_leaves_its_own_strip():
    area, plans = baseline_plans()
    for vehicle, (strip, path) in plans.items():
        for x, y, _z in path:
            assert strip.contains(x, y), (
                f"{vehicle} plans a waypoint at ({x}, {y}) outside its strip")
            assert area.contains(x, y)


def test_every_turn_sits_on_the_strip_boundary():
    """Lanes run the full height of the box, so the ends are the boundary.

    A lane that stops short leaves the cells at the end of it uncovered, and
    the coverage figure drops by a few per cent without saying which part of
    the box was missed.
    """
    _area, plans = baseline_plans()
    for vehicle, (strip, path) in plans.items():
        for x, y, _z in path:
            assert y in (strip.y_min, strip.y_max), (
                f"{vehicle} turns at y = {y}, which is neither boundary "
                f"({strip.y_min}, {strip.y_max}); x = {x}")


def test_every_move_is_along_one_axis_and_holds_altitude():
    """A diagonal between lanes sweeps a band that is neither lane."""
    _area, plans = baseline_plans()
    for vehicle, (_strip, path) in plans.items():
        altitudes = {round(z, 9) for _x, _y, z in path}
        assert len(altitudes) == 1, f"{vehicle} changes altitude mid-survey"
        for a, b in zip(path, path[1:]):
            moved = [i for i in range(2) if a[i] != b[i]]
            assert len(moved) == 1, (
                f"{vehicle} moves diagonally from {a} to {b}")


def test_the_direction_of_travel_alternates_lane_by_lane():
    """As the ox turns. A comb flies every lane the same way and deadheads back."""
    _area, plans = baseline_plans()
    for vehicle, (_strip, path) in plans.items():
        lanes = [(path[i], path[i + 1]) for i in range(0, len(path), 2)]
        signs = [math.copysign(1.0, b[1] - a[1]) for a, b in lanes]
        for first, second in zip(signs, signs[1:]):
            assert first != second, (
                f"{vehicle} flies two lanes in the same direction, so it "
                f"deadheads the length of the box between them")


# ------------------------------------------------------------------ overlap
def test_neighbouring_lanes_overlap_and_the_outer_lanes_reach_the_edge():
    """The spacing rule stated as the two gaps it has to close.

    A disc of radius r sweeps a band 2r wide, so lanes further apart than 2r
    leave a strip of ground between them and a lane further than r from the
    edge of its strip leaves one along the edge. Both are asserted, because
    a planner can close one and open the other.
    """
    area, plans = baseline_plans()
    r = area.sensor_radius_m
    for vehicle, (strip, _path) in plans.items():
        lanes = lane_positions(strip, r)
        spacing = lane_spacing(strip.width, r)
        assert spacing < 2 * r, (
            f"{vehicle}'s lanes are {spacing} m apart against a {r} m sensor, "
            f"so consecutive swaths do not overlap")
        for west, east in zip(lanes, lanes[1:]):
            assert east - west == pytest.approx(spacing)
        assert lanes[0] - strip.x_min <= r
        assert strip.x_max - lanes[-1] <= r


def test_the_lane_count_is_the_fewest_that_can_cover_the_strip():
    """Minimality, stated as what one fewer lane would fail to do.

    Asserting the ceiling expression would restate the implementation. This
    asks the two questions the count exists to answer: n lanes put every
    point of the strip within reach of the sensor, and n - 1 do not.
    """
    for width in (12.5, 25.0, 48.0, 50.0, 51.0, 200.0):
        for r in (3.0, 6.0, 12.0):
            n = lane_count(width, r)
            assert width / (2.0 * n) <= r + 1e-9, (
                f"{n} lanes across {width} m with a {r} m sensor still leave "
                f"a gap")
            if n > 1:
                assert width / (2.0 * (n - 1)) > r + 1e-9, (
                    f"{n - 1} lanes would have covered {width} m with a {r} m "
                    f"sensor, so {n} is one lane of wasted flying")


def test_neighbouring_strips_keep_their_lanes_apart():
    """Two vehicles at the same altitude must not be handed the same line.

    Lanes laid on the strip edges rather than half a spacing inside them put
    the eastmost lane of one strip and the westmost of the next on exactly
    the same x. The altitude layers would still separate them here, and the
    moment two vehicles share a layer, which `encounter.yaml` arranges on
    purpose, nothing would.
    """
    area, plans = baseline_plans()
    lanes = {v: lane_positions(strip, area.sensor_radius_m)
             for v, (strip, _p) in plans.items()}
    worst = min(abs(a - b)
                for va, la in lanes.items()
                for vb, lb in lanes.items() if va < vb
                for a in la for b in lb)
    assert worst >= check_geometry.MIN_SEPARATION, (
        f"two vehicles' lanes come within {worst:.2f} m of each other, under "
        f"the {check_geometry.MIN_SEPARATION} m floor")


# ----------------------------------------------------------------- coverage
def test_the_plan_reaches_every_cell_of_the_frozen_box():
    """The question the mission row is graded on, asked of the plan.

    Exact rather than sampled: the closest the continuous plan comes to each
    cell centre. If this fails, no pose rate and no flying accuracy can save
    the run, because the ground was never planned over.
    """
    area, plans = baseline_plans()
    missed = []
    for x, y in area.cell_centres():
        nearest = min(distance_to_path(path, x, y)
                      for _strip, path in plans.values())
        if nearest > area.sensor_radius_m:
            missed.append(((x, y), round(nearest, 3)))
    assert not missed, (
        f"{len(missed)} of {area.cell_count} cells are further than "
        f"{area.sensor_radius_m} m from every planned lane: {missed[:6]}")


def test_each_vehicle_covers_its_own_strip_and_needs_no_help():
    """Coverage must not depend on a neighbour's footprint spilling over.

    It would still add up to a full box, and it would stop adding up the
    moment one vehicle is taken for the relay role, which is precisely what
    `mission_integrated` does at t = 70 s.
    """
    area, plans = baseline_plans()
    for vehicle, (strip, path) in plans.items():
        for x, y in area.cell_centres():
            if not strip.contains(x, y):
                continue
            assert distance_to_path(path, x, y) <= area.sensor_radius_m, (
                f"{vehicle} never comes within {area.sensor_radius_m} m of "
                f"({x}, {y}), which is in its own strip")


def test_coverage_survives_being_reduced_to_pose_samples():
    """The metric reads poses, not the line between them.

    architecture.md scores coverage from vehicle poses sampled at a frozen
    rate, so the plan has to be covered by a set of points rather than by an
    unbroken path. Checked at the survey speed and then at a step ten times
    coarser, because a plan that only just covers the box at one sampling
    rate is a plan that reports a different figure on a slower machine.
    """
    area, plans = baseline_plans()
    fine = check_geometry.SURVEY_SPEED / POSE_HZ
    for step in (fine, fine * 10.0):
        covered = set()
        for _strip, path in plans.values():
            covered |= covered_indices(
                area, sample_path(path, step), area.sensor_radius_m)
        assert len(covered) == area.cell_count, (
            f"{area.cell_count - len(covered)} cells of {area.cell_count} are "
            f"missed when the plan is sampled every {step:.3f} m")


def test_a_missing_lane_leaves_a_gap_down_the_middle_of_a_strip():
    """The mutation that proves the coverage assertions can fail.

    Without it, every coverage test above could be passing because the
    helper never reports a miss. The middle lane is removed from one strip
    and the same question is asked again, and it has to come back with cells
    nobody flew over.
    """
    area, plans = baseline_plans()
    strip, _path = plans["uav_1"]
    lanes = lane_positions(strip, area.sensor_radius_m)
    assert len(lanes) >= 3, "the frozen strip needs a middle lane to remove"
    keep = lanes[:1] + lanes[2:]
    crippled = []
    for i, x in enumerate(keep):
        ends = ((strip.y_min, strip.y_max) if i % 2 == 0
                else (strip.y_max, strip.y_min))
        crippled += [(x, ends[0], 30.0), (x, ends[1], 30.0)]

    missed = [(x, y) for x, y in area.cell_centres()
              if strip.contains(x, y)
              and distance_to_path(crippled, x, y) > area.sensor_radius_m]
    assert missed, (
        "removing a lane changed nothing, so the coverage checks above are "
        "not measuring coverage")


def test_lanes_that_stop_short_of_the_boundary_leave_the_end_cells_uncovered():
    """The mutation behind the turns-at-the-boundary rule.

    A plan that turns one sensor radius early looks tidier and loses the row
    of cells at each end of every lane. The loss is small, evenly spread and
    invisible in a single fraction, which is what makes it worth a test of
    its own.
    """
    area, plans = baseline_plans()
    strip, _path = plans["uav_1"]
    inset = area.sensor_radius_m + area.cell_m
    short = []
    for i, x in enumerate(lane_positions(strip, area.sensor_radius_m)):
        lo, hi = strip.y_min + inset, strip.y_max - inset
        ends = (lo, hi) if i % 2 == 0 else (hi, lo)
        short += [(x, ends[0], 30.0), (x, ends[1], 30.0)]

    missed = [(x, y) for x, y in area.cell_centres()
              if strip.contains(x, y)
              and distance_to_path(short, x, y) > area.sensor_radius_m]
    assert missed, (
        "lanes stopping short of the boundary lost no cells, so the boundary "
        "rule is not doing anything")


def test_the_coverage_helper_agrees_with_brute_force():
    """The helper skips cells far from a sample; this proves it skips no other."""
    area = SurveyArea.from_corner((0.0, 0.0), 60.0, 40.0, 10.0, 12.0)
    samples = [(5.0, 5.0, 0.0), (31.0, 22.0, 0.0), (59.0, 39.0, 0.0)]
    fast = covered_indices(area, samples, area.sensor_radius_m)
    slow = {cell_index(area, x, y) for x, y in area.cell_centres()
            if any(math.hypot(x - sx, y - sy) <= area.sensor_radius_m
                   for sx, sy, _sz in samples)}
    assert fast == slow


# --------------------------------------------------------------------- cost
def test_the_plan_is_longer_than_the_sweep_floor_and_not_much_longer():
    """Both bounds matter, and they catch opposite faults.

    A disc of radius r dragged along a path of length L sweeps at most 2rL of
    new ground, so a plan shorter than area / 2r cannot have covered the
    strip whatever it claims. A plan far above it is flying ground it has
    already flown, which costs the run duration the coverage gate has to fit
    inside.
    """
    area, plans = baseline_plans()
    for vehicle, (strip, path) in plans.items():
        floor = strip.area_m2 / (2.0 * area.sensor_radius_m)
        assert minimum_sweep_length(strip.area_m2, area.sensor_radius_m) == \
            pytest.approx(floor)
        flown = path_length(path)
        assert flown >= floor, (
            f"{vehicle} plans {flown:.1f} m, under the {floor:.1f} m any path "
            f"needs to sweep {strip.area_m2:.0f} square metres")
        assert flown <= MAX_LENGTH_OVER_SWEEP_FLOOR * floor, (
            f"{vehicle} plans {flown:.1f} m against a {floor:.1f} m floor, "
            f"a factor of {flown / floor:.2f}")


def test_the_strip_can_be_flown_inside_the_frozen_run_duration():
    """The survey has to fit in the run the gate scores it over.

    Taken at the survey speed rather than the cruise speed, which is the
    slower of the two the design names and therefore the conservative
    direction. The ingress is the real distance from that vehicle's frozen
    start position to the first waypoint of its own strip, and the takeoff
    allowance is the one `mission_integrated` freezes.
    """
    area, plans = baseline_plans()
    worst = 0.0
    for vehicle, (_strip, path) in plans.items():
        start = check_geometry.START[vehicle]
        ingress = math.dist(start, path[0]) / check_geometry.CRUISE_SPEED
        survey = path_length(path) / check_geometry.SURVEY_SPEED
        worst = max(worst, check_geometry.T_TAKEOFF + ingress + survey)
    assert worst < BASELINE_DURATION_S, (
        f"the slowest vehicle needs {worst:.1f} s of a {BASELINE_DURATION_S} s "
        f"run before any allowance for turning or station keeping")


# -------------------------------------------------------------------- shape
def test_starting_from_the_north_mirrors_the_first_lane_and_nothing_else():
    _area, south = baseline_plans(start_north=False)
    _area, north = baseline_plans(start_north=True)
    for vehicle in south:
        a = south[vehicle][1]
        b = north[vehicle][1]
        assert [w[0] for w in a] == [w[0] for w in b], (
            f"{vehicle} changes lane order with the starting end")
        assert a[0][1] != b[0][1], f"{vehicle} starts at the same end either way"


def test_sample_path_walks_the_whole_plan_at_the_step_it_was_given():
    _area, plans = baseline_plans()
    _strip, path = plans["uav_2"]
    step = 1.0
    samples = list(sample_path(path, step))
    assert samples[0] == path[0]
    assert samples[-1] == path[-1]
    assert max(math.dist(a, b) for a, b in zip(samples, samples[1:])) <= step + 1e-9
    assert len(samples) >= path_length(path) / step


def test_a_strip_or_a_sensor_with_no_extent_is_refused():
    strip = partition(baseline_area(), VEHICLES)[0]
    with pytest.raises(PlanError):
        lane_count(0.0, 12.0)
    with pytest.raises(PlanError):
        lane_count(50.0, 0.0)
    with pytest.raises(PlanError):
        plan_path(strip, 12.0, 0.0)
    with pytest.raises(PlanError):
        list(sample_path(plan_path(strip, 12.0, 30.0), 0.0))
