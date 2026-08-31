"""The UAV-X simulation harness.

Chunk ownership, from stage-1/plan.md week 1:

    1.3  scenario.py          the loader and its rejections
    1.4  event_injector.py    injection, and observing that it landed
    1.5  graph_snapshot.py    the ROS graph the seam checker reads
    1.6  resource_sampler.py  peak RSS and swap across the process group
    1.7  run_record.py        the record writer and its atomic publish
    1.7  scenario_runner.py   the complete harness

Nothing in this package may carry swarm traffic. The tx and rx endpoints
belong to uavx_comms, and reading simulator ground truth belongs to the
link layer and uavx_eval. scripts/check_seam.sh enforces both.
"""
