# UAV-X handoff

Written 26 August 2026, rewritten 29 August. Stage 1 is due **27 September 2026**, so there are 29 days. This file is where a fresh session starts; everything else links from here.

## What this is

PUSHPAK Grand Challenge 2026, Grand Challenge on resilient BVLOS drone swarms. Build an autonomous UAV swarm that surveys a disaster zone, holds a multi-hop link back to a ground control station beyond visual line of sight, and reconfigures itself as drones fail or lose connectivity. Simulation only, no physical drones anywhere in the challenge.

Funded by MeitY under the national drone mission, hosted by IIT Bombay, presented by IISERB with IISER Bhopal and VJTI Mumbai. Prize ceiling is INR 25,50,000 across the whole staged programme.

Read [context.md](context.md) first. It holds the official competition record pulled straight off the Techfest API, and it outranks every other file here, including this one. Then [stage-1/plan.md](stage-1/plan.md) for the five week build plan, [stage-1/decisions.md](stage-1/decisions.md) for the locked calls and the gate per week, and [stage-1/setup/README.md](stage-1/setup/README.md) for installing the stack.

`brief.md` is the older, looser summary and is superseded by context.md wherever the two disagree. Shared track rules are in [_shared-timeline.md](_shared-timeline.md), compute notes in [_compute.md](_compute.md), the first plan review in [_plan-review-round1.md](_plan-review-round1.md), and the reviewer prompt in [_codex-review-prompt.md](_codex-review-prompt.md).

Repo: github.com/kartikshirode/UAV-X. This project is its own repository and does not share a session with CycloProp.

## Where we are

Day 11, 5 September. Weeks 1, 2 and 3 are accepted: `bash scripts/gate.sh 1`, `2` and `3` have each exited 0, which is 19 of the 25 chunks. Week 4 is the whole of what is left.

The swarm surveys, and it relays. `survey_baseline` covered all 400 cells of the frozen box off flown poses. `relay_required` delivers 99.7% of everything four station-keeping vehicles generate over a mesh where the far one has no link to the ground station at all, and `direct_only`, the same file with forwarding off, delivers exactly 0 from that vehicle. What landed, what broke and what is still open is in [docs/progress/week-1.md](docs/progress/week-1.md), [week-2.md](docs/progress/week-2.md) and [week-3.md](docs/progress/week-3.md).

Registration came through on 1 September and the id is `UAVX-8C4749FDD12C`. The preflight schema requires it now, because the other two fields in that block are a date and an address and a person can type both without ever having registered.

The plan is four weeks starting 30 August, rebuilt after the original week 1 went on reviews. See [stage-1/plan.md](stage-1/plan.md), Why four weeks and not five.

**Decided and locked** in [stage-1/decisions.md](stage-1/decisions.md): WSL2 Ubuntu 22.04 with ROS 2 Humble, PX4 v1.15 and Gazebo Classic; the comms layer gates at the ROS 2 application layer; solo entry at 4 to 6 hours a day; 4 vehicles.

**Environment is up and verified.** `bash stage-1/setup/verify.sh` passes every check and exits 0:

| Piece | State |
| --- | --- |
| Distro | Ubuntu 22.04.5 LTS under WSL2, user `kartik`, passwordless sudo |
| ROS 2 | Humble desktop, `ros2` and `colcon` on the path |
| Simulator | Gazebo Classic 11.10.2 from jammy universe, `gzserver` and `gzclient` present, no Gazebo Garden |
| PX4 | v1.15.4, SITL binary built. Launched by `scripts/sitl_multi.sh`, never by PX4's `sitl_multiple_run.sh` |
| Bridge | `MicroXRCEAgent` v2.4.3 installed, `ws_uavx` built, `px4_msgs` on the path |
| Display | `DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`, so WSLg works |

It took seven failed runs. Four of them presented as something other than their cause and two passed a green check while broken, so the failure notes in [stage-1/setup/README.md](stage-1/setup/README.md) are worth reading before touching any version pin.

That table was written before anything flew. Four vehicles now arm, climb to separate layer altitudes, hold and land, headless, in one command.

**The plan is built for the loop and the harness exists.** Four weeks and 25 chunks. A week ends in `bash scripts/gate.sh <N>`; a chunk is `bash scripts/gate.sh <N>.<M>` and is the unit you actually work in. That script is the only definition of any threshold; the plan, decisions and loop config describe it and never restate it. The frozen design is [stage-1/architecture.md](stage-1/architecture.md).

**Reviews.** The plan goes through Codex, gets fixed, and goes back. Which round has run, what it found and what is left is in `.claude/review-status.json`, and nowhere else. `scripts/check_docs.py` fails any document that writes that state into prose, because four documents were carrying four different versions of it and the loop hands those files to a week-agent that cannot tell which sentence is current.

Every round so far has come back NOT READY and has been right. Round 2's four spot-checked findings were all real. Round 3 found the frozen topology made the relay kill a no-op. Round 4 found the same class of thing one level up, and both of those are worth reading before trusting anything here.

