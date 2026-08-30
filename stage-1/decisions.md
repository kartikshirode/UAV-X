# Stage 1 decisions

Locked 26 August 2026, dates revised 29 August when the plan went from five weeks to four. The calls below did not change; only when they fall due did. Every item here was an open fork in [handoff.md](../handoff.md) or a finding in [round 1 of the review](../_plan-review-round1.md). They are closed. The point of writing them down is so week 3 doesn't reopen week 1.

## D1. Environment: WSL2 Ubuntu 22.04

Four pieces, pinned as a set:

| Piece | Version |
| --- | --- |
| Distro | Ubuntu 22.04 LTS under WSL2 |
| ROS 2 | Humble Hawksbill |
| PX4 | v1.15 |
| Simulator | Gazebo Classic (gazebo11) |
| ROS bridge | uXRCE-DDS agent, plus px4_msgs and px4_ros_com |

Why this set instead of the current one. Gazebo Classic is the best documented multi-vehicle path in PX4 and the one every swarm tutorial worth copying is built on. We do not use PX4's own `sitl_multiple_run.sh`: it ignores `HEADLESS` and ends in an unconditional `gzclient`, which takes the WSL distro down. `scripts/sitl_multi.sh` is the launcher, and it also sets the per-vehicle `PX4_UXRCE_DDS_NS` that the PX4 script never sets. Humble is LTS and has the largest example base behind it. Classic's GUI is far lighter than Harmonic's under WSLg, and WSLg is where week 1 usually dies.

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

Revised 30 August, because the arithmetic under it stopped being true. It read 32 days at 5 hours, about 160 hours, and it was written when the plan started on 26 August with a five week shape. Nothing was built in those first four days.

28 days at 5 hours is about 140 hours, and round 7 finding 9 is right that carrying the old figure hid roughly 20 of them. The four weeks are 30 hours each, leaving about 20 for the reruns that every week has had.

Two consequences, both unchanged. Nothing in the schedule can assume two things happening at once on the same machine, since multi-vehicle SITL eats the CPU while it runs. And there is no packaging week, so each week drafts its own proposal section, cuts its own footage and amends `INSTALL.md` as its work lands.

W4 is where this bites. It holds eight chunks, five full simulations and the freeze, install and send sequence in seven days, and it is the week with no later week to recover from. So the two send days are reserved first and are not available to the build. If the build half runs past 24 September, the order of sacrifice is fixed here rather than decided under pressure:

| Give up | Costs | Keep |
| --- | --- | --- |
| `4.6`, the no-yield control | part of the 10% safety row; the monitor still runs in `4.5` | everything else |
| `4.5` and `4.6` together | the 10% safety row | the 60% that lives in comms, roles and recovery |
| `4.4`, the queue drain case | part of the 15% fault recovery row; `4.2` and `4.3` still show recovery | the integrated run |

`4.7`, the integrated mission, is never given up. It is the working proof-of-concept simulation the organisers ask for by name, and without it there is no submission to send.

## D4. Four vehicles

One node anchored near the GCS, one relay, two out surveying. Smallest set that shows a real multi-hop chain and still has something to lose when the relay dies. Six looks better in a screenshot and costs debugging time the schedule hasn't got.

## Decision gates

One gate per week. The pass condition is `bash scripts/gate.sh <N>` exiting 0, and that script is the only place any threshold is written down. Round 2 finding 2: these were spelled out here, in the plan and in the loop config, and the three had already drifted, with the W3 control in two of them failing a correct implementation. So this table says what each gate is for and what to do when it fails, and never what number it asserts.

| Week | Date | Gate | What it proves | If it fails |
| --- | --- | --- | --- | --- |
| W1 | 5 Sep | `gate.sh 1` | Four vehicles fly a commanded takeoff and land, and the run harness writes a valid run record | The simulator already runs 4 vehicles headless, so a failure here is our code, not the stack. Fix it; do not drop to 2 vehicles. Two vehicles cannot hold an anchor, a relay and a spare, so W3 and W4 could not run at all. |
| W2 | 12 Sep | `gate.sh 2` | The swarm covers the frozen survey box, measured from actual poses | Fix it or halt. Not the box, which is frozen, and not the vehicle count: D4 fixes it at 4, and 3 or fewer cannot hold an anchor, a relay and two survey drones, so W3 and W4 could not run at all. |
| W3 | 19 Sep | `gate.sh 3` | `uav_4` reaches the GCS only by relay, and cannot reach it at all with forwarding off | Hard stop, because this is the submission. W4 gives up days to it, never the reverse. |
| W4 | 26 Sep | `gate.sh 4` | The relay dies, the swarm elects a replacement, repositions it and rebuilds the chain, with no separation violations | Run `link_loss` with the release rule disabled and claim the routing recovery only, which is still the named failure demonstrated. Say so in the proposal rather than implying more. Round 7 finding 10: this row and the plan named two different fallbacks, supporting two different claims, and an unattended tick could have taken either. check_docs.py now holds all three documents to this sentence. |
| `4.8` | 26 Sep | `gate.sh 4.8` | The package is real, and a human has recorded the send | It exits 2 while the package is complete but unsent, which is the normal state before a human sends it. Send on 26 Sep regardless; the deadline is the 27th and email gives no upload confirmation. |

The W3 control run is the one worth defending. A delivery ratio of 1.0 proves nothing by itself, because that is also what a silently open gate produces. The pair of runs together is the evidence.

Every threshold behind these gates is derived in [architecture.md](architecture.md), and `scripts/check_geometry.py` re-derives the geometry and the reconnect budget so they cannot rot in prose.

## What is not a decision

Two things that keep getting written down here and do not belong in a decisions file, because both are state and state rots in prose.

The human dependencies, meaning registration, eligibility, the clarification channel, the organiser questions and the delivery method, are in [human-preflight.md](human-preflight.md) and gate.sh preflight blocks on them. They are not backlog.

Review state, meaning which round has run and what it found, is in `.claude/review-status.json`. `scripts/check_docs.py` fails any document that states it in prose, this one included.

`gh` is still not installed on this machine. Nothing in Stage 1 needs it: the submission goes by email and there is no portal, so a GitHub repository is a convenience for the source-delivery answer and not a dependency.
