# UAV-X Stage 1 build plan

**Deadline:** 27 September 2026, by email to pushpak_gc2026@aero.iitb.ac.in. We send on 26 September.
**Capacity:** one person, 4 to 6 hours a day, so roughly 30 hours a week.
**Execution:** four weeks, one per `/loop` tick, and 25 chunks inside them. Config in [.claude/weekly-loop.md](../.claude/weekly-loop.md).

Four files, and they do not repeat each other:

| File | Holds |
| --- | --- |
| [context.md](../context.md) | what the organisers published. Outranks everything else here. |
| [architecture.md](architecture.md) | the frozen design. Every parameter, protocol and coordinate. |
| [decisions.md](decisions.md) | the locked calls and the fallback per week. |
| this file | what gets built when, and what proves it. |

Gates are `scripts/gate.sh`, and that script is the only definition of them. Round 2 found the same gates written out in three documents that had already drifted, with one version failing a correct implementation. Prose cannot be the source of truth for something a machine enforces, so nothing here restates a threshold.

## What the organisers actually asked for

Checked against the live record, not against memory. `scripts/check_competition_spec.py` refetches `techfest.org/api/compis/`, diffs every field against the capture in `research/`, and downloads the linked problem statement to rehash its bytes. Everything is binding unless it is named as noise, so a field nobody thought about cannot change quietly; `live` turning false stops the gate rather than printing a note.

It earned its place immediately. On its first real run it caught VJTI Mumbai being removed from the published collaborator list, inside 24 hours of the capture, and that is the sentence the eligibility rule points at. Nothing else has moved.

Preflight runs it every week and tolerates being offline only while a genuine check is less than a week old. W5 runs it with no flags at all.

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

## Why four weeks and not five

This was a five week plan: four building and four days packaging, 26 August to 26 September. Nothing was built in the first four days, so the calendar lost a week and the plan had to give it back from somewhere.

It comes out of the packaging tail. Each week now produces its own proposal section, its own demo footage and its own `INSTALL.md` changes as its work lands, so the tail is a freeze and a send rather than four days of writing. Standing rule 5 already said the proposal is drafted a section per week; this extends it to the video and the instructions, which were the two things still pooled at the end.

What that buys is W4 keeping 5 build days for the roles, fault recovery and safety work that carries 45% of the rubric. The alternative was 3, and the audits have said twice that W3 and W4 have no slack.

Nothing is cut. All nine scenarios stand.

## The four weeks

Each week states its goal, what it produces, and its gate. **The gate is the contract.** A week is done when `bash scripts/gate.sh <N>` exits 0, and not before.

### Chunks

A week is too big to be the unit of feedback. Round 6's conclusion pass found W1 promising seven deliverables behind a gate that ran one command, which is the same defect round 4 found in W3 and W4, sitting in the week nobody read first.

So every week is split into chunks and every chunk has its own gate:

```
bash scripts/gate.sh chunks   list all 25
bash scripts/gate.sh 1.3      run one chunk, on its own
bash scripts/gate.sh 1        run every chunk of week 1, in order
```

A chunk is one deliverable and one question. Finish it, run its gate, know where you stand. `scripts/check_docs.py` fails if a chunk exists that no week calls, if a chunk has no id you can run alone, or if a deliverable named in a week here never appears in that week's gate. That last check is the one that would have caught W1 six rounds ago.

Read the exit code from `wsl.exe` itself. A `$?` inside a quoted `wsl.exe` command prints 0 whatever happened, which would make every gate pass forever.

### W1, 30 August to 5 September. The scaffolding every later week writes into

Goal: four vehicles fly, and the harness that records what they did is proven rather than assumed.

The five messages and the two script interfaces are frozen in [architecture.md](architecture.md) section 1b. They were a list of names until round 6's conclusion pass, which made W1's first deliverable the least specified thing in the project.

