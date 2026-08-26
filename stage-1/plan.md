# UAV-X Stage 1 build plan

**Deadline:** 27 September 2026, by email to pushpak_gc2026@aero.iitb.ac.in. We send on 26 September.
**Capacity:** one person, 4 to 6 hours a day.
**Execution:** five weeks, one per `/loop` tick. Config at [.claude/weekly-loop.md](../.claude/weekly-loop.md).

Official facts live in [context.md](../context.md). Locked calls live in [decisions.md](decisions.md). This file is the week-by-week execution plan, and every week ends in a gate that is a command with an exit code, not an opinion.

## The one thing that shapes everything

60% of the rubric is communication resilience, relay and role management, and fault recovery. 25% is mission completion. 10% is safety.

None of the accepted simulators model radio. PX4, ArduPilot, Gazebo and AirSim let every vehicle talk to every other vehicle at any distance, forever. So the mesh is not configuration, it is the deliverable, and it is most of the marks. Flight is a week; the comms and role layers are two.

## Metric spine

Every rubric line gets a number this repo produces, logged per run, quoted in the proposal. A submission an evaluator can grade in 30 seconds beats one they have to interpret.

| Rubric criterion | Weight | Metric we report | Produced in |
| --- | --- | --- | --- |
| Mission completion | 25% | `coverage_fraction`, survey grid cells visited over total | W2 |
| Communication resilience | 25% | `delivery_ratio` and `delivery_ratio_by_node`, `mean_hop_count`, `max_gcs_outage_s` | W3 |
| Autonomous relay and role management | 20% | `role_changes`, `time_in_connected_topology` | W3, W4 |
| Fault recovery and swarm reconfiguration | 15% | `time_to_reconnect_s` per injected failure | W4 |
| Safety and collision avoidance | 10% | `min_pairwise_separation_m`, `separation_violations` | W4 |
| Innovation and technical merit | 5% | the link model and routing writeup | W5 |

## Architecture

### Runtime topology, one scenario run

- One `gzserver`, Gazebo Classic, N iris models. N is 4.
- N PX4 SITL instances, instance `i` carrying `MAV_SYS_ID = i+1`.
- One `MicroXRCEAgent udp4 -p 8888`.
- PX4 topics arrive under per-vehicle ROS 2 namespaces `/px4_1` to `/px4_4`.
- One GCS node, fixed position, deliberately placed so at least one survey drone cannot reach it directly.

### The seam that makes the comms claim honest

Swarm traffic never travels vehicle to vehicle on ordinary ROS 2 topics. It goes through one gate:

```
node i  --publish-->  /uavx/<i>/tx  -->  [ link layer ]  -->  /uavx/<j>/rx  --subscribe-->  node j
```

Every node reads only its own `rx` and writes only its own `tx`. The link layer is the single place that decides whether a message crosses, and it decides from ground-truth positions. Nothing else in the swarm may subscribe to another vehicle's topics. That rule is what the whole 25% rests on, so it is enforced by a test, not by discipline.

The link layer reads ground-truth positions to decide delivery. That is deliberate and it is not the swarm cheating: the link layer stands in for physics, the way the simulator stands in for air. It is infrastructure sitting outside the swarm, and no swarm node may read from it or from another vehicle's position topics. The proposal has to draw that boundary in as many words, because an evaluator who misses it reads the whole design as omniscient.

Stated plainly in the proposal: MAVLink between PX4 instances still flows unconstrained underneath. Our radio model constrains the swarm's own protocol. Claiming more would not survive someone opening the source.

### Link model

For an ordered pair at 3D distance `d`:

- `d <= r_full`: delivered
- `r_full < d <= r_max`: delivered with probability falling linearly from 1 to 0 across the band
- `d > r_max`: dropped

Defaults `r_full = 300 m`, `r_max = 500 m`, both scenario-configurable. Each delivered message takes a fixed per-hop latency. The RNG is seeded per run from the scenario file, so every result replays exactly.

The model stays this simple on purpose. A clean threshold we can derive and defend beats a half-understood propagation model that an evaluator can poke a hole in.

### Routing

A small link-state protocol, because it is explainable in a paragraph and correct for 4 nodes.

1. Each node broadcasts `HELLO` on a fixed period.
2. Each node keeps a neighbour table, entry expiring after `neighbour_timeout` without a HELLO. Expiry is what makes a dead node visible.
3. Neighbour tables flood as link-state advertisements.
4. Each node runs Dijkstra to the GCS over its view of the graph.
5. Data forwards hop by hop along that path.

### Roles

Three roles, held per vehicle, decided from each node's own view of the graph:

