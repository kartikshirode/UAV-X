# UAV-X architecture, frozen

Every number here is fixed before implementation starts. Round 2 of the review found that the plan described intent and left the week-agent to invent a routing protocol, an election and a set of thresholds under deadline. Two competent implementations would have behaved differently and neither would have been wrong.

**Nothing in this file may be changed by a week-agent trying to pass a gate.** Changing a threshold to make a run go green is moving the goal, not fixing the code. If a number here turns out to be unreachable, that is a finding for the human, and the fallbacks in [decisions.md](decisions.md) say what happens next.

## Coordinate frame and units

Local ENU metres, origin at the GCS. `x` east, `y` north, `z` up. Distances are 3D. Time in seconds. Angles in degrees.

## 1. The tx/rx seam

Every swarm message crosses exactly one boundary:

```
node i  --publish-->  /uavx/<id>/tx  -->  [ link layer ]  -->  /uavx/<id>/rx  --subscribe-->  node j
```

`<id>` is one of `uav_1`, `uav_2`, `uav_3`, `uav_4`, `gcs`. The GCS has a modelled endpoint like every other node; round 2 found that leaving it out invited an implementer to wire it straight to every router.

### Endpoint allowlist

A swarm process may hold exactly these endpoints and no others:

| Process | May publish | May subscribe |
| --- | --- | --- |
| `uavx_comms/router` (per node) | `/uavx/<own>/tx` | `/uavx/<own>/rx` |
| `uavx_mission`, `uavx_roles` (per node) | `/uavx/<own>/tx` | `/uavx/<own>/rx` |
| any per-node process | `/<own_px4_ns>/...` | `/<own_px4_ns>/...` |
| `uavx_gcs/gcs_node` | `/uavx/gcs/tx` | `/uavx/gcs/rx` |
| `uavx_comms/link_layer` | every `/uavx/*/rx` | every `/uavx/*/tx`, all ground-truth poses |
| `uavx_eval/metrics_collector` | nothing | ground truth, and its own metrics topics |

The link layer and the metrics collector sit **outside** the swarm. They represent physics and the observer. That is why they may read ground truth and nothing else may.

### What the seam test must catch

A source grep alone misses launch remaps and names built at runtime. A graph check alone misses code paths that never ran. `scripts/check_seam.sh` does both, and fails on any of:

1. A swarm process publishing or subscribing to a topic outside its allowlist row, after remaps are resolved.
2. Any service or action between two swarm nodes.
3. A swarm process subscribing to a `/px4_*` namespace that is not its own.
4. Any module other than `uavx_comms.link_layer` or `uavx_eval` importing the ground-truth pose interface.
5. Any process other than the link layer creating an endpoint for more than one vehicle id.
6. A `SwarmPacket` seen on any topic outside the `/uavx/*/tx` and `/uavx/*/rx` set.

It runs against a live graph in both W3 scenarios, plus a static pass over `uavx_ws/src`.

## 2. Link model

For an ordered pair at 3D distance `d`:

| Band | Condition | Delivery |
| --- | --- | --- |
| Full | `d <= r_full` | delivered, probability 1 |
| Fade | `r_full < d <= r_max` | delivered with `p = (r_max - d) / (r_max - r_full)` |
| Out | `d > r_max` | dropped, probability 0 |

| Parameter | Value |
| --- | --- |
| `r_full` | 400 m |
| `r_max` | 500 m |
| per-hop latency | 20 ms |
| RNG | seeded per run from the scenario file |

Links are evaluated per direction and per message, from ground-truth positions sampled at 20 Hz.

**Why 400 and 500 rather than 300 and 500.** Round 2 did the arithmetic the plan had not. In a fade band, two lossy hops need `p^2 >= 0.95`, so each hop needs `p >= 0.9747`. With a 200 m band that puts every usable link within 5 m of `r_full`, which no moving vehicle can hold. A 100 m band plus the rule below keeps every link the routing depends on out of the fade region entirely, so end-to-end delivery is deterministic and a failure means the code is wrong rather than the geometry unlucky.

### The placement rule

Every scenario obeys both:

- A link the routing **must** use is at most **350 m**, so it sits 50 m inside `r_full`.
- A link that **must not** exist is at least **600 m**, so it sits 100 m beyond `r_max`.

Nothing a gate asserts on may sit in the fade band. The fade band exists to be exercised and reported, not to be depended on.

## 3. Routing

Link-state, chosen because it fits in a paragraph of the proposal and is exactly correct for five nodes.

| Parameter | Value |
| --- | --- |
| `HELLO` period | 1.0 s |
| `neighbour_timeout` | 3.0 s, three missed HELLOs |
| `LSA` period | 2.0 s, and immediately on any neighbour change |
| `lsa_seq` | 32-bit, per originator, higher wins, duplicates dropped |
| `lsa_ttl` | 4 hops |
| link symmetry | a link counts only if both directions have a live HELLO |
| route hysteresis | a new route must win for 2 consecutive computations before it replaces the current one |
| application packet rate | 5 Hz per node |

