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

## What the organisers actually asked for

Checked against the live record on 27 August, not against memory. `scripts/check_competition_spec.py` refetches `techfest.org/api/compis/` and diffs every binding field against the capture in `research/`. Twelve fields, all unchanged since 26 August. It runs in preflight every week and again in W5 without its offline flag, because the rules reserve the organisers' right to modify any stage and the only way we would find out is by looking.

Five deliverables, one email, and each one has a row in the W5 gate:

| Asked for | Where it comes from |
| --- | --- |
| 6 to 8 page technical proposal with software architecture | drafted a section per week, checked for page count, length and content |
| A working proof-of-concept simulation | `mission_integrated`, the one run where the subsystems have to compose |
| Source code | `git archive` of the frozen commit, every file rechecked against it |
| Installation instructions | `INSTALL.md`, rehearsed in W3 and bound in W5 |
| Demo video | recorded against a rehearsed capture path, decoded end to end |

Three things in the rules are not in the rubric and are easy to skip. All three are cheap and all three are now gated: the solution must **comply with Indian aviation and safety law**, so the proposal carries a BVLOS regulatory section; the entrant is responsible for **not infringing third-party IP**, so the archive carries `LICENSE` and `THIRD-PARTY.md`; and the **published record can change**, so it gets refetched.

## The one thing that shapes everything

60% of the rubric is communication resilience, relay and role management, and fault recovery. Mission completion is 25%. Safety is 10%.

None of the accepted simulators model radio. PX4, ArduPilot, Gazebo and AirSim let every vehicle talk to every other vehicle at any distance, forever. The mesh is not configuration, it is the deliverable, and it is most of the marks. Flying is a week and the tooling nearly gives it away.

And the fault is two faults. The organisers say it twice, in the challenge statement and again in the FAQ: the swarm reconfigures as UAVs **fail or lose connectivity**. Stage 2 promises hidden disturbances "such as UAV failures and communication outages", the same pair in different words. A dead vehicle and a vehicle that has gone quiet are not the same problem, because the quiet one is still in the air and it comes back. Both are in W4.

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
- The generic scenario event injector, moved here from W4. It needs no roles code and W4 has no room for it.
- The ROS graph snapshot the seam checker reads, captured by the runner while a scenario is up. Every endpoint carries its message **type** as well as its topic. Round 4 finding 4: types were being thrown away, and without them the rule banning swarm payload outside the seam cannot be enforced at all. `scripts/seam_graph.py` refuses a snapshot that lacks them rather than checking what is left.

Gate: `bash scripts/gate.sh 1`

### W2, 2 to 8 September. Survey mission and the coverage metric

Goal: four vehicles cover a frozen area, and the run reports how much of it they actually flew over.

Produces:

- `uavx_mission`: boustrophedon planner, a partitioner splitting the frozen survey box into 4 strips, a mission executor
- `uavx_eval`: the metrics collector, and `uavx_eval.check`, which validates provenance before reading any metric
- `scenarios/survey_baseline.yaml` at the coordinates in [architecture.md](architecture.md) section 6
- Pure link-model unit tests, brought forward from W3 so W3 is only integration
- Pure routing and election state-machine tests, also brought forward. Neither needs SITL, both are the fiddly part, and W3 and W4 are the two weeks with no slack.

`coverage_fraction` comes from **sampled vehicle poses**, never the planned path. The box is frozen. If coverage cannot reach the gate in 420 s the planner is wrong, and shrinking the box would be moving the goal rather than fixing it.

Gate: `bash scripts/gate.sh 2`

### W3, 9 to 15 September. The comms layer

Goal: swarm messages reach the GCS only through the modelled radio, and `uav_4` reaches it only by relay.

Produces:

- `uavx_comms`: the link model, the link layer holding the tx/rx seam, the link-state router with every timer from [architecture.md](architecture.md) section 3
- `uavx_gcs`: the ground station, with its own `/uavx/gcs/tx` and `/uavx/gcs/rx`
- `scenarios/relay_required.yaml` and `scenarios/direct_only.yaml`

The control run is the important half. A delivery ratio of 1.0 proves nothing on its own, because that is also what a silently open gate produces. `uav_4` is 484.6 m from the GCS, beyond `r_max`, so with forwarding disabled its delivery must be exactly 0.

Also this week, because W5 has no room for them: a fresh-install **dry run** writing `submission/dryrun-install-receipt.json`, and a 60 second recording dry run writing `submission/dryrun-recording-receipt.json` with the clip it produced. `scripts/check_dryruns.py` is in the W3 gate, decodes the clip and ties both receipts to the current source tree, so the week cannot be accepted without them and hand the bill to W5.

Round 4 finding 1: the plan said both of these were in the W3 gate and the gate asked for neither, which made the sentence decoration. The recording one carries the most risk, because the `gazebo` GUI binary has taken this distro down three times and the video is a deliverable.

The install that binds the submission is a different thing and happens after code freeze in W5, against the archive rather than the working tree. Round 3 finding 3: an install tested in W3 cannot vouch for source that W4 and W5 have since changed.

