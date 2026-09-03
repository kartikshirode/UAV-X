"""The UAV-X mesh, pure logic.

None of the accepted simulators model radio. PX4, ArduPilot, Gazebo and AirSim
let every vehicle talk to every other vehicle at any distance, forever, so the
link model, the routing state machine and the relay election in this package
are the deliverable and not configuration.

Chunk ownership, from stage-1/plan.md:

    2.3  params.py     the frozen protocol parameters, one runtime home
    2.3  packet.py     the pure mirror of SwarmPacket and its identity
    2.3  link.py       whether an ordered pair can talk, and with what odds
    2.3  graph.py      the link-state database, the route key and Dijkstra
    2.3  routing.py    queues, hysteresis and the store-and-forward rules
    2.3  slots.py      where a relay parks, and when there is nowhere to park
    2.3  election.py   epochs, bids, leases, and giving the vehicle back
    2.3  router.py     one node's whole state machine, tx and rx only
    2.3  pure_net.py   the deterministic network the unit tests drive
    3.x  the ROS nodes that wrap all of the above

Nothing here imports rclpy, reads a clock, or names a topic or a vehicle id.
A Router holds no reference to another Router: the only ingress is on_rx and
the only egress is drain_tx, which is the tx/rx seam expressed in objects. W3
supplies the two endpoints and nothing else.
"""
