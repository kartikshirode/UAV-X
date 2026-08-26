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
| `r_full` | 200 m |
| `r_max` | 250 m |
| per-hop latency | 20 ms |
| RNG | seeded per run from the scenario file |

Links are evaluated per direction and per message, from ground-truth positions sampled at 20 Hz.

**How the numbers were chosen.** Two constraints, in order. First, a fade band cannot carry a link the gates depend on: two lossy hops need `p^2 >= 0.95`, so each hop needs `p >= 0.9747`, which lands within a few metres of `r_full` and no moving vehicle holds that. So every asserted link sits in the full band and the fade band exists only to be exercised and reported. Second, the whole geometry scales with the radio, and a smaller radio means shorter recovery flights and a shorter demo. 200 and 250 keep the topology in section 6 and put the relay flight at 191.6 m, which is 19.2 s rather than 38.

### The placement rule

Every scenario obeys both:

- A link the routing **must** use is at most **175 m**, so it sits 25 m inside `r_full`.
- A link that **must not** exist is at least **300 m**, so it sits 50 m beyond `r_max`.

No pair may fall between those two figures. A pair in the gap makes a code failure and an unlucky draw produce the same result, which is untestable. `scripts/check_geometry.py` enumerates every pair and enforces it.

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

### Who elects

Round 3 finding 1 broke the previous rule. It made the coordinator "the lowest id with a route to `gcs`", but in the frozen topology the only node left with a route after the relay dies is the anchor, and the anchor is never eligible to move. Nobody could have opened an election.

**The disconnected component elects its own relay.** That is also the more honest reading of "reconfigures itself as drones fail": the part of the swarm that lost contact is the part that has to solve it.

1. A node whose route to `gcs` has been absent for `neighbour_timeout` marks itself `DISCONNECTED` and floods that state within its own component.
2. The component's coordinator is its **lowest id member**. Every member computes the same one because they all see the same component.
3. The **attachment node** is the last node the component could reach that still had a route to `gcs`, taken from the last link-state view before the split. In the frozen scenario that is `uav_1`.
4. Eligible movers are `SURVEY` members of the component. A `GCS_ANCHOR` is never eligible.
5. The mover is the eligible member **nearest the attachment node**, ties broken by lowest id.

### Relay slots

The slot is the midpoint between the **attachment node** and the **member that stays**, both of which are stationary at the moment of computation. The previous rule said "nearest connected node", which in this topology resolved to the moving candidate itself and was circular.

If either half of that midpoint exceeds 175 m the component reports `RELAY_INFEASIBLE` rather than sending someone to an impossible place. Positions travel in `HELLO` and in role messages; LSAs carry neighbour tables only.

### Election

1. Coordinator opens epoch `e = e + 1`, broadcasting `ELECTION(e, attachment_id, slot_position)`.
2. Each eligible member replies `BID(e, own_id, distance_to_attachment)`.
3. Coordinator waits `election_window` = 1.0 s, then picks the lowest distance, ties by lowest id.
4. Coordinator sends `ASSIGN(e, winner_id, slot_position)`; the winner replies `ROLE_ACK(e)`.
5. The winner becomes `RELAY` and flies to the slot. **Its survey strip is handed to the members that stay, not abandoned.**
6. The role is held on a 30 s lease, renewed each `LSA` period. An expired lease reverts the node to `SURVEY`, so a dead coordinator cannot strand a vehicle.
7. Recovery is complete when the component has had a route to `gcs` for `stability_window` = 3.0 s continuously.

Elections are idempotent per epoch, and a node ignores `ASSIGN` for an epoch older than the highest it has seen.

### Derived reconnect budget

Round 2 finding 6 is right that 30 s was asserted, not derived. For the frozen `relay_kill` geometry:

| Term | Value | Where from |
| --- | --- | --- |
| Detect the loss | 3.0 s | `neighbour_timeout` |
| LSA convergence | 2.0 s | one `LSA` period |
| Election | 1.0 s | `election_window` |
| Fly to slot | 19.2 s | 191.6 m at 10 m/s |
| Accelerate and settle | 4.0 s | allowance |
| Stability window | 3.0 s | `stability_window` |
| **Total** | **32.2 s** | |

The gate is **45 s**, the derived total plus 40% margin. It is not 30 s, because 30 s fails a correct implementation. `scripts/check_geometry.py` recomputes every term from the frozen coordinates, so the table cannot drift away from the geometry it describes.

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

All positions metres, GCS at the origin. Every figure below is recomputed by `scripts/check_geometry.py`, which enumerates all ten pairs rather than the handful an author thought to list. Round 3 finding 1 exists because the previous version did the latter and missed two full-band links that made the whole relay-kill scenario meaningless.

| Node | Role at start | Position | Cruise altitude |
| --- | --- | --- | --- |
| `gcs` | ground station | (0, 0, 0) | fixed |
| `uav_1` | `GCS_ANCHOR` | (165, 0, 30) | 30 |
| `uav_2` | `RELAY` | (330, 0, 40) | 40 |
| `uav_3` | `SURVEY` | (475, 75, 50) | 50 |
| `uav_4` | `SURVEY` | (475, -75, 60) | 60 |

The complete distance matrix, all ten pairs:

| Pair | Distance | Band | Link |
| --- | --- | --- | --- |
| `gcs` to `uav_1` | 167.7 m | full | yes |
| `uav_1` to `uav_2` | 165.3 m | full | yes |
| `uav_2` to `uav_3` | 163.6 m | full | yes |
| `uav_2` to `uav_4` | 164.5 m | full | yes |
| `uav_3` to `uav_4` | 150.3 m | full | yes |
| `gcs` to `uav_2` | 332.4 m | out | no |
| `uav_1` to `uav_3` | 319.6 m | out | no |
| `uav_1` to `uav_4` | 320.4 m | out | no |
| `gcs` to `uav_3` | 483.5 m | out | no |
| `gcs` to `uav_4` | 484.6 m | out | no |

So the graph is a chain, `gcs - uav_1 - uav_2 - {uav_3, uav_4}`, with `uav_3` and `uav_4` also linked to each other. Both survey drones reach the GCS **only** through `uav_2`, which is what makes killing it an actual event.

The `uav_3` to `uav_4` link is load-bearing in a way that is easy to miss: it is what lets the disconnected side run an election at all. Without it the two survey drones could not agree on anything after the split.

### `survey_baseline.yaml`

| Parameter | Value |
| --- | --- |
| Survey area | 200 m by 200 m, south-west corner at (375, -100) |
| Grid cell | 10 m by 10 m, so 400 cells |
| Sensor footprint | 12 m radius disc, centred under the vehicle |
| Vehicles surveying | 4, area split into 4 vertical strips of 50 m |
| Cruise speed | 10 m/s |
| Run duration | 420 s |
| Coverage source | **sampled vehicle poses at 20 Hz**, never the planned path |

`coverage_fraction` is cells whose centre fell inside the footprint of any pose sample, over 400. Communications are disabled here so the scenario measures the mission and nothing else.

The box is frozen. If coverage cannot reach the gate in 420 s the planner is wrong, and shrinking the box would be moving the goal rather than fixing the code.

### `mission_integrated.yaml`

Round 3 finding 5: every other scenario proves one subsystem in isolation, and the official task couples them. A demo of stationary dots recovering a synthetic packet stream is not a disaster-response swarm. This is the scenario the proposal and the video both point at, and it is the proof of concept the five deliverables describe.

It runs the whole task at once: survey, relay the observations home, lose the relay, reconfigure, finish the mission.

| Parameter | Value |
| --- | --- |
| Survey area | 50 m by 210 m, south-west corner at (405, -105) |
| Grid cell | 10 m by 10 m, so 105 cells |
| Surveying vehicles | `uav_3` and `uav_4`, two strips of 25 m |
| Static roles | `uav_1` anchor, `uav_2` relay, both station-keeping |
| Observation packets | 5 Hz per surveying vehicle |
| Kill | `uav_2` at `t = 150 s` |
| Run duration | 480 s |

The box is not a free choice, it is the largest area that satisfies two hard constraints at once, computed rather than picked: every point must stay within 175 m of `uav_2` so observations have a live route home before the kill, and at least 250 m from `uav_1` so the kill actually disconnects the surveyors. At the worst corner those come out at 164.5 m and 262.7 m, leaving about 10 m of margin on each.

That box is small, and saying why is worth a paragraph of the proposal. **The area a swarm can survey behind a relay is bounded by radio geometry, not by battery or flight time.** That is the actual engineering trade-off this challenge is about, and a submission that states it with numbers is making the point the rubric rewards rather than hiding a limitation.

After the kill the surveyors are disconnected exactly as in `relay_kill`, `uav_3` becomes the relay and flies to the slot, and `uav_4` inherits `uav_3`'s strip. Observations generated during the outage are **buffered and delivered once the route returns**, which is what a real BVLOS mission does, and it is why the gate asserts delivery by end of run rather than instantaneously.

What this scenario has to show, and what its gate asserts:

- the survey completes despite losing a vehicle to the relay role
- observations generated during the outage arrive after recovery, none silently dropped
- reconnection happens inside the derived budget
- no separation violation across the whole 480 s

### `relay_required.yaml`

Common geometry, station-keeping, no survey motion. Routing enabled. 240 s. Each node sends 5 application packets per second to `gcs`, so `uav_4` sends 1200, well past the 100 minimum the gate requires.

### `direct_only.yaml`

Identical to `relay_required.yaml` with forwarding disabled, so only direct links deliver. `uav_4` is 981.8 m from the GCS, beyond `r_max`, so its delivery must be exactly 0. This is the control that proves the relay is doing the work.

### `relay_kill.yaml`

Common geometry. At `t = 120 s`, `uav_2` is killed. Both survey drones lose their route; `uav_1` keeps the GCS link.

The component `{uav_3, uav_4}` elects. Distances to the attachment node `uav_1` are 319.6 m and 320.4 m, so `uav_3` wins by 0.8 m. That margin is small but it is deterministic and reproducible, not a coin flip; the tie-break by lowest id would pick `uav_3` regardless.

The slot is the midpoint of `uav_1` at (165, 0, 30) and `uav_4`, the member that stays, at (475, -75, 60), which is **(320, -37.5, 45)**.

- `uav_3` flies (475, 75, 50) to (320, -37.5, 45): **191.6 m**, 19.2 s at 10 m/s.
- Both new hops are 160.2 m, inside `r_full`.
- Restored path: `uav_4 -> uav_3 -> uav_1 -> gcs`.
- `uav_3`'s survey strip is reassigned to `uav_4`, not abandoned.

Run duration 300 s, leaving 180 s after the kill for recovery and steady state.

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
