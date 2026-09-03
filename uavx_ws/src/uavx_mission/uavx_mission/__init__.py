"""The UAV-X survey mission.

Four modules, and only one of them knows what ROS is:

    survey_area   the frozen box and the grid coverage is scored on
    partition     the box split into one strip per vehicle
    boustrophedon the lawnmower path that covers one strip
    executor      the state that flies a plan and hands it over
    frames        the frozen ENU frame against PX4's local NED frame
    mission_node  the rclpy node, and the only file that imports rclpy

Chunk 2.1 asks whether the planner and the partitioner hold up with no
simulator involved, so everything above the node is plain arithmetic over
tuples. Nothing here reads ground truth and nothing here names a vehicle that
is not its own; see the endpoint allowlist in architecture.md section 1.
"""
