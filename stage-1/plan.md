# UAV-X Stage 1 build plan

**Deadline:** 27 September 2026, by email to pushpak_gc2026@aero.iitb.ac.in. We send on 26 September.
**Capacity:** one person, 4 to 6 hours a day, so roughly 30 hours a week.
**Execution:** five weeks, one per `/loop` tick. Config in [.claude/weekly-loop.md](../.claude/weekly-loop.md).

Four files, and they do not repeat each other:

| File | Holds |
| --- | --- |
| [context.md](../context.md) | what the organisers published. Outranks everything else here. |
| [architecture.md](architecture.md) | the frozen design. Every parameter, protocol and coordinate. |
| [decisions.md](decisions.md) | the locked calls and the fallback per week. |
| this file | what gets built when, and what proves it. |

Gates are `scripts/gate.sh`, and that script is the only definition of them. Round 2 found the same gates written out in three documents that had already drifted, with one version failing a correct implementation. Prose cannot be the source of truth for something a machine enforces, so nothing here restates a threshold.

## The one thing that shapes everything

60% of the rubric is communication resilience, relay and role management, and fault recovery. Mission completion is 25%. Safety is 10%.

None of the accepted simulators model radio. PX4, ArduPilot, Gazebo and AirSim let every vehicle talk to every other vehicle at any distance, forever. The mesh is not configuration, it is the deliverable, and it is most of the marks. Flying is a week and the tooling nearly gives it away.

## Already done, before W1

The environment is up and verified, and the execution harness exists. This was week 1's stated work and it is finished:

- Ubuntu 22.04.5, ROS 2 Humble, Gazebo Classic 11.10.2, PX4 v1.15.4, uXRCE-DDS agent v2.4.3, all pinned by SHA in [setup/versions.lock](setup/versions.lock)
- `scripts/sitl_multi.sh` brings up 4 vehicles headless with a ROS namespace each, and proves it. Measured: 4 px4 processes, no gzclient, 43 topics per namespace, 11050 MiB of 11821 free, zero swap
- `scripts/gate.sh` and `scripts/gate-env.sh`, the gate harness
- `scripts/check_geometry.py`, `scripts/check_seam.sh`, `scripts/check_submission.py`
- `scenarios/run-record.schema.json`, the provenance contract

So W1 starts from a working simulator rather than from nothing, and its freed time goes to `uavx_msgs` and the run harness, which is what round 2 finding 10 asked for.

## Metric spine

Every rubric line gets a number this repo produces, logged per run, quoted in the proposal. Which artifact earns which row is in [architecture.md](architecture.md) section 8.

## The five weeks

Each week states its goal, what it produces, and its gate. **The gate is the contract.** A week is done when `bash scripts/gate.sh <N>` exits 0, and not before.

Read the exit code from `wsl.exe` itself. A `$?` inside a quoted `wsl.exe` command prints 0 whatever happened, which would make every gate pass forever.

### W1, 26 August to 1 September. Messages, the run harness, and flight

Goal: four vehicles fly a commanded takeoff and land, and the scaffolding every later week writes into exists.

Produces:

- `uavx_ws/src/uavx_msgs`: `SwarmPacket`, `LinkState`, `RoleAssignment`, `Hello`, `RunMetrics`
- `uavx_sim/scenario_runner`: reads a scenario YAML, launches the stack via `scripts/sitl_multi.sh`, runs to the scenario's duration, tears down, writes the run record
- `scripts/run_scenario.sh` and `scripts/run_smoke.sh`
- The run record writer, to `scenarios/run-record.schema.json`, publishing `latest.jsonl` by atomic rename only after every process has exited cleanly
- Peak-memory and swap sampling in every run record

Gate: `bash scripts/gate.sh 1`

### W2, 2 to 8 September. Survey mission and the coverage metric

Goal: four vehicles cover a frozen area, and the run reports how much of it they actually flew over.

Produces:

- `uavx_mission`: boustrophedon planner, a partitioner splitting the frozen 400 m box into 4 strips, a mission executor
- `uavx_eval`: the metrics collector, and `uavx_eval.check`, which validates provenance before reading any metric
- `scenarios/survey_baseline.yaml` at the coordinates in [architecture.md](architecture.md) section 6
- Pure link-model unit tests, brought forward from W3 so W3 is only integration

