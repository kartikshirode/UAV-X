"""The mission executor as a ROS node, and the only file here that knows ROS.

Everything this node decides is decided in `executor`, `boustrophedon`,
`partition`, `station` and `frames`, all of which are plain arithmetic over
tuples and are proved with no simulator running. What is left here is wiring,
and wiring is the part a unit test cannot honestly cover, so there is as
little of it as the job allows.

It flies one of two things, and which one is decided by the `station_enu`
parameter alone. Unset, the default, and it partitions the survey box, plans
a boustrophedon path over its own strip and works through it. Set, and it
holds that one point, which is what architecture.md section 6 asks of
`relay_required` and `direct_only`: common geometry, station-keeping, no
survey motion. A station is not a one waypoint survey. `MissionExecutor`
refuses an empty plan and checks that every waypoint lies inside the strip it
was handed, and both of those rules are worth more than the code they would
save here.

The endpoint allowlist in architecture.md section 1 gives this process four
topics and no others:

    publish    /uavx/<own>/tx           the swarm side, one hop of radio away
    publish    /<own>/fmu/in/...        its own PX4 namespace
    subscribe  /uavx/<own>/rx
    subscribe  /<own>/fmu/out/...

Every one of them is built from the `vehicle_id` parameter, never written out
with an id in it. That is not only style: scripts/check_seam.sh counts the
distinct vehicle ids appearing in a file's topic literals and calls a file
with two of them a bypass, because a process that can name a second vehicle's
endpoint can talk to it without crossing the radio.

Nothing here reads simulator ground truth. Position comes from this vehicle's
own PX4 estimate, in this vehicle's own local NED frame, and `frames` is what
puts it back in the frame the survey box is frozen in.
"""

from __future__ import annotations

import math
from typing import Optional

import rclpy
from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleLocalPosition
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from uavx_msgs.msg import RoleAssignment, SwarmPacket

from std_msgs.msg import Bool

from uavx_mission import frames, station
from uavx_mission.boustrophedon import plan_path
from uavx_mission.executor import MissionExecutor
from uavx_mission.partition import partition, strip_of
from uavx_mission.survey_area import (BASELINE_CELL_M, BASELINE_SENSOR_RADIUS_M,
                                      BASELINE_SIDE_M, BASELINE_SW_CORNER_M,
                                      SurveyArea)
from uavx_mission.track import Track

# What one vehicle has been given to do. Exactly one of these, decided once in
# the constructor from the parameters, because the question used to be asked
# three times as `station is None` and the third one was wrong the moment a
# third kind of work existed. uavx_sim.work is the scenario side of the same
# three and refuses a vehicle with none or with two.
SURVEY = "survey"
STATION = "station"
TRACK = "track"

# architecture.md section 6: observation packets, 5 Hz per surveying vehicle.
OBSERVATION_HZ = 5.0

# PX4 leaves offboard when OffboardControlMode stops arriving, and PX4's own
# rcS sets COM_OF_LOSS_T to half a second in SITL. Chunk 2.4's second survey
# run lost all four vehicles to that: the heartbeat was published inside the
# position callback, so it depended on another topic's cadence and stopped
# outright the moment the plan finished, which is exactly when the vehicle is
# still airborne and needs to hold. PX4 fell back to position control, found no
# manual control to read, logged "Matching flight task was not able to run" and
# landed the vehicle. It has its own timer now, well above the 2 Hz PX4 asks
# for and independent of everything else.
CONTROL_MODE_HZ = 20.0

# architecture.md section 3, "What delivered once means". Both are frozen and
# both are named rather than written into the call below, because a bare 300
# beside a bare 256 in a message builder is unreadable and unsearchable.
OBSERVATION_LIFETIME_S = 300.0
OBSERVATION_BYTES = 256

# PX4 publishes its estimates best effort with a small queue. A reliable
# subscription simply never matches it, and the node then sits waiting for a
# position that is being published a metre away.
PX4_QOS = QoSProfile(
    reliability=QoSReliabilityPolicy.BEST_EFFORT,
    durability=QoSDurabilityPolicy.VOLATILE,
    history=QoSHistoryPolicy.KEEP_LAST,
    depth=5,
)