| Chunk | Produces | The question it answers |
| --- | --- | --- |
| `1.1` | `uavx_ws/src/uavx_msgs`: `SwarmPacket`, `LinkState`, `RoleAssignment`, `Hello`, `RunMetrics` | do the five types resolve, and does `SwarmPacket` carry the identity fields every delivered-once claim rests on |
| `1.2` | `scripts/run_smoke.sh` | do four vehicles get airborne together, headless |
| `1.3` | `scenarios/harness_check.yaml`, the run record writer, and `scripts/validate_record.py` to check it | does the scenario exist and say what the later chunks assume, does what the writer produces satisfy `scenarios/run-record.schema.json`, and is `latest.jsonl` published only after a clean exit |
| `1.4` | `uavx_sim/scenario_runner` and `scripts/run_scenario.sh` | does it honour the scenario's duration, record the right vehicles, and leave nothing running |
| `1.5` | the generic event injector, moved here from W4 | does an injected event fire and get observed, or is it only listed |
| `1.6` | the ROS graph snapshot, captured while a scenario is up | will `scripts/seam_graph.py` accept it: types on every endpoint, one `ros2 node info` result per node, tied to the run it came from |
| `1.7` | peak memory and swap sampling in every record | do four PX4 instances, gzserver and our nodes fit in 11 GB without swapping |

They run against `scenarios/harness_check.yaml`, four vehicles hovering for 60 s with one injected event. It proves the harness and is never cited as evidence for anything, which is why it sits outside the nine runs W5 requires. Chunk 1.3 produces it and `scripts/check_scenario.py` holds it to the duration, vehicle count and injected event the later chunks assume: `pose_sample_count>=100` is a claim about a 60 second run, and against a 5 second scenario it is a much weaker claim that nothing would have flagged.

W1 asserts through `scripts/validate_record.py`, not through `uavx_eval.check`. Round 7 finding 1: the chunks reached `uavx_eval` by way of the gate's own helper and `uavx_eval` is built in chunk 2.2, so a correct W1 could not pass its own gates. The two checkers are not redundant. The validator checks a record against the schema and the values asked of it; `uavx_eval.check` additionally verifies provenance against the working tree, which is what W2 onward needs and what chunk 2.2 proves by making it reject a record.

`1.7` is here rather than later on purpose. Nobody has measured this stack under load, and if it does not fit, finding out in W1 with two vehicles of headroom is recoverable. Finding out during `mission_integrated` is not.

Also this week: the proposal's architecture section, drafted while the design is being built rather than recalled in the last three days.

Gate: `bash scripts/gate.sh 1`

### W2, 6 to 12 September. Survey mission and the coverage metric

Goal: four vehicles cover a frozen area, and the run reports how much of it they actually flew over.

| Chunk | Produces | The question it answers |
| --- | --- | --- |
| `2.1` | `uavx_mission`: boustrophedon planner, a partitioner splitting the frozen box into 4 strips, a mission executor | do the planner and partitioner hold up in unit tests, with no simulator involved |
| `2.2` | `uavx_eval`: the metrics collector and `uavx_eval.check`, which validates provenance before reading any metric | does it reject a record whose provenance does not hold |
| `2.3` | `uavx_comms` pure logic: the link model, and the routing and election state machines | do they hold up in unit tests, brought forward so W3 and W4 are integration only |
| `2.4` | `scenarios/survey_baseline.yaml` at the coordinates in [architecture.md](architecture.md) section 6 | does the swarm actually cover the box |

`2.3` is the fix for a gap round 6's conclusion pass found. The plan has always pulled these tests forward to keep W3 and W4 clean, and the W2 gate named `uavx_mission` and `uavx_eval` only, so the tests it was built around lived in a package the week never tested. W2 could pass with none of them written.

`coverage_fraction` comes from **sampled vehicle poses**, never the planned path. The box is frozen. If coverage cannot reach the gate in 420 s the planner is wrong, and shrinking the box would be moving the goal rather than fixing it.

Also this week: the proposal's mission and coverage section, and the first demo footage, a clean survey run to open the video on.

Gate: `bash scripts/gate.sh 2`

### W3, 13 to 19 September. The comms layer

