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

Round 1 finding 6: no gates anywhere, no stated fallback. Here they are, one per `/loop` week. Each is a date, a pass condition and what happens if it is missed. The pass conditions are commands, written out in full in [plan.md](plan.md); a week is done when they exit 0.

| Week | Date | Gate | Passes when | If it fails |
| --- | --- | --- | --- | --- |
| W1 | 1 Sep | Environment | `verify.sh` and `run_smoke.sh --vehicles 4` both exit 0 | Drop to 2 vehicles and carry on unchanged. If WSLg is the blocker rather than PX4, stay headless and book one GUI run in W5 for the video. Do not spend W2 here. |
| W2 | 8 Sep | Survey mission | `coverage_fraction >= 0.95` on the baseline scenario | Shrink the area, keep the metric. A small box covered fully beats a big one covered partly, because the number is what gets reported. |
| W3 | 15 Sep | Comms layer | `delivery_ratio >= 0.95` and `mean_hop_count > 1.0` on the relay scenario, and `delivery_ratio < 0.5` on the direct-only control | Hard stop, because this is the submission. W4 gives up days to it, never the reverse. |
| W4 | 22 Sep | Fault recovery and safety | `time_to_reconnect_s <= 30`, `role_changes >= 1`, `separation_violations == 0` after a relay kill | Fall back to a deterministic priority list computed at startup instead of live election. It still recovers and still demos; the proposal describes what it is. |
| W5 | 26 Sep | Submit | `check_submission.py` exits 0, email sent | Send on 26 Sep regardless, with whatever exists. Deadline is 27 Sep and email gives no upload confirmation. |

The control run in W3 is the one worth defending. A delivery ratio of 1.0 on its own proves nothing, because that is also what an accidentally open gate produces. The pair of runs together is the evidence.

## Still open

Not decisions, just things nobody has done yet.

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- Send the organiser email, including the question about whether a detailed problem statement exists for UAV-X
- `gh` is not installed on this machine, which blocks the GitHub repo step (finding 8)
- Round 2 of the plan cross-check, by Codex