## Stage 1 deliverables, all in one email

To `pushpak_gc2026@aero.iitb.ac.in`. There is no portal anywhere in this track, so the email is the submission and there is no upload confirmation.

1. Technical proposal, 6 to 8 pages, covering software architecture
2. A working proof of concept simulation
3. Source code
4. Installation instructions
5. Demonstration video

Plan sends on 26 September, a day early.

## The one thing to understand before writing any code

Look at where the marks are:

| Criterion | Weight |
| --- | --- |
| Mission completion | 25% |
| Communication resilience | 25% |
| Autonomous relay and role management | 20% |
| Fault recovery and swarm reconfiguration | 15% |
| Safety and collision avoidance | 10% |
| Innovation and technical merit | 5% |

Communication resilience, relay management and fault recovery total **60%**.

Now the part that decides the project: **PX4 and Gazebo model no radio at all.** No range limit, no packet loss, no link budget. Every drone talks to everything, always, at any distance. The mesh layer is not a setting you turn on. It is the thing you build, and it is 60% of the score.

Flight is 25% and the tooling hands it to you. Budget one week on flight and two on the comms layer.

## What's not done, in order of risk

1. **The next round of the plan review.** Each round's fixes get read only by their author until Codex sees them, which is the position that produced 11 findings the first time and 9 the last. Prompt is in [_codex-review-prompt.md](_codex-review-prompt.md); current position in `.claude/review-status.json`.
2. **Nothing has failed in a run yet.** The survey flies and the mesh delivers, both in steady state. Every scenario that kills a vehicle, gates a radio or moves a relay is week 4, and the routing and election logic behind them is unit tested against the state machines and has never been driven by a simulator.
3. **The demo needs a shot, not a camera.** The capture path exists and is proven: gzserver renders a camera sensor headless and the recording rehearsal produces a 60 s clip with the run id burned into every frame and its sha256 in a receipt. What it produces is not yet a demo, because one static camera cannot make a 480 m formation of half metre vehicles look like anything and `relay_required` has nothing moving in it. The shot belongs to `mission_integrated`.
4. **The organiser email.** Drafted in [stage-1/organiser-email.md](stage-1/organiser-email.md), not sent.

Closed on 26 August: whether Gazebo runs here at all, and whether four vehicles fit. `gzserver` starts headless and leaves the distro alone; only the GUI binary is dangerous. `scripts/sitl_multi.sh` brings up 4 PX4 instances with a ROS namespace each and 43 topics apiece, and the whole stack costs under 800 MB of the 11.5 GB available.

## Running it

The environment is up and verified, and the state table above is the current one. This section is how to drive it.

Settled: WSL2 Ubuntu 22.04 native, not Docker, because a container boundary hides the GUI fight rather than ending it. The matched set is Ubuntu 22.04, ROS 2 Humble, PX4 v1.15.4 and Gazebo Classic 11.10.2, pinned on purpose. Picking the latest of each independently is the most common way week 1 goes wrong.

Run `bash stage-1/setup/setup-all.sh` inside the Ubuntu shell, then `bash stage-1/setup/verify.sh` in a fresh one. Steps are reentrant, so a failed run resumes at the step that broke.

Three things learned the hard way on the first runs, all handled in the scripts now:

- **Never run anything long off `/mnt/c`.** The 9P bridge to Windows drops and takes the run with it, reporting `Operation canceled @p9io.cpp`. Copy the scripts into the Linux filesystem and run from there. Everything the build touches already lives under `$HOME`.
- **Keep a client attached.** WSL terminates the distro when nothing is connected, and `setsid nohup` does not rescue a detached build from that.
- **Leave Docker Desktop alone mid-build.** Starting it reconfigures the WSL NAT and DNS drops for a few seconds. Stopping it runs `wsl --shutdown`, which kills every distro including the one building.

## Compute

Baramati HPC is available in principle. Full notes in [_compute.md](_compute.md). Short version for this project:

**Do not plan Stage 1 around it.** It is off-LAN right now and unreachable. `srun` is broken there so there are no interactive jobs, Gazebo development wants a display, and multi-vehicle SITL is CPU bound, which the laptop already does well with a screen attached.

Where it earns its place is the scenario sweep after the simulation works: swarm sizes, failure timings, range thresholds, topologies, all as one Slurm array job. The IDS project at `MagaMinds/June/Adversarial IDS/tools/hpc/` has a working sweep-and-collect pattern to adapt rather than rewrite.

## The human steps, and none of them is done by code

`bash scripts/gate.sh 1` cannot start until `submission/human-preflight.json` exists, so this list is the thing standing between week 1 and acceptance. Registration is the only one closed.