Hard stop. This is the submission. If the gate is not green on 15 September, W4 gives up days to it rather than the other way round.

Gate: `bash scripts/gate.sh 3`

### W4, 16 to 22 September. Roles, fault recovery and safety

Goal: kill the relay mid-mission and watch the swarm notice, elect, reposition and rebuild the chain with nobody touching anything, without vehicles converging on each other.

Produces:

- `uavx_roles`: the state machine in [architecture.md](architecture.md) section 4, epochs and leases included
- The integrated mission, `scenarios/mission_integrated.yaml`, which is the run the proposal and the video both point at. Every other scenario proves one subsystem alone; this is the only one that proves the swarm.
- Altitude deconfliction, the separation monitor, the yield rule
- `scenarios/relay_kill.yaml`, `scenarios/link_loss.yaml`, `scenarios/encounter.yaml` and its negative control `scenarios/encounter_noyield.yaml`

`scenarios/link_loss.yaml` is the second half of the fault the organisers name. `uav_2` keeps flying and loses its radio, then gets it back. It reuses `relay_kill`'s geometry exactly, so the two runs differ in one thing and the comparison is the argument, and it costs one scenario file plus two rules rather than a new topology.

It is also what found a real defect in the design. The slot rule balances two hops and says nothing about airspace, so on the integrated geometry it puts the relay 6.8 m from `uav_2`, inside the separation floor. Nothing caught it because `uav_2` is dead in every scenario that computes a slot, which is a property of the scenario list rather than of the rule. A vehicle that loses its radio is still flying. Both the clearance rule and its worked counterexample are in `check_geometry.py`.

`gps_degrade` stays in Stage 2. Round 2 costed three failure modes at more hours than the week has, and that still holds; what changed is that this one is nearly free, because the link layer already decides delivery per message and the event injector landed in W1.

**Fallback, if the role-release half does not land.** Run `link_loss` with the release rule disabled and claim the routing recovery only, which is still the named failure demonstrated. Say so in the proposal rather than implying more. Time comes from W2's slack under standing rule 6, never from W3.

`encounter.yaml` exists because altitude layers alone would keep everything apart and leave the yield rule as dead code that still scored 10%. Its control run, with yielding disabled, must violate separation; without that, a pass is indistinguishable from two vehicles that happened to miss each other.

Gate: `bash scripts/gate.sh 4`

### W5, 23 to 26 September. Freeze, package, submit

Four days, not five.

Goal: five deliverables in one email, sent a day early.

By now the proposal is mostly written, because each week drafts its own section as its metric lands. W5 is reruns, editing and packaging, not authorship.

Produces:

- The source freeze, by `scripts/freeze_source.sh`: commit `C`, `submission/uavx-source.zip` built from it with `git archive`, `submission/source-manifest.json` carrying `C`, the archive hash and the source tree hash
- The binding fresh install, run against that archive, receipt bound to the archive hash
- `submission/proposal.pdf`, 6 to 8 pages, citing the run id behind every number it quotes, and carrying a section on Indian BVLOS regulation because the rules require the solution to comply with it
- `submission/demo.mp4`, opening on the failure and the recovery
- `submission/INSTALL.md`
- `LICENSE` and `THIRD-PARTY.md` inside the archive, since the rules make the entrant responsible for third-party IP and this is built on PX4, Gazebo and ROS 2
- A refetch of the published competition record, this time with no offline flag, so the package is checked against what the organisers say on the day rather than what they said on 26 August
- `submission/evidence-manifest.json`, naming the exact run record behind each scenario
- `submission/attachment-manifest.json`, the file list with hashes and sizes, checked against the delivery budget in `submission/human-preflight.json`
- `submission/fresh-install-receipt.json`, carrying the submitted commit SHA
- `submission/sent-receipt.json`, written by the human after sending, bound to the attachment manifest

`check_submission.py` rehashes the archive, checks every file in it against commit `C`, revalidates every named run against the schema and against `uavx_eval.check`, and requires each run's source hash to match the frozen source. Round 4 findings 5 and 6: it used to compare two strings that both came out of files we wrote, and count run evidence by filename. `scripts/test_submission_fixtures.py` tampers with each of those in turn and proves the rejection.

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

## Before week 1 runs at all

These block. `bash scripts/gate.sh preflight` refuses to start a week without `submission/human-preflight.json`, and every gate begins with preflight, so nothing in the five weeks above happens until they are done.

They used to sit here as a backlog list. Round 3 finding 4 pointed out that an unregistered or ineligible entrant has no valid submission however good the simulation is, and eligibility disqualifies at any stage, including after Stage 1 results. Five weeks of work behind an invalid entry is the worst outcome available and the cheapest one to prevent.

[human-preflight.md](human-preflight.md) has the detail and the receipt to write. In short: register, declare eligibility, join the clarification channel, send the organiser questions, and decide how a package too large to attach actually gets delivered.
