"""What the executor has to be true of while it flies a plan.

Two of these are the reason the module exists rather than being three lines
inside the node.

The first is that a waypoint is never skipped. An executor that flies to
whichever waypoint is nearest looks identical on a clean run and quietly
abandons half a lane on a run where the vehicle started from the wrong end or
was pushed across its strip. Coverage then falls by one swath and the run
record still describes a completed survey.

The second is that progress is a distance. `mission_integrated` freezes the
claim that each surveyor is 58% of the way through its strip when the relay
dies, and the run record carries a coverage figure at that moment. A
lawnmower's waypoints are not evenly spaced, so counting them answers a
different question and answers it confidently.

The handover tests are set comparisons for the same reason the delivery
claims are. "The strip was reassigned" is satisfied by a vehicle that flies
one waypoint of it, and "nothing was lost" is satisfied by a vehicle that
flies the same waypoint twice. What has to hold is that the two vehicles
between them fly every waypoint of both plans exactly once.
"""

import math

import pytest

import check_geometry

from uavx_mission.boustrophedon import path_length, plan_path
from uavx_mission.executor import (ExecutorError, MissionExecutor,
                                   MissionState)
from uavx_mission.frames import frozen_to_px4, px4_to_frozen
from uavx_mission.partition import Strip, partition
from uavx_mission.survey_area import SurveyArea

VEHICLES = ("uav_1", "uav_2", "uav_3", "uav_4")


def strips():
    return partition(SurveyArea.baseline(), VEHICLES)


def executor_for(vehicle_id, radius=2.0, start_north=False):
    area = SurveyArea.baseline()
    strip = next(s for s in strips() if s.vehicle_id == vehicle_id)
    altitude = check_geometry.START[vehicle_id][2]
    path = plan_path(strip, area.sensor_radius_m, altitude,
                     start_north=start_north)
    return MissionExecutor(vehicle_id, strip, path, radius), strip, path


def fly(executor, limit=1000):
    """Teleport to each setpoint in turn until the plan is finished.

    Deliberately not a simulation. The executor's job is to decide what the
    next setpoint is, so a test that flew it with a controller would be
    grading the controller.
    """
    steps = 0
    while not executor.complete and steps < limit:
        target = executor.target()
        if target is None:
            break
        executor.update(target)
        steps += 1
    return steps


# ------------------------------------------------------------- refusals
def test_a_plan_that_leaves_its_strip_is_refused():
    """The partition and the planner disagreeing is worth a crash.

    A vehicle flying over its neighbour's strip at its own altitude covers
    ground somebody else is already covering, leaves its own uncovered, and
    both vehicles report a healthy survey.
    """
    strip = strips()[0]
    outside = (strip.x_max + 5.0, strip.y_min, 30.0)
    with pytest.raises(ExecutorError) as excinfo:
        MissionExecutor("uav_1", strip, [outside])
    assert "leaves its strip" in str(excinfo.value)


def test_an_empty_plan_or_a_zero_acceptance_radius_is_refused():
    strip = strips()[0]
    with pytest.raises(ExecutorError):
        MissionExecutor("uav_1", strip, [])
    with pytest.raises(ExecutorError):
        MissionExecutor("uav_1", strip,
                        [(strip.x_min + 1.0, strip.y_min, 30.0)], 0.0)


# ------------------------------------------------------------- sequencing
def test_it_flies_to_the_next_waypoint_and_not_to_the_nearest_one():
    """The fault that costs a swath of coverage and reports success.

    The vehicle is placed exactly on a waypoint three legs ahead. A
    nearest-waypoint executor retires everything up to it; this one is still
    flying to the first waypoint, because that is the one it was told to fly
    to and the lane in between is the ground the survey is for.
    """
    executor, _strip, path = executor_for("uav_1")
    assert executor.target() == path[0]

    ahead = executor.update(path[3])
    assert ahead == path[0], (
        f"standing on {path[3]} made the executor target {ahead}, skipping "
        f"the two legs before it")
    assert executor.visited == ()
    assert executor.progress_fraction() == 0.0


def test_it_advances_only_when_the_vehicle_actually_arrives():
    executor, _strip, path = executor_for("uav_1", radius=2.0)
    first = path[0]

    just_outside = (first[0], first[1] + 2.5, first[2])
    assert executor.update(just_outside) == first
    assert executor.visited == ()

    just_inside = (first[0], first[1] + 1.5, first[2])
    assert executor.update(just_inside) == path[1]
    assert executor.visited == (first,)


def test_it_retires_one_waypoint_per_report():
    """Two waypoints inside one acceptance radius must not both be retired.

    Retiring both flies neither of the legs between them. On a real plan the
    waypoints are hundreds of metres apart, so this is built small on
    purpose: the rule has to hold for the degenerate case or it is not a
    rule.
    """
    strip = Strip("uav_1", 0, 0.0, 100.0, 0.0, 100.0, True)
    plan = [(10.0, 10.0, 30.0), (10.5, 10.0, 30.0), (90.0, 90.0, 30.0)]
    executor = MissionExecutor("uav_1", strip, plan, acceptance_radius_m=5.0)

    assert executor.update(plan[0]) == plan[1]
    assert executor.visited == (plan[0],)
    assert executor.update(plan[1]) == plan[2]
    assert executor.visited == (plan[0], plan[1])


