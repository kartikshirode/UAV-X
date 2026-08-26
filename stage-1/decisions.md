# Stage 1 decisions

Locked 26 August 2026. Every item here was an open fork in [handoff.md](../handoff.md) or a finding in [round 1 of the review](../_plan-review-round1.md). They are closed. The point of writing them down is so week 3 doesn't reopen week 1.

## D1. Environment: WSL2 Ubuntu 22.04

Four pieces, pinned as a set:

| Piece | Version |
| --- | --- |
| Distro | Ubuntu 22.04 LTS under WSL2 |
| ROS 2 | Humble Hawksbill |
| PX4 | v1.15 |
| Simulator | Gazebo Classic (gazebo11) |
| ROS bridge | uXRCE-DDS agent, plus px4_msgs and px4_ros_com |

Why this set instead of the current one. Multi-vehicle on Gazebo Classic runs through `sitl_multiple_run.sh`, which is the best documented path in PX4 and the one every swarm tutorial worth copying is built on. Humble is LTS and has the largest example base behind it. Classic's GUI is far lighter than Harmonic's under WSLg, and WSLg is where week 1 usually dies.

The cost is real and I'd rather state it than hide it. Gazebo Classic went end of life in January 2025. For a submission due September 2026 that doesn't matter, and this is a Stage 1 and 2 decision rather than a forever one. If the swarm needs a live simulator past this programme, that's a port, and porting off something that works beats rewriting something that doesn't.

Docker lost because the GUI problem survives the move into a container. Ubuntu 24.04 with Jazzy and Harmonic lost because multi-vehicle there means `PX4_GZ_STANDALONE=1` per instance with hand-managed poses and system IDs, on much thinner documentation.

## D2. The comms layer gates at the ROS 2 application layer

This closes finding 4, the one round 1 called the highest-value unresolved decision.

A ROS 2 node sits between the swarm's own messages and their delivery. It tracks the current position of every vehicle, and decides each message from the distance between sender and receiver: pass inside the range threshold, drop outside it, drop with rising probability through a band near the edge.

What it buys. Full control over the model, an explanation that fits in half a page of the proposal, deterministic replay for testing and failure injection that's a function call rather than a network trick.

What it costs, and the proposal has to say this in plain words: MAVLink between PX4 instances still flows freely underneath. The radio model lives at the application layer, so it constrains the swarm's coordination traffic and not the simulator's plumbing. Claiming anything stronger is the sort of thing an evaluator catches by opening the source.

One design constraint comes with the choice. The gate goes behind an interface with a single job, deciding whether a given message gets from A to B right now. A UDP shaper answering the same question is a Stage 2 job, and leaving that seam in place costs nothing today.

## D3. Capacity: one person, 4 to 6 hours a day

Finding 5 said this was the input most likely to invalidate everything else. Answer: solo, roughly 5 hours daily, with parallel work split across separate working sessions rather than across people.

32 days at 5 hours is about 160 hours. That's enough for the plan, with two consequences. Nothing in the schedule can assume two things happening at once on the same machine, since multi-vehicle SITL will eat the CPU while it runs. And the 5 days budgeted for proposal and video at the end are 5 solo days, so drafting has to start before the simulation is finished.

## D4. Four vehicles

One node anchored near the GCS, one relay, two out surveying. Smallest set that shows a real multi-hop chain and still has something to lose when the relay dies. Six looks better in a screenshot and costs debugging time the schedule hasn't got.

## Decision gates

One gate per week. The pass condition is `bash scripts/gate.sh <N>` exiting 0, and that script is the only place any threshold is written down. Round 2 finding 2: these were spelled out here, in the plan and in the loop config, and the three had already drifted, with the W3 control in two of them failing a correct implementation. So this table says what each gate is for and what to do when it fails, and never what number it asserts.

| Week | Date | Gate | What it proves | If it fails |
| --- | --- | --- | --- | --- |
| W1 | 1 Sep | `gate.sh 1` | Four vehicles fly a commanded takeoff and land, and the run harness writes a valid run record | The simulator already runs 4 vehicles headless, so a failure here is our code, not the stack. Fix it; do not drop to 2 vehicles. Two vehicles cannot hold an anchor, a relay and a spare, so W3 and W4 could not run at all. |
| W2 | 8 Sep | `gate.sh 2` | The swarm covers the frozen survey box, measured from actual poses | Cut run time or vehicle count, never the box. Shrinking the area until the number passes is moving the goal. |
| W3 | 15 Sep | `gate.sh 3` | `uav_4` reaches the GCS only by relay, and cannot reach it at all with forwarding off | Hard stop, because this is the submission. W4 gives up days to it, never the reverse. |
| W4 | 22 Sep | `gate.sh 4` | The relay dies, the swarm elects a replacement, repositions it and rebuilds the chain, with no separation violations | Fall back to a deterministic priority list fixed at startup instead of a live election. It still recovers and still demos; the proposal describes what it is. |
| W5 | 26 Sep | `gate.sh 5` | The package is real, and a human has recorded the send | `gate.sh 5` exits 2 while the package is complete but unsent, which is the normal state before a human sends it. Send on 26 Sep regardless; the deadline is the 27th and email gives no upload confirmation. |

The W3 control run is the one worth defending. A delivery ratio of 1.0 proves nothing by itself, because that is also what a silently open gate produces. The pair of runs together is the evidence.

Every threshold behind these gates is derived in [architecture.md](architecture.md), and `scripts/check_geometry.py` re-derives the geometry and the reconnect budget so they cannot rot in prose.

## Still open

Not decisions, just things nobody has done yet.

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- Send the organiser email, including the question about whether a detailed problem statement exists for UAV-X
- `gh` is not installed on this machine, which blocks the GitHub repo step (finding 8)
- Round 2 of the plan cross-check, by Codex
