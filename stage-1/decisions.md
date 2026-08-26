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

Round 1 finding 6: no gates anywhere, no stated fallback. Here they are. Each one is a date, a pass condition and what happens if it isn't met.

| Date | Gate | Passes when | If it fails |
| --- | --- | --- | --- |
| 31 Aug | Environment | 4 PX4 SITL instances airborne together in Gazebo Classic, `ros2 topic list` showing a namespace per vehicle | Drop to 2 vehicles and carry on. If WSLg or the GPU is the blocker rather than PX4, run headless with `HEADLESS=1` and keep one scripted GUI run at the end for the video. Do not spend day 7 here. |
| 6 Sep | Survey mission | All 4 fly a waypoint coverage pattern, telemetry from all of them arriving at one node | Cut the pattern to a straight lawnmower over a smaller box. Mission completion is 25% and the comms work is 60%. |
| 13 Sep | Comms layer | Range gate delivering, GCS reachable from the far drone only through a relay hop | Hard stop, because this is the submission. If it isn't working by 13 Sep, reduce fault recovery to a single scripted failure and give those days back to comms. |
| 19 Sep | Fault recovery | Relay killed mid-flight, role reassigned, chain rebuilt, nobody touching anything | Fall back to a kill at a known time with hand-tuned reassignment. It still reads on video. |
| 24 Sep | Proposal and video | 6 to 8 pages written, demo recorded | Video first and proposal second. Without footage there's nothing for an evaluator to look at. |
| 26 Sep | Submit | Fresh-machine install test passes, email sent | Send on 26 Sep regardless, with whatever exists. Deadline is 27 Sep and email gives no upload confirmation. |

## Still open

Not decisions, just things nobody has done yet.

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- Send the organiser email, including the question about whether a detailed problem statement exists for UAV-X
- `gh` is not installed on this machine, which blocks the GitHub repo step (finding 8)
- Round 2 of the plan cross-check, by Codex