Each node floods its neighbour table, builds a graph, and runs Dijkstra to `gcs` with hop count as cost. Data forwards hop by hop. A node with no route buffers up to 50 packets and drops oldest first.

Hysteresis and the symmetry rule exist to stop route flapping when a link sits near a band edge.

## 4. Roles and the recovery state machine

Three roles: `SURVEY`, `RELAY`, `GCS_ANCHOR`. Round 2 finding 8 is right that "the swarm elects" is not a specification, so here is the machine.

### Coordinator

The node with the **lowest id that currently has a route to `gcs`** is the coordinator. It is the only node that may open an election. A node with no route to `gcs` never coordinates, which resolves the split-view problem: the disconnected side cannot elect anything, and does not need to.

### Relay slots

A relay slot is a **computed midpoint**, not a free choice. When the coordinator sees that node `X` has no route to `gcs`, it computes the slot as the midpoint between `X`'s last known position and the position of the connected node nearest to `X`. If that midpoint is further than 350 m from either end, the topology needs two relays and the coordinator reports `RELAY_INFEASIBLE` rather than sending someone to an impossible place.

Positions travel in `HELLO` and in role messages. LSAs carry neighbour tables only, so the plan's earlier claim that a node could score repositioning distance from LSAs was wrong.

### Election

1. Coordinator opens epoch `e = e + 1`, broadcasting `ELECTION(e, slot_position, disconnected_id)`.
2. Eligible candidates are `SURVEY` nodes with a live route to the coordinator. `GCS_ANCHOR` is never eligible; losing the anchor loses everyone.
3. Each candidate replies `BID(e, own_id, distance_to_slot)`.
4. Coordinator waits `election_window` = 1.0 s, then picks the lowest `distance_to_slot`, breaking ties by lowest id.
5. Coordinator sends `ASSIGN(e, winner_id, slot_position)`. The winner acknowledges with `ROLE_ACK(e)`.
6. Winner switches to `RELAY` and flies to the slot. Its survey area is marked unassigned.
7. The role is held on a **lease** of 30 s, renewed by the coordinator each `LSA` period. A relay whose lease expires reverts to `SURVEY`, so a dead coordinator cannot strand a vehicle.
8. Recovery is complete when the disconnected node has had a route to `gcs` for `stability_window` = 3.0 s continuously.

Elections for the same epoch are idempotent. A node ignores `ASSIGN` for an epoch older than the one it has seen.

### Derived reconnect budget

Round 2 finding 6 is right that 30 s was asserted, not derived. For the frozen `relay_kill` geometry:

| Term | Value | Where from |
| --- | --- | --- |
| Detect the loss | 3.0 s | `neighbour_timeout` |
| LSA convergence | 2.0 s | one `LSA` period |
| Election | 1.0 s | `election_window` |
| Fly to slot | 20.1 s | 200.6 m at 10 m/s |
| Accelerate and settle | 4.0 s | measured allowance |
| Stability window | 3.0 s | `stability_window` |
| **Total** | **33.1 s** | |

The gate is **45 s**, which is the derived total plus roughly 35% margin. It is not 30 s, because 30 s fails a correct implementation.

## 5. Safety

| Parameter | Value |
| --- | --- |
| `min_separation_m` | 10 m |
| `altitude_layer_m` | 10 m, so uav_1 at 30, uav_2 at 40, uav_3 at 50, uav_4 at 60 |
| `yield_horizon_s` | 4.0 s |
| cruise speed | 10 m/s |

Three layers: distinct cruise altitudes, a separation monitor sampling at 20 Hz, and a yield rule. When predicted separation within `yield_horizon_s` drops below `min_separation_m`, the vehicle with the **higher** system id holds position until the predicted violation clears.

Relative position and velocity for the predictor come from `HELLO` messages, which means they are **late by up to one HELLO period plus link latency**. The predictor uses the timestamp in the message, not arrival time, and extrapolates. Safety therefore degrades when the link degrades, and the proposal should say so rather than pretend the swarm has perfect state.

Round 2 finding 9 is right that altitude layers alone would keep everything apart and leave the yield rule dead code. `scenarios/encounter.yaml` exists to force it: two vehicles are commanded to the same altitude on converging paths, so the rule must act.

## 6. Frozen scenario geometry

All positions in metres, GCS at the origin. Distances chosen against the placement rule in section 2.

### Common

| Node | Role at start | Position | Cruise altitude |
| --- | --- | --- | --- |
| `gcs` | ground station | (0, 0, 0) | fixed |
| `uav_1` | `GCS_ANCHOR` | (330, 0, 30) | 30 |
| `uav_2` | `RELAY` | (660, 0, 40) | 40 |
| `uav_3` | `SURVEY` | (640, 200, 50) | 50 |
| `uav_4` | `SURVEY` | (980, 0, 60) | 60 |

Resulting link distances:

| Pair | Distance | Band | Used by routing |
| --- | --- | --- | --- |
| `gcs` to `uav_1` | 331.4 m | full | yes |
| `uav_1` to `uav_2` | 330.2 m | full | yes |
| `uav_2` to `uav_4` | 320.6 m | full | yes |
| `uav_2` to `uav_3` | 201.2 m | full | yes |
| `gcs` to `uav_2` | 661.2 m | out | no |
| `gcs` to `uav_3` | 672.4 m | out | no |
| `gcs` to `uav_4` | 981.8 m | out | no |
| `uav_1` to `uav_4` | 650.7 m | out | no |