- `SURVEY`, flying its assigned coverage area
- `RELAY`, holding position to bridge a gap
- `GCS_ANCHOR`, the node with a live direct link to the ground station

When a node loses its path to the GCS, the swarm elects the best-placed `SURVEY` drone to become `RELAY` and reposition. Election is deterministic: lowest total repositioning distance, ties broken by lowest system ID.

### Safety

Three layers, in order of how much they earn:

1. Altitude deconfliction. Each vehicle gets a distinct cruise altitude, spaced by `altitude_layer_m`. This alone removes most conflicts.
2. A separation monitor, logging every pair closer than `min_separation_m`.
3. A yield rule. The higher system ID holds position when predicted separation drops below the threshold.

### Repository layout

```
uavx_ws/src/
  uavx_msgs/       LinkState, SwarmPacket, RoleAssignment, RunMetrics
  uavx_comms/      link model, link layer node, router node
  uavx_mission/    coverage planner, area partitioner, mission executor
  uavx_roles/      role manager, relay election, reposition logic
  uavx_sim/        launch files, world, scenario runner, failure injection
  uavx_eval/       metrics collector, JSONL writer, report generator
  uavx_gcs/        ground station node
scenarios/         YAML scenario definitions
scripts/           run_smoke.sh, run_scenario.sh, fresh_install_test.sh
stage-1/setup/     provisioning, done in W1
docs/              progress, audits, decisions, journal
```

`uavx_ws` overlays the third-party workspace at `~/ws_uavx` that holds px4_msgs and px4_ros_com. Our code lives in the repo and gets committed; theirs does not.

## The five weeks

Each week states its goal, what it must produce, and the gate. The gate is the contract. A week is done when its gate commands exit 0, and not before.

### W1, 26 August to 1 September. Environment and four vehicles airborne

Goal: four PX4 vehicles flying at once, ROS 2 seeing all of them, reproducible from one command.

Produces:

- `stage-1/setup/` provisioning, verified on this machine
- `uavx_ws/src/uavx_sim/` with a launch that brings up N vehicles, the agent and the GCS
- `scripts/run_smoke.sh`, which arms all N, takes off, holds 20 s, lands

Gate:

```bash
bash stage-1/setup/verify.sh                 # exits 0
bash scripts/run_smoke.sh --vehicles 4       # exits 0
```

`run_smoke.sh` must fail loudly if fewer than 4 vehicles reach 4 m altitude. Runs headless, so it works with no display.

Fallback if the gate fails on 1 September: drop to 2 vehicles and carry the plan forward unchanged. If the blocker is WSLg rather than PX4, stay headless and book one GUI run in W5 for the video.

### W2, 2 to 8 September. Survey mission and the coverage metric

Goal: all four fly a coverage pattern over a defined area, and the run reports how much of it was covered.

Produces:

- `uavx_mission`: a boustrophedon coverage planner over a rectangular area, an area partitioner splitting it into N disjoint strips, a mission executor flying the assigned strip
- `uavx_eval`: a metrics collector writing one JSONL per run, carrying `coverage_fraction`
- `scenarios/survey_baseline.yaml`
- `scripts/run_scenario.sh`

Gate:

```bash
colcon test --packages-select uavx_mission uavx_eval   # exits 0
bash scripts/run_scenario.sh scenarios/survey_baseline.yaml   # exits 0
python3 -m uavx_eval.check runs/latest.jsonl --require "coverage_fraction>=0.95"
```

Unit tests must cover: the partitioner produces disjoint strips whose union is the whole area, and the planner's path visits every grid cell of its strip.

Fallback: cut the area, not the metric. A smaller box covered fully beats a big one covered partly, because the number is what gets reported.

### W3, 9 to 15 September. The comms layer

Goal: swarm messages reach the GCS only through the modelled radio, and at least one drone reaches it only by relay.

Produces:

- `uavx_msgs`
- `uavx_comms`: the link model, the link layer node holding the tx/rx seam, the link-state router
- `scenarios/relay_required.yaml`, GCS placed so `uav_4` is outside `r_max` of it
- `scenarios/direct_only.yaml`, the control, routing disabled so only direct links count
- `delivery_ratio`, `mean_hop_count`, `max_gcs_outage_s` in the metrics

Gate:

```bash
colcon test --packages-select uavx_comms uavx_msgs   # exits 0
bash scripts/run_scenario.sh scenarios/relay_required.yaml
python3 -m uavx_eval.check runs/latest.jsonl \
  --require "delivery_ratio>=0.95" \
  --require "delivery_ratio_by_node.uav_4>=0.95" \
  --require "mean_hop_count>1.0"
bash scripts/run_scenario.sh scenarios/direct_only.yaml
python3 -m uavx_eval.check runs/latest.jsonl \
  --require "delivery_ratio_by_node.uav_4<=0.05"
```

