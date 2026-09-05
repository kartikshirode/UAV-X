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

import rclpy
from px4_msgs.msg import OffboardControlMode, TrajectorySetpoint, VehicleLocalPosition
from rclpy.node import Node
from rclpy.qos import (QoSDurabilityPolicy, QoSHistoryPolicy, QoSProfile,
                       QoSReliabilityPolicy)
from uavx_msgs.msg import SwarmPacket

from uavx_mission import frames, station
from uavx_mission.boustrophedon import plan_path
from uavx_mission.executor import MissionExecutor
from uavx_mission.partition import partition, strip_of
from uavx_mission.survey_area import (BASELINE_CELL_M, BASELINE_SENSOR_RADIUS_M,
                                      BASELINE_SIDE_M, BASELINE_SW_CORNER_M,
                                      SurveyArea)

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

        sw = [float(v) for v in self.get_parameter("area_sw_m").value]
        area = SurveyArea.from_corner(
            (sw[0], sw[1]),
            float(self.get_parameter("area_width_m").value),
            float(self.get_parameter("area_height_m").value),
            float(self.get_parameter("cell_m").value),
            float(self.get_parameter("sensor_radius_m").value))
        if self.station is None:
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
        if self.station is None:
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

        self.sequence = 0
        self.positions_seen = 0
        # The last setpoint sent, so the heartbeat timer can keep holding it
        # after the plan is finished. None until the first position arrives,
        # except in station mode, where the destination is known before the
        # vehicle has said anything and the heartbeat can start flying it
        # there the moment PX4 grants offboard.
        self.last_setpoint = None
        if self.station is not None:
            self.last_setpoint = [float(v) for v in
                                  frames.frozen_to_px4(self.station, self.home)]
        if self.generates:
            self.create_timer(1.0 / OBSERVATION_HZ, self.publish_observation)
        self.create_timer(1.0 / CONTROL_MODE_HZ, self.publish_control_mode)
        if self.station is None:
            self.get_logger().info(
                f"{self.vehicle_id} surveying strip {strip.index}, "
                f"x {strip.x_min:.3f} to {strip.x_max:.3f}, "
                f"{len(path)} waypoints, observations {self.generates}")
        else:
            self.get_logger().info(
                f"{self.vehicle_id} holding station "
                f"{self.station[0]:.1f}, {self.station[1]:.1f}, "
                f"{self.station[2]:.1f} in the frozen frame, "
                f"observations {self.generates}")

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
        mode = OffboardControlMode()
        mode.timestamp = self.stamp()
        mode.position = True
        self.control_mode.publish(mode)

        if self.last_setpoint is None:
            return
        setpoint = TrajectorySetpoint()
        setpoint.timestamp = mode.timestamp
        setpoint.position = list(self.last_setpoint)
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
        if self.mission is None:
            # Station-keeping. The setpoint was decided before the vehicle
            # reported anything and does not depend on where it is now, so
            # there is nothing here to advance.
            return
        here = frames.px4_to_frozen((msg.x, msg.y, msg.z), self.home)
        target = self.mission.update(here)
        if target is None:
            return
        self.last_setpoint = [float(v) for v in
                              frames.frozen_to_px4(target, self.home)]

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
