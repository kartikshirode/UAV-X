"""Giving way has to stop the aircraft, not only the number in the record.

Chunk 4.5 put the yield rule in the router and gave every mission executor a
`/{v}/yield_hold` subscription. The executor stored the flag and one kind of
work read it: a track vehicle, which holds by stopping its own clock. The
encounter pair both fly tracks, so the chunk passed with the gap still open.

The gap matters at chunk 4.7. mission_integrated flies two surveyors, two
station keepers and a role manager that sends one of them to a relay slot, and
the run record carries `yield_events_by_node` and `yield_hold_seconds` straight
off the routers. Those numbers come from the rule and not from the aircraft, so
a vehicle that ignored the hold would still be written down as having given
way. That record is the one the proposal and the video point at.

So: a track holds its schedule, and every other kind of work holds a point.
These tests drive the three real callbacks against a stand-in for the node,
because what is being checked is the wiring between them and starting a ROS
node needs a graph.

    python3 -m pytest -q uavx_ws/src/uavx_mission/test/test_yield_hold.py

Needs rclpy and px4_msgs, so it skips on a bare checkout and runs for real
under `colcon test`, which is where the gate calls it.
"""

from types import SimpleNamespace

import pytest

pytest.importorskip("rclpy", reason="mission_node subclasses rclpy.node.Node")
pytest.importorskip("px4_msgs.msg", reason="the setpoint is a PX4 message")

from uavx_mission import frames, mission_node  # noqa: E402

# Somewhere off the world origin, because a home of (0, 0, 0) hides every
# frame error this package exists to prevent.
HOME = (0.0, 7.5, 0.0)


class Recorder:
    """A publisher that keeps what it was given."""

    def __init__(self):
        self.sent = []

    def publish(self, msg):
        self.sent.append(msg)

    @property
    def last(self):
        return self.sent[-1]


class Plan:
    """A mission executor that always wants the same waypoint."""

    def __init__(self, target=(465.0, 60.0, 50.0)):
        self.target = target
        self.asked = 0

    def update(self, here):
        self.asked += 1
        return self.target


class Executor:
    """The parts of MissionNode the three callbacks under test touch.

    It borrows the real methods rather than restating them, so a change to any
    of the three is a change to what these tests run.
    """

    on_yield_hold = mission_node.MissionNode.on_yield_hold
    on_position = mission_node.MissionNode.on_position
    publish_control_mode = mission_node.MissionNode.publish_control_mode

    def __init__(self, work=mission_node.SURVEY):
        self.work = work
        self.home = HOME
        self.holding = False
        self.hold_point = None
        self.slot_target = None
        self.positions_seen = 0
        self.last_setpoint = None
        self.mission = Plan()
        self.control_mode = Recorder()
        self.setpoint = Recorder()
        self.advanced = 0

    # The track's own hold, stubbed. What it does is chunk 4.5's and is tested
    # in uavx_comms; what matters here is that the heartbeat still calls it.
    def advance_track(self, now):
        self.advanced += 1

    def stamp(self):
        return 1_000_000

    def get_clock(self):
        return SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=10 ** 9))

    # ------------------------------------------------------------- helpers
    def at(self, x, y, z):
        """One PX4 position report, in the local NED frame it arrives in."""
        local = frames.frozen_to_px4((x, y, z), HOME)
        self.on_position(SimpleNamespace(x=local[0], y=local[1], z=local[2]))

    def told(self, holding):
        self.on_yield_hold(SimpleNamespace(data=holding))

    def commanded(self):
        """Where the last heartbeat told PX4 to go, back in the frozen frame."""
        self.publish_control_mode()
        return frames.px4_to_frozen(tuple(self.setpoint.last.position), HOME)


def approx(point):
    return pytest.approx(point, abs=1e-4)


# ------------------------------------------------------------- the survey
def test_a_surveyor_flies_its_plan_when_nothing_is_holding_it():
    node = Executor()
    node.at(400.0, 20.0, 50.0)
    assert node.commanded() == approx(node.mission.target)


def test_a_held_surveyor_is_commanded_to_where_it_is():
    """Not to the waypoint it was already most of the way to.

    A boustrophedon lane is over a hundred metres long and its waypoints sit
    at the ends, so a hold that only stopped the plan advancing would leave
    the aircraft flying the rest of the lane before it stopped. That is a
    vehicle crossing at survey speed while the record says it gave way.
    """
    node = Executor()
    node.at(400.0, 20.0, 50.0)
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    assert node.commanded() == approx((402.0, 20.0, 50.0))