That control run is the important half of the gate. Without it, a delivery ratio of 1.0 proves nothing, because it is also what a silently open gate produces. The pair together prove the relay is carrying traffic.

The control asserts on `uav_4` alone rather than on the swarm total. Direct-only delivery across the swarm lands near 0.75 by construction, since three of four drones still reach the GCS without help, so a threshold on the aggregate would fail a correct implementation. Per-node is also the only form of this test that survives a change in the geometry.

Unit tests must cover: the link model is deterministic under a fixed seed, drops everything past `r_max`, delivers everything inside `r_full`; Dijkstra returns the expected path on a hand-built 4 node graph; and a test asserting no swarm node subscribes to another vehicle's topics directly.

Hard stop. This is the submission. If the gate is not green on 15 September, W4 loses days to it rather than the other way round.

### W4, 16 to 22 September. Roles, fault recovery and safety

Goal: kill a relay mid-mission and watch the swarm notice, reassign and rebuild the chain, with nobody touching anything, and without vehicles converging on each other.

Produces:

- `uavx_roles`: role manager, deterministic relay election, reposition logic
- Failure injection in `uavx_sim`, driven by scenario events: `kill`, `comms_blackout`, `gps_degrade`
- Altitude deconfliction, separation monitor, yield rule
- `scenarios/relay_kill.yaml`
- `time_to_reconnect_s`, `role_changes`, `min_pairwise_separation_m`, `separation_violations` in the metrics

Gate:

```bash
colcon test --packages-select uavx_roles uavx_sim   # exits 0
bash scripts/run_scenario.sh scenarios/relay_kill.yaml
python3 -m uavx_eval.check runs/latest.jsonl \
  --require "time_to_reconnect_s<=30" \
  --require "delivery_ratio_after_recovery>=0.90" \
  --require "separation_violations==0" \
  --require "role_changes>=1"
```

The 30 second reconnect budget is not a free parameter. It has to cover the neighbour timeout before the loss is even visible, then the election, then the elected drone flying to its new position. At 10 m/s a 200 m reposition is 20 s on its own. Derive the budget from those three numbers in W4 and move the threshold if the arithmetic says to. Failing a correct implementation against a figure nobody checked is worse than having no gate.

Fallback: if autonomous election will not converge, fall back to a deterministic priority list computed once at startup. It still recovers, it still demos, and the proposal describes what it is rather than overselling it.

### W5, 23 to 26 September. Proposal, video, package, submit

Goal: five deliverables in one email, sent a day early.

Produces:

- `submission/proposal.pdf`, 6 to 8 pages. Architecture, link model, routing, role management and the metric table get the space. One page on flight is plenty.
- `submission/demo.mp4`. Open on the failure and the recovery. Do not narrate the setup.
- `submission/INSTALL.md`, derived from `stage-1/setup/README.md` and tested from nothing.
- Source, as a repo link and a zip, covering both readings of the unanswered format question.
- `submission/CHECKLIST.md`

Gate:

```bash
python3 scripts/check_submission.py     # exits 0
```

That script verifies: the PDF exists and its page count is 6 to 8; the video exists and runs 180 s or less; `INSTALL.md` exists; the source archive exists; and `CHECKLIST.md` lists all five deliverables as present. `scripts/fresh_install_test.sh` must have passed once against a clean distro.

Send 26 September. Email submission gives no upload confirmation and no portal to check, so the spare day is the whole safety margin.

## Standing rules for every week

1. No week is done until its gate commands exit 0 in a shell, run after the work, not before.
2. Every scenario run writes a JSONL to `runs/`. Metrics are never quoted from memory.
3. Every run is seeded and replayable. A number nobody can reproduce is not evidence.
4. Failure injection is built in W4 and used from then on, not bolted on at the end. Stage 2 hands over hidden disturbances, and a swarm that only works on the happy path scores nothing there.
5. When a week overruns, take the days from the phase with the lowest rubric weight that still has slack. Never from W3.

## What this plan does not cover

- Stage 2 and Stage 3. The seam left behind the link layer means a UDP shaper can replace the application gate later without touching the routing, roles or mission code. That is the intended Stage 2 upgrade and it is deliberately not started now.
- Anything needing hardware. There is none in the challenge.

## Open items, not blocking

- Register on techfest.org, Competitions, PUSHPAK Grand Challenge
- Join the UAV-X WhatsApp group, where clarifications land first
- Send the organiser email, drafted in [organiser-email.md](organiser-email.md)
- Confirm no attachment to the PUSHPAK project, the Drone Centre or the organising institutions, which disqualifies at any stage
