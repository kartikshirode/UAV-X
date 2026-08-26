# UAV-X handoff

Written 26 August 2026. Stage 1 is due **27 September 2026**, so there are 32 days. This file is where a fresh session starts; everything else links from here.

## What this is

PUSHPAK Grand Challenge 2026, Grand Challenge on resilient BVLOS drone swarms. Build an autonomous UAV swarm that surveys a disaster zone, holds a multi-hop link back to a ground control station beyond visual line of sight, and reconfigures itself as drones fail or lose connectivity. Simulation only, no physical drones anywhere in the challenge.

Funded by MeitY under the national drone mission, hosted by IIT Bombay, presented by IISERB with IISER Bhopal and VJTI Mumbai. Prize ceiling is INR 25,50,000 across the whole staged programme.

Read [context.md](context.md) first. It holds the official competition record pulled straight off the Techfest API, and it outranks every other file here, including this one. Then [stage-1/plan.md](stage-1/plan.md) for the five week build plan, [stage-1/decisions.md](stage-1/decisions.md) for the locked calls and the gate per week, and [stage-1/setup/README.md](stage-1/setup/README.md) for installing the stack.

`brief.md` is the older, looser summary and is superseded by context.md wherever the two disagree. Shared track rules are in [_shared-timeline.md](_shared-timeline.md), compute notes in [_compute.md](_compute.md), the first plan review in [_plan-review-round1.md](_plan-review-round1.md), and the reviewer prompt in [_codex-review-prompt.md](_codex-review-prompt.md).

Repo: github.com/kartikshirode/UAV-X. This project is its own repository and does not share a session with CycloProp.

## Where we are

Day 1, 26 August. Planning is done and the environment is most of the way up. No project code has been written, on purpose: the plan goes through Codex first.

**Decided and locked** in [stage-1/decisions.md](stage-1/decisions.md): WSL2 Ubuntu 22.04 with ROS 2 Humble, PX4 v1.15 and Gazebo Classic; the comms layer gates at the ROS 2 application layer; solo entry at 4 to 6 hours a day; 4 vehicles.

**Installed and checked:** Ubuntu 22.04.5 under WSL2, user `kartik` with passwordless sudo, ROS 2 Humble desktop, Gazebo Classic 11.10.2 with `gzserver` and `gzclient` present. PX4 v1.15.4 is cloned and building; the uXRCE-DDS bridge and the px4_msgs workspace come after it.

WSLg works. `DISPLAY=:0` and `WAYLAND_DISPLAY=wayland-0` are both set, so the demo video has a path.

**The plan is now built for the loop.** Five weeks, each ending in gate commands that exit 0 or do not. Config at [.claude/weekly-loop.md](.claude/weekly-loop.md).

Round 1 of the plan cross-check found 8 issues; findings 1, 4, 5, 6 and 7 are answered. Round 2 by Codex has not run, and the plan should not be executed before it does. Prompt is ready in [_codex-review-prompt.md](_codex-review-prompt.md).

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

1. **Round 2 of the plan review.** The plan has never been read by anything except its author. Rereading it once already turned up two gate thresholds that were wrong, one of which would have failed a correct implementation. Prompt is in [_codex-review-prompt.md](_codex-review-prompt.md); run it before any code gets written.
2. **The rest of the environment.** The PX4 build and the ROS 2 bridge. Neither has completed once.
3. **Recording the video.** The `gazebo` GUI binary takes the distro down, so the one thing still blocked is the part that needs a picture. `gzclient` is installed and untested. Deal with it well before 20 September, and keep the headless fallback.
4. **The organiser email.** Drafted in [stage-1/organiser-email.md](stage-1/organiser-email.md), not sent.

Closed on 26 August: whether Gazebo runs here at all. `gzserver` starts headless, stays up, survives the dxg ioctl failures that fill dmesg, and leaves the distro alone. Only the GUI-side binary is dangerous, so every gate in the plan can run headless.

## Running it, once there is anything to run

Nothing runs yet. Machine as it stood on day 1:

| | |
| --- | --- |
| CPU | i7-13650HX, 14 cores, 20 threads |
| RAM | 23.7 GB |
| GPU | RTX 4060 Laptop |
| OS | Windows 11 |
| WSL | Installed, but the only distro is `docker-desktop`, which is the Docker backend and not a dev environment |
| git | present |
| gh | **not installed** |
| docker | present |
| Python | 3.12 |

The machine is comfortably strong enough for 4 to 6 vehicle SITL. The OS is the problem: PX4, ROS 2 and Gazebo are Ubuntu-first and there is no Ubuntu here.

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

## Do these in week 1, before technical work

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- **Check no attachment to the PUSHPAK project, the Drone Centre, or the organising or host institutions.** This disqualifies an entry at any stage.
- Send the organiser email, drafted in [stage-1/organiser-email.md](stage-1/organiser-email.md)

Team size is settled: solo entry.

## Starting points worth reading before writing from scratch

- **UAVros**, ROS1 and ROS2 kit for PX4 multi-rotor and UGV swarm simulation
- **Aerostack2**, ROS2 framework for multi-robot aerial systems, plugin architecture
- PX4 multi-vehicle docs: new Gazebo uses `PX4_GZ_STANDALONE=1` per extra instance with distinct poses and system IDs, plus `MicroXRCEAgent udp4 -p 8888` for the ROS 2 bridge. Gazebo Classic has `sitl_multiple_run.sh`, default 3 vehicles.
- MAVLink `MAV_SYS_ID` caps at 255 vehicles, so nothing near our range is a limit. Use 4 to 6; more drones cost debugging time we do not have.

## Things that bit earlier cluster projects, so they do not bite this one

Carried from the Vaani and Adversarial IDS handoffs, same servers:

- Windows ssh strips quotes. Never send a python one-liner or heredoc through `ssh`. Write the file, `scp` it, run it. Same for PowerShell inline python.
- Git on Windows turned slurm scripts CRLF once and sbatch refused them. Force LF in `.gitattributes` and run `sed -i "s/\r$//"` after any sync anyway.
- uv venvs on the cluster have no pip. Use `.venv/bin/python` directly.
- Assume compute nodes have no outbound internet until proven otherwise.

## Honest gaps

- Steps 04 and 05 of the setup have never completed. The px4_msgs `release/1.15` branch, the XRCE agent tag and the PX4 build itself are documentation rather than observed fact.
- Nothing has flown. `gzserver` starting on an empty world is a long way from 4 PX4 instances airborne together, which is what the W1 gate actually asks for.
- Package version checks were treating packages apt merely knows about as installed, so `verify.sh` passed for hours on a machine with no simulator binaries. Every gate from here asserts on artifacts, not on metadata. Assume the same class of mistake is hiding somewhere else.
- Every threshold in the plan gates was chosen by its author. `coverage_fraction>=0.95` and `time_to_reconnect_s<=30` were never derived from anything, and the reconnect budget has to cover neighbour timeout plus election plus flight time before it means much.
- WSLg has never produced a display here. Headless carries the work but not the demo video, and that needs sorting before 20 September.
- Round 2 of the plan cross-check, by Codex, has not run.

## Execution notes

Runs under `/loop` in its own session, separate from CycloProp. **Never run a loop longer than 7 to 8 hours**; past that the increment per cycle collapses and it just spins.