def test_a_full_flight_visits_every_waypoint_once_and_reports_complete():
    executor, _strip, path = executor_for("uav_1")
    steps = fly(executor)
    assert steps == len(path)
    assert executor.visited == tuple(path)
    assert executor.complete
    assert executor.state is MissionState.COMPLETE
    assert executor.target() is None
    assert executor.remaining == ()


# --------------------------------------------------------------- progress
def test_progress_is_the_distance_flown_and_not_the_waypoints_counted():
    """The two answers differ, and only one of them is what a coverage figure means.

    A lane is the height of the box and a turn is one lane spacing, so the
    waypoint count runs ahead of the work every time a turn is retired.
    """
    executor, _strip, path = executor_for("uav_1")
    total = sum(math.dist(a, b) for a, b in zip(path, path[1:]))

    executor.update(path[0])
    executor.update(path[1])

    flown = math.dist(path[0], path[1])
    assert executor.progress_fraction() == pytest.approx(flown / total)

    by_waypoint = 2 / len(path)
    assert abs(executor.progress_fraction() - by_waypoint) > 0.01, (
        "the distance answer and the waypoint answer are too close together "
        "for this test to tell them apart")


def test_progress_counts_the_part_of_the_current_leg_already_behind_the_vehicle():
    executor, _strip, path = executor_for("uav_1")
    total = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    executor.update(path[0])

    half = ((path[0][0] + path[1][0]) / 2,
            (path[0][1] + path[1][1]) / 2,
            path[0][2])
    executor.update(half)
    expected = math.dist(path[0], half) / total
    assert executor.progress_fraction() == pytest.approx(expected)


def test_drifting_sideways_off_a_lane_is_not_progress_along_it():
    """The projection, not the distance from the last waypoint.

    A vehicle pushed 40 m across its strip has flown further and covered
    nothing new, and a progress figure that grew would be reporting the wind.
    """
    executor, strip, path = executor_for("uav_1")
    executor.update(path[0])

    along = ((path[0][0] + path[1][0]) / 2,
             (path[0][1] + path[1][1]) / 2,
             path[0][2])
    executor.update(along)
    on_track = executor.progress_fraction()

    drifted = (min(along[0] + 40.0, strip.x_max - 1.0), along[1], along[2])
    executor.update(drifted)
    assert executor.progress_fraction() == pytest.approx(on_track)


def test_progress_never_goes_backwards_and_ends_at_one():
    executor, _strip, path = executor_for("uav_1")
    seen = [executor.progress_fraction()]
    while not executor.complete:
        target = executor.target()
        executor.update(target)
        seen.append(executor.progress_fraction())
    assert all(b >= a - 1e-12 for a, b in zip(seen, seen[1:])), seen
    assert seen[0] == 0.0
    assert seen[-1] == pytest.approx(1.0)


# --------------------------------------------------------------- handover
def test_handover_gives_up_exactly_the_unflown_work():
    executor, _strip, path = executor_for("uav_1")
    for waypoint in path[:3]:
        executor.update(waypoint)

    work = executor.hand_over()
    assert work == tuple(path[3:])
    assert executor.remaining == ()
    assert executor.visited == tuple(path[:3])
    assert executor.complete


def test_handing_over_twice_returns_nothing_the_second_time():
    """Work handed to two vehicles is flown twice and reported once."""
    executor, _strip, _path = executor_for("uav_1")
    executor.update(executor.target())
    first = executor.hand_over()
    assert first
    assert executor.hand_over() == ()


def test_the_two_halves_of_a_handover_cover_both_strips_exactly_once():
    """The set comparison, which is the only version of this claim that holds.

    Counting waypoints flown is satisfied by flying one of them twice, and
    "the strip was reassigned" is satisfied by flying one of them at all.
    """
    leaver, _s1, leaver_plan = executor_for("uav_1")
    stayer, _s2, stayer_plan = executor_for("uav_2")

    for waypoint in leaver_plan[:3]:
        leaver.update(waypoint)
    stayer.update(stayer_plan[0])

    stayer.inherit(leaver.hand_over())
    fly(stayer)

    flown = list(leaver.visited) + list(stayer.visited)
    assert len(flown) == len(set(flown)), "a waypoint was flown twice"
    assert set(flown) == set(leaver_plan) | set(stayer_plan), (
        "the two vehicles between them did not cover both plans")
    assert leaver.complete and stayer.complete


def test_the_inheriting_vehicle_finishes_its_own_strip_first():
    """architecture.md freezes the order, and the order is not decoration.

    The inherited work is in another strip. Flying it before its own would
    take the vehicle across the box and back at survey speed, and would put
    it over its neighbour's ground while the neighbour is still there.
    """
    leaver, _s1, leaver_plan = executor_for("uav_1")
    stayer, _s2, stayer_plan = executor_for("uav_2")

    stayer.inherit(leaver.hand_over())
    fly(stayer)

    visited = list(stayer.visited)
    assert visited[:len(stayer_plan)] == list(stayer_plan)
    assert visited[len(stayer_plan):] == list(leaver_plan)