`coverage_fraction` comes from **sampled vehicle poses**, never the planned path. The box is frozen. If coverage cannot reach the gate in 420 s the planner is wrong, and shrinking the box would be moving the goal rather than fixing it.

Gate: `bash scripts/gate.sh 2`

### W3, 9 to 15 September. The comms layer

Goal: swarm messages reach the GCS only through the modelled radio, and `uav_4` reaches it only by relay.

Produces:

- `uavx_comms`: the link model, the link layer holding the tx/rx seam, the link-state router with every timer from [architecture.md](architecture.md) section 3
- `uavx_gcs`: the ground station, with its own `/uavx/gcs/tx` and `/uavx/gcs/rx`
- `scenarios/relay_required.yaml` and `scenarios/direct_only.yaml`

The control run is the important half. A delivery ratio of 1.0 proves nothing on its own, because that is also what a silently open gate produces. `uav_4` at 981.8 m from the GCS is beyond `r_max`, so with forwarding disabled its delivery must be exactly 0.

Also this week, because W5 has no room for them: the fresh-install test runs once end to end, and a 60 second recording dry run proves the video path works.

Hard stop. This is the submission. If the gate is not green on 15 September, W4 gives up days to it rather than the other way round.

Gate: `bash scripts/gate.sh 3`

### W4, 16 to 22 September. Roles, fault recovery and safety

Goal: kill the relay mid-mission and watch the swarm notice, elect, reposition and rebuild the chain with nobody touching anything, without vehicles converging on each other.

Produces:

- `uavx_roles`: the state machine in [architecture.md](architecture.md) section 4, epochs and leases included
- Failure injection for `kill` only
- Altitude deconfliction, the separation monitor, the yield rule
- `scenarios/relay_kill.yaml` and `scenarios/encounter.yaml`

`comms_blackout` and `gps_degrade` move to Stage 2. Stage 1 needs one failure demonstrated properly more than it needs three demonstrated thinly, and round 2 costed all three at more hours than the week has.

`encounter.yaml` exists because altitude layers alone would keep everything apart and leave the yield rule as dead code that still scored 10%.

Gate: `bash scripts/gate.sh 4`

### W5, 23 to 26 September. Final runs, packaging, submit

Goal: five deliverables in one email, sent a day early.

By now the proposal is mostly written, because each week drafts its own section as its metric lands. W5 is reruns, editing and packaging, not authorship.

Produces:

- `submission/proposal.pdf`, 6 to 8 pages
- `submission/demo.mp4`, opening on the failure and the recovery
- `submission/INSTALL.md`, `submission/uavx-source.zip`
- `submission/fresh-install-receipt.json`, carrying the submitted commit SHA
- `submission/sent-receipt.json`, written by the human after sending

Gate: `bash scripts/gate.sh 5`

`check_submission.py` exits 2 when the package is complete but unsent, and the loop halts there. Sending is a human step, so "ready" and "submitted" are deliberately different states.

## Standing rules

1. A week is done when its gate exits 0 in a shell, run after the work.
2. Every number in a document traces to a JSONL under `runs/`. Nothing is quoted from memory.
3. Every run is seeded and replays exactly. A result nobody can reproduce is not evidence.
4. **No week-agent may change a value in [architecture.md](architecture.md) or a threshold in `scripts/gate.sh` to make its own gate pass.** That is moving the goal. If a number is unreachable, stop and report it.
5. Each week drafts its proposal section as its metric lands.
6. When a week overruns, take days from the phase with the lowest rubric weight that still has slack. Never from W3.

## What this plan does not cover

- Stage 2 and Stage 3. The seam behind the link layer means a UDP shaper can replace the application gate without touching routing, roles or mission code. That is the intended Stage 2 upgrade, along with `comms_blackout` and `gps_degrade`.
- Hardware. There is none in the challenge.

## Open items, not blocking

- Register on techfest.org, Competitions, PUSHPAK Grand Challenge
- Join the UAV-X WhatsApp group, where clarifications land first
- Send the organiser email, drafted in [organiser-email.md](organiser-email.md)
- Confirm no attachment to the PUSHPAK project, the Drone Centre or the organising institutions, which disqualifies at any stage
