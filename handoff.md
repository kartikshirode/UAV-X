# UAV-X handoff

Written 26 August 2026. Stage 1 is due **27 September 2026**, so there are 32 days. This file is where a fresh session starts; everything else links from here.

## What this is

PUSHPAK Grand Challenge 2026, Grand Challenge on resilient BVLOS drone swarms. Build an autonomous UAV swarm that surveys a disaster zone, holds a multi-hop link back to a ground control station beyond visual line of sight, and reconfigures itself as drones fail or lose connectivity. Simulation only, no physical drones anywhere in the challenge.

Funded by MeitY under the national drone mission, hosted by IIT Bombay, presented by IISERB with IISER Bhopal and VJTI Mumbai. Prize ceiling is INR 25,50,000 across the whole staged programme.

Read [brief.md](brief.md) for the full competition rules, then [stage-1/plan.md](stage-1/plan.md) for the build plan. Settled calls and the go/no-go gates are in [stage-1/decisions.md](stage-1/decisions.md), and installing the stack is [stage-1/setup/README.md](stage-1/setup/README.md). Shared track rules are in [_shared-timeline.md](_shared-timeline.md), compute notes in [_compute.md](_compute.md), and the first plan review in [_plan-review-round1.md](_plan-review-round1.md).

Repo: github.com/kartikshirode/UAV-X. This project is its own repository and does not share a session with CycloProp.

## Where we are

Day 1, 26 August. The three week 1 forks are closed and written down in [stage-1/decisions.md](stage-1/decisions.md): WSL2 Ubuntu 22.04 with ROS 2 Humble, PX4 v1.15 and Gazebo Classic; the comms layer gates at the ROS 2 application layer; solo entry at 4 to 6 hours a day. Every phase now has a date, a pass condition and a fallback.

The Ubuntu 22.04 distro is downloading. Provisioning scripts for the whole stack are written and committed but have not been run yet, so nothing is installed and nothing flies.

Round 1 of the plan cross-check found 8 issues. Findings 1, 4, 5 and 6 are answered. Round 2 by Codex has not happened.

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

1. **Environment.** Still the only live risk. Nothing is installed. The scripts exist, they have never run, and the first run is where week 1 gets decided.
2. **The organiser email.** Drafted in [stage-1/organiser-email.md](stage-1/organiser-email.md), not sent.
3. **Everything downstream of the environment.**

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

Settled: WSL2 Ubuntu 22.04 native, not Docker, because a container boundary hides the GUI fight rather than ending it. The matched set is Ubuntu 22.04, ROS 2 Humble, PX4 v1.15 and Gazebo Classic 11, pinned together on purpose. Picking the latest of each independently is the most common way week 1 goes wrong.

Run `bash stage-1/setup/setup-all.sh` inside the Ubuntu shell, then `bash stage-1/setup/verify.sh` in a fresh one. Steps are reentrant, so a failed run resumes at the step that broke.

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

- The setup scripts have been syntax checked and nothing more. Version pins in them (the ros2-apt-source package, gazebo11 from osrfoundation, the newest v1.15.x PX4 tag, px4_msgs on release/1.15) are written from documentation rather than from a machine where they worked. Expect at least one of them to be wrong.
- WSLg is untested here. If Gazebo's GUI does not open, the work continues headless but the demo video does not, and that has to be sorted before 20 September.
- Round 2 of the plan cross-check, by Codex, has not run.

## Execution notes

Runs under `/loop` in its own session, separate from CycloProp. **Never run a loop longer than 7 to 8 hours**; past that the increment per cycle collapses and it just spins.