def test_the_hold_point_is_latched_and_not_chased():
    """The aircraft still has momentum when the router says to give way.

    Re-reading the position every cycle would walk the setpoint along with
    the drift, and the vehicle would coast through the crossing a metre at a
    time while reporting a hold the whole way.
    """
    node = Executor()
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    for step in range(1, 5):
        node.at(402.0 + step, 20.0, 50.0)
    assert node.hold_point == approx((402.0, 20.0, 50.0))
    assert node.commanded() == approx((402.0, 20.0, 50.0))


def test_releasing_puts_the_plan_back_on_the_next_heartbeat():
    """Nothing is restored on release, because nothing was overwritten."""
    node = Executor()
    node.at(400.0, 20.0, 50.0)
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    assert node.commanded() == approx((402.0, 20.0, 50.0))
    node.told(False)
    assert node.commanded() == approx(node.mission.target)


def test_a_release_does_not_wait_for_the_next_position_report():
    """PX4 can go quiet, and a released vehicle may not stay parked for it."""
    node = Executor()
    node.at(400.0, 20.0, 50.0)
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    node.told(False)
    assert node.hold_point is None
    assert node.commanded() == approx(node.mission.target)


def test_a_second_hold_latches_the_second_place():
    node = Executor()
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    node.told(False)
    node.at(430.0, 20.0, 50.0)
    node.told(True)
    node.at(431.0, 20.0, 50.0)
    assert node.commanded() == approx((431.0, 20.0, 50.0))


# -------------------------------------------------------------- the slot
def test_a_vehicle_sent_to_a_slot_still_gives_way():
    """Separation outranks the link.

    A role manager repositioning a relay is the swarm keeping the ground
    station reachable, and it is still a command to fly somewhere. Ten metres
    is a floor, and the link can wait the two seconds out.
    """
    node = Executor(work=mission_node.STATION)
    node.slot_target = (330.0, 0.0, 40.0)
    node.last_setpoint = list(frames.frozen_to_px4(node.slot_target, HOME))
    node.told(True)
    node.at(300.0, 5.0, 40.0)
    assert node.commanded() == approx((300.0, 5.0, 40.0))
    node.told(False)
    assert node.commanded() == approx(node.slot_target)


def test_a_slot_command_is_not_overwritten_by_the_plan_while_held():
    """The hold is an override at the heartbeat and never an edit of the plan."""
    node = Executor()
    node.slot_target = (330.0, 0.0, 40.0)
    wanted = list(frames.frozen_to_px4(node.slot_target, HOME))
    node.last_setpoint = list(wanted)
    node.told(True)
    node.at(300.0, 5.0, 40.0)
    node.commanded()
    assert node.mission.asked == 0
    assert node.last_setpoint == wanted


# -------------------------------------------------------------- the track
def test_a_track_vehicle_has_no_hold_point():
    """Its hold is its clock stopping, which advance_track already does.

    Latching a point here as well would command the place the aircraft was
    rather than the place its schedule says it should be, and those differ by
    the tracking error. Chunk 4.5 flew and measured the clock version.
    """
    node = Executor(work=mission_node.TRACK)
    node.told(True)
    node.at(250.0, -10.0, 45.0)
    assert node.hold_point is None


def test_the_heartbeat_still_advances_the_track_while_held():
    node = Executor(work=mission_node.TRACK)
    node.last_setpoint = list(frames.frozen_to_px4((250.0, -10.0, 45.0), HOME))
    node.told(True)
    node.publish_control_mode()
    assert node.advanced == 1


# ------------------------------------------------------- nothing to fly yet
def test_a_vehicle_with_no_plan_and_no_hold_publishes_no_setpoint():
    """Before its first setpoint exists a vehicle has nowhere to be."""
    node = Executor(work=mission_node.STATION)
    node.publish_control_mode()
    assert node.setpoint.sent == []
    assert len(node.control_mode.sent) == 1


def test_a_held_vehicle_publishes_a_setpoint_even_with_no_plan():
    """Holding is somewhere to be, so the heartbeat has something to send."""
    node = Executor(work=mission_node.STATION)
    node.told(True)
    node.at(402.0, 20.0, 50.0)
    assert node.last_setpoint is None
    assert node.commanded() == approx((402.0, 20.0, 50.0))
