"""The mission executor as a ROS node, and the only file here that knows ROS.

Everything this node decides is decided in `executor`, `boustrophedon`,
`partition` and `frames`, all of which are plain arithmetic over tuples and
are proved by chunk 2.1's tests with no simulator running. What is left here
is wiring, and wiring is the part a unit test cannot honestly cover, so there
is as little of it as the job allows.

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

from uavx_mission import frames
from uavx_mission.boustrophedon import plan_path
from uavx_mission.executor import MissionExecutor
from uavx_mission.partition import partition, strip_of
from uavx_mission.survey_area import (BASELINE_CELL_M, BASELINE_SENSOR_RADIUS_M,
                                      BASELINE_SIDE_M, BASELINE_SW_CORNER_M,
                                      SurveyArea)

# architecture.md section 6: observation packets, 5 Hz per surveying vehicle.
OBSERVATION_HZ = 5.0

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

        vehicle_id = self.get_parameter("vehicle_id").value
        if not vehicle_id:
            raise ValueError(
                "vehicle_id is required. A mission executor that does not know "
                "which vehicle it is cannot build its own endpoints, and every "
                "topic it holds has to be its own.")
        self.vehicle_id = str(vehicle_id)
        self.home = tuple(float(v) for v in self.get_parameter("home_enu").value)

        sw = [float(v) for v in self.get_parameter("area_sw_m").value]
        area = SurveyArea.from_corner(
            (sw[0], sw[1]),
            float(self.get_parameter("area_width_m").value),
            float(self.get_parameter("area_height_m").value),
            float(self.get_parameter("cell_m").value),
            float(self.get_parameter("sensor_radius_m").value))
        strips = partition(area, list(self.get_parameter("swarm_vehicles").value))
        strip = strip_of(strips, self.vehicle_id)
        path = plan_path(
            strip,
            area.sensor_radius_m,
            float(self.get_parameter("survey_altitude_m").value),
            start_north=bool(self.get_parameter("start_north").value),
        )
        # Not self.executor. rclpy.node.Node defines `executor` as a
        # property with a setter, and assigning to it calls
        # new_executor.add_node(self) on whatever was assigned. Every
        # mission executor died in this line with AttributeError:
        # 'MissionExecutor' object has no attribute 'add_node', on the
        # first survey run, which was the first time this node had ever
        # been started. `executor` and `handle` are the only two settable
        # properties Node has, and test_node_attributes.py now refuses
        # either name on any node in this workspace.
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
        self.create_timer(1.0 / OBSERVATION_HZ, self.publish_observation)
        self.get_logger().info(
            f"{self.vehicle_id} surveying strip {strip.index}, "
            f"x {strip.x_min:.3f} to {strip.x_max:.3f}, "
            f"{len(path)} waypoints")

    def on_position(self, msg: VehicleLocalPosition) -> None:
        """Advance the plan on this vehicle's own PX4 estimate."""
        here = frames.px4_to_frozen((msg.x, msg.y, msg.z), self.home)
        target = self.mission.update(here)
        if target is None:
            return
        stamp = self.get_clock().now().nanoseconds // 1000

        mode = OffboardControlMode()
        mode.timestamp = stamp
        mode.position = True
        self.control_mode.publish(mode)

        setpoint = TrajectorySetpoint()
        setpoint.timestamp = stamp
        setpoint.position = [float(v) for v in frames.frozen_to_px4(target, self.home)]
        self.setpoint.publish(setpoint)

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