- ~~Register on techfest.org under Competitions, then PUSHPAK Grand Challenge.~~ Done 1 September, id `UAVX-8C4749FDD12C`.
- **Declare no attachment to the PUSHPAK project, the Drone Centre, or the organising or host institutions.** This disqualifies an entry at any stage, so the declaration is dated and signed rather than assumed.
- Send the organiser email, drafted in [stage-1/organiser-email.md](stage-1/organiser-email.md).
- Join the WhatsApp clarification channel, and record when it was last read.
- Record the delivery route and the attachment budget in MB.
- Get the BVLOS regulatory position reviewed by a person, and record who and when. Stage 1 and Stage 2 fly nothing, so the statement is that no operational permission attaches and the obligations before physical flight sit in the proposal. A checker can confirm the citations resolve. Only a person can sign that the reading is right.

Team size is settled: solo entry.

## Starting points worth reading before writing from scratch

- **UAVros**, ROS1 and ROS2 kit for PX4 multi-rotor and UGV swarm simulation
- **Aerostack2**, ROS2 framework for multi-robot aerial systems, plugin architecture
- Multi-vehicle launching is solved and lives in `scripts/sitl_multi.sh`. Do not use PX4's `sitl_multiple_run.sh`: it ignores `HEADLESS`, ends in an unconditional `gzclient` that crashes this distro, and never sets the per-vehicle `PX4_UXRCE_DDS_NS` that gives each vehicle its own ROS namespace.
- MAVLink `MAV_SYS_ID` caps at 255 vehicles, so the protocol is nowhere near a limit here. The count is 4, fixed by D4 and by every coordinate in the frozen geometry. Adding a fifth is not a knob, it is a redesign.

## Things that bit earlier cluster projects, so they do not bite this one

Carried from the Vaani and Adversarial IDS handoffs, same servers:

- Windows ssh strips quotes. Never send a python one-liner or heredoc through `ssh`. Write the file, `scp` it, run it. Same for PowerShell inline python.
- Git on Windows turned slurm scripts CRLF once and sbatch refused them. Force LF in `.gitattributes` and run `sed -i "s/\r$//"` after any sync anyway.
- uv venvs on the cluster have no pip. Use `.venv/bin/python` directly.
- Assume compute nodes have no outbound internet until proven otherwise.

## Honest gaps

- Nothing has failed in a run. Four vehicles survey, four station-keep and relay, and no scenario has yet killed a vehicle or gated a radio.
- `check_submission.py` has still only run against an empty package and against its own fixtures, never against a real one. The seam checker has 51 fixtures and has now seen five real ROS graphs; the first failed it on three endpoints and none of the three was a bypass, which is the argument for capturing more of them.
- Bring-up is flaky under load. Two rehearsal runs died at it on 5 September, once with a boost transport assertion inside gzserver and once with two of four namespaces missing from DDS discovery. The second is fixed by waiting for discovery rather than sampling it once. The first happened once in about thirty bring-ups and has not been chased.
- The same mistake has now been caught twice: a checker that only examines what its author thought to list, confirming what its author already believed. First the frozen topology, where the relay kill turned out to be a no-op. Then the integrated mission, where the survey box sat 240.8 m from the anchor while the design claimed 250. `check_geometry.py` now walks every pair at every sampled instant of every trajectory. Assume that class of mistake is hiding somewhere else too, and that the next place it turns up will also look thoroughly checked.
- Three checkers found bugs in themselves while being written, which is the argument for writing the fixtures. The seam suite exposed a `grep` under `pipefail` that killed the static pass mid-scan: it printed its header, exited 1 and checked nothing, and nobody had noticed because `uavx_ws/src` does not exist yet so the guard above it always fired first.

## Next

NEXT-WEEK: 4

Week 4 is every scenario where something breaks, plus the packaging tail. Eight
chunks, and `4.7` is the integrated run the proposal is built on.

Two things are known to be waiting there and both are written up in
[docs/progress/week-3.md](docs/progress/week-3.md) rather than left to be
discovered, and both turn on the 10 Hz clock.

The separation floor and the altitude layers are both frozen at 10 m, so two
vehicles stacked one above the other sit exactly on the floor and the accepted
survey run recorded 9.90 m. Standing rule 4 forbids moving either number. Where
it bites is narrower than that sounds: the integrated mission flies its two
surveyors mirrored, 12.5 m apart in x, so they are never closer than 16.0 m,
and the relay band puts every mover 15 m above the highest mission altitude.
The scenario that stacks vehicles on purpose is `encounter`, and there the
yield rule is what has to hold them apart.

`queue_drain` asserts a control queue delay at or under 50 ms against a 10 Hz
clock, which rules out one implementation rather than all of them. A packet
served on the next timer tick reads 100 ms; a packet served in the callback
that queued it reads the same clock twice and reads 0. So control is served on
arrival and never behind data, and the 2.25 s drain bound holds the same way,
with the first batch going out when the route returns rather than at the next
tick.

Before any of it, read the three progress files. Between them they carry 27
defects found in the acceptance harness itself, and the harness is what every
week is graded by.

## Execution notes

Runs under `/loop` in its own session, separate from CycloProp. **Never run a loop longer than 7 to 8 hours**; past that the increment per cycle collapses and it just spins.