Goal: `uav_4` reaches the GCS only by relay, and cannot reach it at all with forwarding off.

This is the week. 25% of the rubric is communication resilience and none of the accepted simulators model radio, so the mesh is the deliverable rather than configuration. Standing rule 6 takes days from nowhere else and gives them to nothing else.

| Chunk | Produces | The question it answers |
| --- | --- | --- |
| `3.1` | `uavx_comms` integration, `uavx_gcs` with its own tx and rx | do the packages build and pass their tests together |
| `3.2` | nothing new; the frozen topology rechecked | does the geometry still support what the scenarios claim, before a simulator is started |
| `3.3` | the tx/rx seam held in source | does any swarm module reach outside `/uavx/<own>/tx` and `/uavx/<own>/rx` |
| `3.4` | `scenarios/relay_required.yaml` | does `uav_4` reach the GCS through `uav_3`, at 2 hops, with a delivery ratio above 0.95 |
| `3.5` | `scenarios/direct_only.yaml`, the control | with forwarding off, does `uav_4` reach the GCS not at all |
| `3.6` | the rebuild and recording rehearsals | does the install path work, and does the video capture path work, found out now rather than in the last three days |

`3.5` is the control and it matters as much as `3.4`. A relay that "works" in a scenario where the direct link would also have worked has demonstrated nothing, and the two together are what make the claim falsifiable.

`3.6` runs both wrappers and then checks their evidence. Round 6 finding 4: the gate used to run the checker and neither wrapper, so a week could go green on receipts left over from a previous run.

Also this week: the proposal's communication architecture section, which is the section carrying the most marks, and footage of a relayed delivery.

Gate: `bash scripts/gate.sh 3`

### W4, 20 to 26 September. Roles, fault recovery, safety, then send

Goal: the relay dies, the swarm elects a replacement, repositions it, rebuilds the chain, and the package goes out on the 26th.

Five build days and two to send. That split is what distributing the packaging bought; the alternative was three build days for the 45% of the rubric that lives here.

| Chunk | Produces | The question it answers |
| --- | --- | --- |
| `4.1` | `uavx_roles`: the state machine in [architecture.md](architecture.md) section 4, epochs and leases | do the roles packages build and pass their tests |
| `4.2` | `scenarios/relay_kill.yaml` | the relay dies. Does the swarm elect, reposition and reconnect inside the derived budget |
| `4.3` | `scenarios/link_loss.yaml` | the relay goes quiet and comes back. Does the epoch owner hand the vehicle back without breaking the link first |
| `4.4` | `scenarios/queue_drain.yaml` | across a full 45 s outage, is every observation delivered exactly once and the backlog drained inside 2.25 s |
| `4.5` | `scenarios/encounter.yaml` | do two vehicles on a converging course yield and stay 10 m apart |
| `4.6` | `scenarios/encounter_noyield.yaml`, the control | with the yield rule off, do they actually violate separation |
| `4.7` | `scenarios/mission_integrated.yaml` | do all of it at once. This is the proof-of-concept run the proposal is built on |
| `4.8` | the freeze, the archive install on a clean target, and the package | does what we are about to send install and fly somewhere that is not this machine |

`4.3` and `4.4` are the two halves of the fault the organisers name twice, in the challenge statement and again in the FAQ: the swarm reconfigures as UAVs **fail or lose connectivity**. A dead vehicle and a quiet one are different problems, because the quiet one is still in the air and it comes back.

`4.6` is a control in the same way `3.5` is. A separation monitor that never fires has not been shown to work.

`4.8` is the old W5. Everything in it is automated and tested: `freeze_source.sh`, `fresh_install.sh`, `check_submission.py` and `test_submission_fixtures.py`. What is left for a human is the final read of the proposal, the video cut, and sending the email.

**Fallback, if the role-release half does not land.** Run `link_loss` with the release rule disabled and claim the routing recovery only, which is still the named failure demonstrated. Say so in the proposal rather than implying more. Time comes from W2's slack under standing rule 6, never from W3.

Gate: `bash scripts/gate.sh 4`