So `uav_4` reaches the GCS only as `uav_4 -> uav_2 -> uav_1 -> gcs`, three hops, every hop deterministic. With no relay it cannot reach the GCS at all. That is what makes the W3 gate pair meaningful.

### `survey_baseline.yaml`

| Parameter | Value |
| --- | --- |
| Survey area | 400 m by 400 m, south-west corner at (400, -200) |
| Grid cell | 20 m by 20 m, so 400 cells |
| Sensor footprint | 25 m radius, disc, centred under the vehicle |
| Vehicles surveying | 4, area split into 4 vertical strips of 100 m |
| Cruise speed | 10 m/s |
| Run duration | 420 s |
| Coverage source | **sampled vehicle poses at 20 Hz**, never the planned path |

`coverage_fraction` is cells whose centre fell inside the footprint of any pose sample, over 400. Comms are disabled in this scenario so it measures the mission and nothing else.

Round 2 is right that "shrink the box until it passes" is moving the goal. The box is frozen here. If coverage cannot reach 0.95 in 420 s, the mission planner is wrong and that is the finding.

### `relay_required.yaml`

Common geometry, station-keeping, no survey motion. Routing enabled. 240 s. Each node sends 5 application packets per second to `gcs`, so `uav_4` sends 1200, well past the 100 minimum the gate requires.

### `direct_only.yaml`

Identical to `relay_required.yaml` with forwarding disabled, so only direct links deliver. `uav_4` is 981.8 m from the GCS, beyond `r_max`, so its delivery must be exactly 0. This is the control that proves the relay is doing the work.

### `relay_kill.yaml`

Common geometry. At `t = 120 s`, `uav_2`, the relay, is killed. `uav_3` is the only eligible `SURVEY` node with a route to the coordinator, and the computed slot is the midpoint of `uav_1` at (330, 0, 30) and `uav_4` at (980, 0, 60), which is **(655, 0, 45)**.

- `uav_3` flies (640, 200, 50) to (655, 0, 45): **200.6 m**, 20.1 s at 10 m/s.
- New chain: `uav_4 -> uav_3 -> uav_1 -> gcs`, hops of 325.3 m each, both inside `r_full`.

Run duration 300 s, so there are 180 s after the kill to observe recovery and steady state.

### `encounter.yaml`

`uav_3` and `uav_4` are commanded to the same altitude, 45 m, on paths that cross at (700, 0) at the same time. Without the yield rule they pass within 2 m. With it, `uav_4`, the higher id, holds until the conflict clears. The gate requires at least one logged yield event naming `uav_4`, and zero separation violations.

## 7. Run record contract

Round 2 finding 5: nothing tied `runs/latest.jsonl` to the run that had just happened, so a stale green file survived a crashed simulation and a checker could be satisfied by a metrics writer that never saw a vehicle.

Every run writes `runs/<run_id>.jsonl`. `runs/latest.jsonl` is written **only after every process has exited cleanly**, by atomic rename, never by append. The gate deletes it before launching, so an absent file is a failure rather than a stale pass.

Required provenance fields, all validated before any metric is read:

| Field | Meaning |
| --- | --- |
| `run_id` | unique per run |
| `scenario_path` | must equal the scenario the gate asked for |
| `scenario_sha256` | hash of the scenario file as read |
| `seed` | RNG seed, from the scenario |
| `commit_sha` | repo HEAD at launch |
| `started_at`, `ended_at` | wall clock |
| `completion` | `complete`, or the run is rejected |
| `vehicle_ids_observed` | must match the scenario's vehicle list |
| `pose_sample_count` | zero means nothing was watched |
| `app_packets_sent_by_node` | denominators, per node |
| `app_packets_delivered_by_node` | numerators, per node |
| `injected_events` | each with requested and observed timestamp |
| `versions` | the contents of `stage-1/setup/versions.lock` at run time |

`uavx_eval.check` rejects the file if the scenario does not match, an expected event never fired, a denominator is zero, `completion` is not `complete`, or the file predates the launch. A metric can only be trusted after its provenance is.

## 8. What each rubric row is actually earning

| Criterion | Weight | Evidence artifact |
| --- | --- | --- |
| Mission completion | 25% | `coverage_fraction` from pose samples, `survey_baseline` |
| Communication resilience | 25% | `delivery_ratio_by_node.uav_4` in `relay_required` against `direct_only` |
| Autonomous relay and role management | 20% | `relay_role_moved`, the named transition in `relay_kill`, plus the seam test |
| Fault recovery and swarm reconfiguration | 15% | `time_to_reconnect_s` against the derived 45 s budget |
| Safety and collision avoidance | 10% | `yield_events` in `encounter`, `min_pairwise_separation_m`, `collision_contacts` |
| Innovation and technical merit | 5% | this document, and the honest statement of what the link layer does and does not constrain |
