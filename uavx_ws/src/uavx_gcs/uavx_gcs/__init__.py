"""The UAV-X ground station.

One node, `gcs_node`. architecture.md section 1 gives it `/uavx/gcs/tx` and
`/uavx/gcs/rx` and nothing else, so it reaches the swarm only through the
radio, exactly like a vehicle. It is the destination every observation is
addressed to and the only place deduplication happens.
"""