`check_submission.py` exits 2 when the package is complete but unsent, and the loop halts there. Sending is a human step, so "ready" and "submitted" are deliberately different states.

### What ships, and where each piece was written

Nothing here is authored in the last two days. Each row lands in the week that produced the evidence behind it.

| Deliverable | Written in |
| --- | --- |
| `submission/proposal.pdf`, 6 to 8 pages, citing the run id behind every number | a section a week, W1 to W4 |
| `submission/demo.mp4`, opening on the failure and the recovery | footage a week, cut in `4.8` |
| `submission/INSTALL.md` | W1, then amended by any week that changes the build |
| `submission/uavx-source.zip` and `source-manifest.json`, by `scripts/freeze_source.sh` | `4.8` |
| `submission/fresh-install-receipt.json` and its transcript, by `scripts/fresh_install.sh` | `4.8` |
| `LICENSE` and `THIRD-PARTY.md` inside the archive | done |
| the regulatory compliance section, Drone Rules 2021 and the 2022 and 2023 amendments | W1, since it needs no run |
| `submission/evidence-manifest.json`, naming the exact record behind each scenario | `4.8` |
| `submission/attachment-manifest.json`, hashes and sizes against the delivery budget | `4.8` |
| `submission/sent-receipt.json`, written by the human after sending | after `4.8` |

`check_submission.py` rehashes the archive, checks every file in it against commit `C`, revalidates every named run against the schema and against `uavx_eval.check`, and requires each run's source hash to match the frozen source. `scripts/test_submission_fixtures.py` tampers with each of those in turn and proves the rejection, from a baseline it has first proved the checker accepts.

## Standing rules

1. A week is done when its gate exits 0 in a shell, run after the work.
2. Every number in a document traces to a JSONL under `runs/`. Nothing is quoted from memory.
3. Every run is seeded and replays exactly. A result nobody can reproduce is not evidence.
4. **No week-agent may change a value in [architecture.md](architecture.md) or a threshold in `scripts/gate.sh` to make its own gate pass.** That is moving the goal. If a number is unreachable, stop and report it.
5. Each week drafts its proposal section, cuts its demo footage and amends `INSTALL.md` as its work lands. The tail is a freeze and a send, not four days of authorship.
6. When a week overruns, take days from the phase with the lowest rubric weight that still has slack. Never from W3.
7. A chunk is the unit of work. Finish one, run its gate, then start the next. A week's gate is the acceptance test, not the first feedback.

## Before any physical flight, whenever that comes

Not a Stage 1 item, and written down here so it cannot be skipped later. Stage 1 and Stage 2 fly nothing. Stage 3 is a live demonstration in front of a jury, and the moment anything leaves the ground the position changes completely.

Go or no go, rechecked against current requirements rather than against a paragraph written in September: airspace classification for the site, drone registration, the remote pilot certificate, aircraft type certification, and BVLOS permission. The Drone Rules 2021 and its amendments are the instrument; DGCA and Digital Sky are where the current position is published. No field test starts from what this repository says.

## What this plan does not cover

- Stage 2 and Stage 3. The seam behind the link layer means a UDP shaper can replace the application gate without touching routing, roles or mission code. That is the intended Stage 2 upgrade, along with `comms_blackout` and `gps_degrade`.
- Hardware. There is none in the challenge.

## Before week 1 runs at all

These block. `bash scripts/gate.sh preflight` refuses to start a week without `submission/human-preflight.json`, and every gate begins with preflight, so nothing in the four weeks above happens until they are done.

They used to sit here as a backlog list. Round 3 finding 4 pointed out that an unregistered or ineligible entrant has no valid submission however good the simulation is, and eligibility disqualifies at any stage, including after Stage 1 results. Four weeks of work behind an invalid entry is the worst outcome available and the cheapest one to prevent.

[human-preflight.md](human-preflight.md) has the detail and the receipt to write. In short: register, declare eligibility, join the clarification channel, send the organiser questions, and decide how a package too large to attach actually gets delivered.
