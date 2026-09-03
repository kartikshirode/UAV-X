"""The UAV-X observer.

Chunk ownership, from stage-1/plan.md week 2:

    2.2  check.py              the provenance gate every run record passes
    2.2  collector.py          what the collector measures, with no ROS in it
    2.2  coverage.py           coverage from sampled poses, never a plan
    2.2  separation.py         the pairwise separation monitor
    2.2  metrics_collector.py  the node that wires those to the graph

Two jobs and one reason they live together. Both of them read what actually
happened rather than what something intended: the collector reads simulator
ground truth, and `check.py` reads the tree, the scenario file and git.

stage-1/architecture.md section 1 puts this package and the link layer outside
the swarm. They stand for physics and for the observer, which is why they may
read ground truth and why nothing else may. Nothing here publishes or
subscribes to a tx or rx endpoint, and nothing here carries a SwarmPacket.
"""