class MissionNode(Node):
    """One vehicle's survey, wired to its own PX4 and its own radio endpoint."""

    def __init__(self) -> None:
        super().__init__("mission_executor")

        self.declare_parameter("vehicle_id", "")
        self.declare_parameter("swarm_vehicles", ["uav_1", "uav_2", "uav_3", "uav_4"])
        self.declare_parameter("survey_altitude_m", 0.0)
        # Where this vehicle stands in the frozen frame. It is not
        # computed here and it must not be: scripts/sitl_multi.sh
        # writes runs/.launcher-spawn.json when it places the
        # vehicles, and the runner copies it into every record as
        # spawn_x_m and spawn_y_m. A second derivation of the same
        # offset is a second answer to where the survey box is.
        self.declare_parameter("home_enu", [0.0, 0.0, 0.0])
        self.declare_parameter("acceptance_radius_m", 2.0)
        self.declare_parameter("start_north", False)
        # The survey box, defaulting to the frozen survey_baseline one.
        # mission_integrated flies a different box over the same code, so the
        # area is configuration rather than a second constant in this file.
        self.declare_parameter("area_sw_m", list(BASELINE_SW_CORNER_M))
        self.declare_parameter("area_width_m", BASELINE_SIDE_M)
        self.declare_parameter("area_height_m", BASELINE_SIDE_M)
        self.declare_parameter("cell_m", BASELINE_CELL_M)
        self.declare_parameter("sensor_radius_m", BASELINE_SENSOR_RADIUS_M)
        # Chunk 3.4. Where this vehicle holds, in the frozen frame, for a
        # scenario that station-keeps rather than surveys. Three NaNs mean
        # nobody asked, which is what survey_baseline passes. See station.py
        # for why the unset value is not an empty list and not the origin.
        self.declare_parameter("station_enu", list(station.unset()))
        # Whether this process mints observations. False from chunk 3.4
        # onward for any vehicle that also runs a router, because identity is
        # (origin_id, sequence) and two processes on one vehicle minting
        # sequences from zero produce colliding ids. Delivered-once is a
        # comparison of those ids, so the collision would not look like a
        # fault; it would look like a delivery.
        self.declare_parameter("observations", True)
        # Chunk 4.7. How fast the plan advances along a survey lane, and when
        # it starts. A pace of zero hands PX4 the waypoint and lets it fly
        # there at its own limit, which is what survey_baseline wants and what
        # every scenario before this one did. mission_integrated needs the
        # lanes slower than the transit to a relay slot, and one PX4 parameter
        # cannot be two speeds.
        self.declare_parameter("survey_speed_mps", 0.0)
        self.declare_parameter("survey_start_s", 0.0)
        self.declare_parameter("survey_epoch_s", 0.0)
        # Chunk 4.5. The straight line this vehicle flies, for the encounter
        # pair and for nothing else. A speed of zero means this vehicle has no
        # track, which is every vehicle in the other eight scenarios.
        self.declare_parameter("track_start_enu", [0.0, 0.0, 0.0])
        self.declare_parameter("track_end_enu", [0.0, 0.0, 0.0])
        self.declare_parameter("track_start_s", 0.0)
        self.declare_parameter("track_speed_mps", 0.0)
        # Where the scenario's zero sits in the simulated clock this node
        # reads. Zero means the run has not started and the vehicle waits at
        # the head of its line.
        #
        # It arrives at run time rather than at launch because nothing knows
        # it at launch: the scenario's clock starts after the ingress, and the
        # ingress ends when the last vehicle reaches its station. Both track
        # vehicles are given the same number, which is what makes them start
        # together, and starting together is the whole reason neither of them
        # arrives at the crossing first.
        self.declare_parameter("track_epoch_s", 0.0)

        vehicle_id = self.get_parameter("vehicle_id").value
        if not vehicle_id:
            raise ValueError(
                "vehicle_id is required. A mission executor that does not know "
                "which vehicle it is cannot build its own endpoints, and every "
                "topic it holds has to be its own.")
        self.vehicle_id = str(vehicle_id)
        self.home = tuple(float(v) for v in self.get_parameter("home_enu").value)

        altitude_m = float(self.get_parameter("survey_altitude_m").value)
        try:
            self.station = station.station_of(
                self.get_parameter("station_enu").value, altitude_m)
        except station.StationError as exc:
            raise ValueError(f"{self.vehicle_id}: {exc}") from exc
        self.generates = bool(self.get_parameter("observations").value)
        self.track = self._track()
        if self.track is not None and self.station is not None:
            raise ValueError(
                f"{self.vehicle_id} was given a station and a track. A "
                f"vehicle with two jobs is given two places to be, and which "
                f"one it flies comes down to which was read first")
        self.work = (TRACK if self.track is not None else
                     STATION if self.station is not None else SURVEY)

        sw = [float(v) for v in self.get_parameter("area_sw_m").value]
        area = SurveyArea.from_corner(
            (sw[0], sw[1]),
            float(self.get_parameter("area_width_m").value),
            float(self.get_parameter("area_height_m").value),
            float(self.get_parameter("cell_m").value),
            float(self.get_parameter("sensor_radius_m").value))
        if self.work == SURVEY:
            strips = partition(
                area, list(self.get_parameter("swarm_vehicles").value))
            strip = strip_of(strips, self.vehicle_id)
            path = plan_path(
                strip,
                area.sensor_radius_m,
                altitude_m,
                start_north=bool(self.get_parameter("start_north").value),
            )
        else:
            # No partition and no plan. MissionExecutor refuses an empty plan
            # and checks every waypoint lies inside the strip it was handed,
            # and both of those are rules worth keeping, so a station is held
            # beside the executor rather than smuggled through it as a one
            # waypoint survey of a lane nobody assigned.
            strip = path = None
        # Not self.executor. rclpy.node.Node defines `executor` as a
        # property with a setter, and assigning to it calls
        # new_executor.add_node(self) on whatever was assigned. Every
        # mission executor died in this line with AttributeError:
        # 'MissionExecutor' object has no attribute 'add_node', on the
        # first survey run, which was the first time this node had ever
        # been started. `executor` and `handle` are the only two settable
        # properties Node has, and test_node_attributes.py now refuses
        # either name on any node in this workspace.
        self.mission = None
        if self.work == SURVEY:
            self.mission = MissionExecutor(
                self.vehicle_id, strip, path,
                float(self.get_parameter("acceptance_radius_m").value))

        swarm = f"/uavx/{self.vehicle_id}"
        px4 = f"/{self.vehicle_id}/fmu"
        self.tx = self.create_publisher(SwarmPacket, f"{swarm}/tx", 10)
        self.create_subscription(SwarmPacket, f"{swarm}/rx", self.on_packet, 10)
        self.setpoint = self.create_publisher(
            TrajectorySetpoint, f"{px4}/in/trajectory_setpoint", PX4_QOS)
        self.control_mode = self.create_publisher(
            OffboardControlMode, f"{px4}/in/offboard_control_mode", PX4_QOS)
        self.create_subscription(
            VehicleLocalPosition, f"{px4}/out/vehicle_local_position",
            self.on_position, PX4_QOS)

        # Chunk 4.2. Where this vehicle's own role manager says it should be.
        # A vehicle-local topic in this vehicle's namespace, carrying a role
        # message rather than a SwarmPacket, so the seam rules allow it: swarm
        # traffic crosses the radio and this is one process on an aircraft
        # telling another where the aircraft is going.
        self.create_subscription(
            RoleAssignment, f"/{self.vehicle_id}/role_slot",
            self.on_role_slot, 10)
        # Chunk 4.5. This vehicle's own router works out whether it has to
        # give way, because the router is the process that decodes HELLO, and
        # says so here. A vehicle-local topic in this vehicle's namespace
        # carrying no SwarmPacket, which is the role slot's shape and what the
        # seam rules allow.
        self.create_subscription(
            Bool, f"/{self.vehicle_id}/yield_hold", self.on_yield_hold, 10)
        self.holding = False
        self.hold_seconds = 0.0
        # Where the aircraft was when it started giving way, in the frozen
        # frame, or None when it is not holding. Latched once on the rising
        # edge rather than followed live: a setpoint that chases the current
        # position every cycle lets a decelerating vehicle drift the whole way
        # through the conflict it is meant to be waiting out. A track vehicle
        # never has one, because its hold is its clock stopping.
        self.hold_point = None
        # Chunk 4.7. The point this vehicle is actually commanded to while it
        # surveys, which walks towards the executor's waypoint at the pace the
        # scenario named. None until the first position report, because it
        # starts where the ingress left the aircraft and nothing else knows
        # where that is.
        self.paced = None
        self._paced_last_s = None
        self.pace_mps = float(self.get_parameter("survey_speed_mps").value)
        self.survey_start_s = float(self.get_parameter("survey_start_s").value)
        self.track_time_s = 0.0
        self.track_complete = False
        self._track_last_s = None
        self.slot_target = None
        self.slot_commands = 0
        self.slot_errors = 0
        self.slot_messages = 0

        self.sequence = 0
        self.positions_seen = 0
        # The last setpoint sent, so the heartbeat timer can keep holding it
        # after the plan is finished. None until the first position arrives,
        # except in station mode, where the destination is known before the
        # vehicle has said anything and the heartbeat can start flying it
        # there the moment PX4 grants offboard.
        self.last_setpoint = None
        if self.work == STATION:
            self.last_setpoint = [float(v) for v in
                                  frames.frozen_to_px4(self.station, self.home)]
        if self.work == TRACK:
            # The head of the line, which is also where the ingress flew it.
            # Holding here until the scenario's clock arrives is what keeps
            # the pair together: whichever of the two is told first waits for
            # the same instant as the other.
            self.last_setpoint = [float(v) for v in
                                  frames.frozen_to_px4(self.track.start,
                                                       self.home)]
        if self.generates:
            self.create_timer(1.0 / OBSERVATION_HZ, self.publish_observation)
        self.create_timer(1.0 / CONTROL_MODE_HZ, self.publish_control_mode)
        if self.work == SURVEY:
            self.get_logger().info(
                f"{self.vehicle_id} surveying strip {strip.index}, "
                f"x {strip.x_min:.3f} to {strip.x_max:.3f}, "
                f"{len(path)} waypoints, observations {self.generates}")
        elif self.work == TRACK:
            self.get_logger().info(
                f"{self.vehicle_id} flying a track, "
                f"{self.track.length_m:.1f} m at "
                f"{self.track.speed_mps:.1f} m/s from t="
                f"{self.track.start_s:.1f}s, observations {self.generates}")
        else:
            self.get_logger().info(
                f"{self.vehicle_id} holding station "
                f"{self.station[0]:.1f}, {self.station[1]:.1f}, "
                f"{self.station[2]:.1f} in the frozen frame, "
                f"observations {self.generates}")

    def _track(self):
        """The straight line this vehicle flies, or None if it has no track.

        A speed of zero is the answer for every vehicle in eight of the nine
        scenarios. It is checked rather than the endpoints, because a track
        from a point to itself and a track at no speed are both a vehicle
        commanded to fly nowhere and only one of them is easy to spot.
        """
        speed = float(self.get_parameter("track_speed_mps").value)
        if speed <= 0.0:
            return None
        start = tuple(float(v) for v
                      in self.get_parameter("track_start_enu").value)
        end = tuple(float(v) for v in self.get_parameter("track_end_enu").value)
        return Track(start=start, end=end,
                     start_s=float(self.get_parameter("track_start_s").value),
                     speed_mps=speed)

    def on_yield_hold(self, msg: Bool) -> None:
        """This vehicle's router saying whether it has to give way.

        The release happens here and the latch happens in `on_position`, which
        is the only place that knows where the aircraft is. Clearing the point
        on the message rather than on the next position report means a vehicle
        whose PX4 estimate has gone quiet is released anyway, instead of
        holding a point until a topic it does not control comes back.
        """
        self.holding = bool(msg.data)
        if not self.holding:
            self.hold_point = None

    def advance_track(self, now: float) -> None:
        """Move the commanded point along the line, unless the vehicle is held.

        The hold is the clock stopping and not a different setpoint. `t` is
        the scenario time this vehicle has been allowed to spend flying, so a
        held vehicle keeps the point it already had and then carries on from
        there. The leg after the hold is the same leg, later by exactly the
        length of the hold, which is why a vehicle that gives way still
        finishes its line.
        """
        if self.work != TRACK:
            return
        epoch = float(self.get_parameter("track_epoch_s").value)
        if epoch <= 0.0:
            return
        if self._track_last_s is None:
            self._track_last_s = now
        elapsed = max(0.0, now - self._track_last_s)
        self._track_last_s = now
        if self.holding:
            self.hold_seconds += elapsed
        self.track_time_s = now - epoch - self.hold_seconds
        if self.track_time_s >= self.track.arrival_s:
            self.track_complete = True
        if self.slot_target is not None:
            # A role manager has sent this vehicle somewhere. The clock still
            # runs, because the track is a schedule and not a queue, but the
            # setpoint belongs to whoever took the aircraft.
            return
        point = self.track.position_at(self.track_time_s)
        self.last_setpoint = [float(v) for v in
                              frames.frozen_to_px4(point, self.home)]

    def survey_time_s(self, now: float) -> Optional[float]:
        """Scenario seconds, or None while nobody has said where zero is.

        The runner sends the epoch after the ingress, in the same call it
        makes to a track vehicle and for the same reason: the scenario's clock
        starts when the last vehicle reaches the start of its own work, and
        nothing knows that at launch.
        """
        epoch = float(self.get_parameter("survey_epoch_s").value)
        return None if epoch <= 0.0 else now - epoch

    def advance_survey(self, now: float) -> None:
        """Walk the commanded point along the plan at the scenario's pace.

        Nothing happens for a vehicle whose scenario names one speed. It is
        handed the waypoint and PX4 flies there, which is what survey_baseline
        measured and what its coverage figure was taken off.

        A held vehicle does not advance. The pace is the plan's clock and
        giving way stops it, so the leg after a hold is the same leg later,
        which is the rule chunk 4.5 wrote for a track and the same rule here.
        """
        if self.work != SURVEY or self.pace_mps <= 0.0 or self.paced is None:
            return
        elapsed = 0.0 if self._paced_last_s is None else max(
            0.0, now - self._paced_last_s)
        self._paced_last_s = now
        if self.holding or self.slot_target is not None:
            return
        started = self.survey_time_s(now)
        if started is None or started < self.survey_start_s:
            # Holding the head of its first lane until the scenario says go.
            return
        target = self.mission.target()
        if target is None:
            return
        gap = math.dist(self.paced, target)
        step = self.pace_mps * elapsed
        if gap <= step or gap <= 0.0:
            self.paced = tuple(float(v) for v in target)
        else:
            share = step / gap
            self.paced = tuple(float(a + (b - a) * share)
                               for a, b in zip(self.paced, target))
        self.last_setpoint = [float(v) for v in
                              frames.frozen_to_px4(self.paced, self.home)]

    def stamp(self) -> int:
        """Microseconds, the way every PX4 message here is timestamped."""
        return self.get_clock().now().nanoseconds // 1000

    def publish_control_mode(self) -> None:
        """The offboard heartbeat, and the setpoint that goes with it.

        Unconditional. A vehicle that has finished its plan is still flying,
        and one whose position estimate has gone quiet for a moment is too, so
        neither may stop this. When the plan is done the last setpoint is
        republished, which holds the vehicle where it finished rather than
        handing PX4 nothing to fly to.
        """
        # The track advances here, on this node's own timer, and not in the
        # position callback. The callback depends on PX4 publishing; a gap in
        # that topic would stop a track vehicle mid leg and the record would
        # read it as a yield.
        moment = self.get_clock().now().nanoseconds / 1e9
        self.advance_track(moment)
        self.advance_survey(moment)

        mode = OffboardControlMode()
        mode.timestamp = self.stamp()
        mode.position = True
        self.control_mode.publish(mode)

        # A vehicle giving way is commanded to where it stopped, and the
        # plan underneath keeps its own idea of where it was going. So a
        # release needs nothing restored: the next heartbeat is already
        # flying the strip, the station or the slot again.
        point = self.last_setpoint
        if self.hold_point is not None:
            point = frames.frozen_to_px4(self.hold_point, self.home)
        if point is None:
            return
        setpoint = TrajectorySetpoint()
        setpoint.timestamp = mode.timestamp
        setpoint.position = [float(v) for v in point]
        self.setpoint.publish(setpoint)

    def on_position(self, msg: VehicleLocalPosition) -> None:
        """Advance the plan on this vehicle's own PX4 estimate.

        This decides where to go. Publishing is the timer's job, so a gap in
        this topic slows the plan down and never drops the vehicle out of
        offboard. A station-keeping vehicle has nowhere to advance to and
        only counts what arrived, so the subscription still proves the link
        is live.
        """
        self.positions_seen += 1
        here = frames.px4_to_frozen((msg.x, msg.y, msg.z), self.home)
        # Giving way is stopping, and this vehicle stops wherever it happens
        # to be rather than where its plan wanted it. Latched once: `holding`
        # stays true for the length of the hold and this runs on every
        # position report, so the second one through here must leave the first
        # one's point alone.
        if self.work != TRACK and self.holding and self.hold_point is None:
            self.hold_point = here
        if self.slot_target is not None:
            # Chunk 4.2. This vehicle has been sent somewhere by its own role
            # manager, so it is not flying its own work. Advancing the plan
            # here would fight the command every time a position arrived, and
            # the vehicle would sit between the two.
            return
        if self.work != SURVEY:
            # A station was decided before the vehicle reported anything and a
            # track advances on its own timer, so neither depends on where the
            # aircraft is now and neither has anything to advance here.
            return
        target = self.mission.update(here)
        if self.paced is None:
            # Where the ingress left it, which is the head of its first lane
            # and the only place a paced survey may start from.
            self.paced = here
        if target is None:
            return
        if self.pace_mps > 0.0:
            # The heartbeat walks the commanded point there. Placing it on the
            # waypoint here as well would put the vehicle back at PX4's limit
            # every time a position arrived.
            return
        self.last_setpoint = [float(v) for v in
                              frames.frozen_to_px4(target, self.home)]

    def on_role_slot(self, msg: RoleAssignment) -> None:
        """A point from this vehicle's role manager, or a withdrawal.

        Three NaNs mean "as you were": a surveyor goes back to its strip and a
        station-keeping vehicle to its station. Anything else is a place to be
        and it overrides both, because a vehicle holding the relay role is not
        doing its own work any more.
        """
        try:
            target = station.station_of(list(msg.slot))
        except station.StationError as exc:
            # One malformed command must not take the aircraft down, and a
            # half set point is exactly the kind that would otherwise fly it
            # somewhere with one coordinate left over from the last one.
            self.slot_errors += 1
            self.get_logger().error(f"unusable slot command: {exc}")
            return
        self.slot_messages += 1
        if target == self.slot_target:
            if self.slot_messages == 1:
                # The first one, whatever it says. Without this the node is
                # silent in exactly the case that is hardest to tell from a
                # topic nobody is publishing on, which is what chunk 4.2 spent
                # a 300 s run finding out.
                self.get_logger().info(
                    f"{self.vehicle_id} is hearing its role manager, which "
                    f"has asked for nothing so far")
            return

        self.slot_target = target
        self.slot_commands += 1
        if target is not None:
            self.last_setpoint = [float(v) for v in
                                  frames.frozen_to_px4(target, self.home)]
            self.get_logger().info(
                f"{self.vehicle_id} commanded to "
                f"{target[0]:.1f}, {target[1]:.1f}, {target[2]:.1f}")
            return

        # Withdrawn. A station-keeping vehicle has one place to go back to and
        # knows it; a surveyor's next waypoint is whatever its plan says, and
        # the next position report will produce it.
        if self.work == STATION:
            self.last_setpoint = [float(v) for v in
                                  frames.frozen_to_px4(self.station, self.home)]
        if self.work == SURVEY and self.pace_mps > 0.0:
            # The commanded point restarts from the aircraft rather than from
            # where the lane had got to before the role manager took it. The
            # survey index has not moved, so this is the same leg, resumed
            # from wherever the slot left the vehicle.
            self.paced = None
        # A track vehicle needs nothing here. advance_track stops deferring to
        # the slot on its next tick and puts the commanded point back on the
        # line, at the place the schedule says it should be by now.
        self.get_logger().info(
            f"{self.vehicle_id} released, back to its own work")

    def on_packet(self, msg: SwarmPacket) -> None:
        """Everything arriving from the radio, which in W2 is nothing.

        The subscription exists from the start because the seam is checked
        against a process manifest rather than against what a scenario
        happened to exercise, and because a mission executor that grows an
        rx endpoint later grows it under less scrutiny than this.
        """

    def publish_observation(self) -> None:
        """One observation, at the rate architecture.md freezes.

        Identity is `(origin_id, sequence)` and nothing else, which is what
        makes delivered-once a set comparison rather than a count.
        """
        packet = SwarmPacket()
        packet.origin_id = self.vehicle_id
        packet.sequence = self.sequence
        packet.kind = SwarmPacket.OBSERVATION
        packet.dest_id = "gcs"
        now = self.get_clock().now().nanoseconds / 1e9
        packet.created_at = now
        packet.expires_at = now + OBSERVATION_LIFETIME_S
        packet.hop_count = 0
        packet.path = [self.vehicle_id]
        packet.payload = [0] * OBSERVATION_BYTES
        self.tx.publish(packet)
        self.sequence += 1


def main(args=None) -> None:
    rclpy.init(args=args)
    node = MissionNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