def test_a_finished_vehicle_that_inherits_work_goes_back_to_surveying():
    leaver, _s1, leaver_plan = executor_for("uav_1")
    stayer, _s2, _p2 = executor_for("uav_2")
    fly(stayer)
    assert stayer.complete

    stayer.inherit(leaver.hand_over())
    assert stayer.state is MissionState.SURVEY
    assert stayer.target() == leaver_plan[0]
    fly(stayer)
    assert stayer.complete


def test_inheriting_nothing_changes_nothing():
    stayer, _strip, plan = executor_for("uav_2")
    stayer.update(plan[0])
    before = (stayer.remaining, stayer.state, stayer.progress_fraction())
    stayer.inherit(())
    assert (stayer.remaining, stayer.state,
            stayer.progress_fraction()) == before


# ------------------------------------------------------------------- role
def test_the_relay_role_suspends_the_survey_and_a_release_resumes_it_in_place():
    """A released relay must not restart its strip or skip the waypoint it lost.

    Restarting flies ground twice and runs the survey past the end of the
    scenario. Skipping leaves a lane's worth of the box unflown, and both
    look like a completed mission from outside.
    """
    executor, _strip, path = executor_for("uav_3")
    executor.update(path[0])
    executor.update(path[1])
    interrupted = executor.target()
    progress = executor.progress_fraction()

    slot = (317.3, -36.8, 75.0)
    executor.assign_relay(slot)
    assert executor.state is MissionState.RELAY
    assert executor.target() == slot
    # Reporting a position on the way to the slot must not retire a survey
    # waypoint. The last report is deliberately the waypoint the vehicle was
    # flying to when it was taken: a position that would move the progress
    # figure if it were being recorded. Reporting the slot last instead hides
    # the fault, because the slot is behind the current leg and the
    # projection clamps to zero either way.
    assert executor.update(slot) == slot
    assert executor.update(interrupted) == slot
    assert executor.visited == tuple(path[:2])
    assert executor.progress_fraction() == pytest.approx(progress)

    executor.release()
    assert executor.state is MissionState.SURVEY
    assert executor.target() == interrupted
    assert executor.progress_fraction() == pytest.approx(progress)
    fly(executor)
    assert executor.visited == tuple(path)


def test_flying_out_to_the_relay_slot_is_not_progress_through_the_strip():
    """The relay crosses its own lanes on the way out and covers none of them.

    Progress through a strip is what `coverage_fraction_at_kill` rests on, so
    a vehicle that was taken for the relay role has to come back reporting
    the progress it left with. Crediting the flight to the slot would report
    a strip as further along than anybody flew it, and the ground under the
    difference is never surveyed by anyone.

    The position reported here is the waypoint the vehicle was heading for
    when it was taken, which sits at the far end of the leg the progress
    figure is measured along. A relay flight recorded as survey work would
    move the figure by that whole leg.
    """
    executor, _strip, path = executor_for("uav_3")
    executor.update(path[0])
    executor.update(path[1])
    interrupted = executor.target()
    before = executor.progress_fraction()

    executor.assign_relay((317.3, -36.8, 75.0))
    executor.update(interrupted)
    assert executor.progress_fraction() == pytest.approx(before)

    executor.release()
    assert executor.progress_fraction() == pytest.approx(before)
    assert executor.target() == interrupted


def test_a_release_without_an_assignment_does_nothing():
    executor, _strip, path = executor_for("uav_3")
    executor.release()
    assert executor.state is MissionState.SURVEY
    assert executor.target() == path[0]


def test_a_relay_that_handed_its_work_over_stays_a_relay_until_released():
    executor, _strip, path = executor_for("uav_3")
    executor.update(path[0])
    executor.assign_relay((317.3, -36.8, 75.0))
    executor.hand_over()
    assert executor.state is MissionState.RELAY
    executor.release()
    assert executor.state is MissionState.COMPLETE


# ------------------------------------------------- composed with the frame
def test_the_setpoint_sent_to_px4_is_the_planned_waypoint():
    """The executor and the frame conversion, composed the way the node does.

    Each of the two is right on its own in the tests above. This is the
    thing the vehicle actually receives, and it is where an off-by-a-home or
    a swapped axis would land.
    """
    executor, _strip, path = executor_for("uav_4")
    home = (165.0, -37.5, 30.0)
    while not executor.complete:
        target = executor.target()
        setpoint = frozen_to_px4(target, home)
        assert px4_to_frozen(setpoint, home) == pytest.approx(target)
        executor.update(target)


def test_the_whole_plan_is_flown_at_the_length_the_planner_reported():
    """Nothing between the planner and the executor changes the distance."""
    executor, _strip, path = executor_for("uav_4")
    fly(executor)
    flown = sum(math.dist(a, b)
                for a, b in zip(executor.visited, executor.visited[1:]))
    assert flown == pytest.approx(path_length(path))
