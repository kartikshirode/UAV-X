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

### When it runs, and against what

Round 4 finding 4 found this arranged so it could not do its job in either direction. The live pass required four `role_manager` processes, which W4 introduces, so a correct W3 graph failed it. Then W4 added those processes and never ran the checker again, so the one part of the swarm that could bypass the radio after W3 was accepted was the part nothing ever looked at.

Each scenario now carries its **own exact process manifest**, in `scripts/seam_manifests.json`. A W3 scenario expects routers, mission executors and the GCS node. A W4 scenario expects those plus role managers. A process in the graph and not in that scenario's manifest is a violation, and so is a process in the manifest and not in the graph.

| Week | Pass | Against |
| --- | --- | --- |
| W3 | static | `uavx_ws/src` |
| W3 | snapshot | the graph captured during `relay_required` |
| W4 | static | `uavx_ws/src` again, now that roles code exists |
| W4 | snapshot | the graph captured during `mission_integrated` |

Graph snapshots carry the message **type** on every endpoint, not only the topic name. Without the type there is no way to enforce rule 6, and rule 6 is the one that catches a swarm quietly agreeing on a side channel that happens to be named something innocent.

A snapshot also has to belong to the run it certifies. Round 6 finding 6: the gate deleted `latest.jsonl` before each launch and left `latest-graph.json` alone, so a run that wrote new metrics and missed its graph capture was checked against the previous scenario's graph. The four provenance strings were read, confirmed non-empty, and thrown away without being compared to anything.

Both files are invalidated before launch now, and the graph pass takes `--expect-run`. It matches the snapshot's scenario against the pass being run, its run id and source hash against the run record, its capture time against the run window, and its own sha256 against the `graph_snapshot_sha256` the record carries. Every node in the snapshot also has to name the `ros2 node info` call that produced it, with a zero return code, alongside one successful `ros2 node list`: a capture that never reached a running graph used to write the same file as a swarm where every node was clean.

Outside processes are matched by exact name, `/link_layer`, `/metrics_collector` and `/scenario_runner`. Substring matching let a node called `uav_2/link_layer_helper` inherit the link layer's exemption and read ground truth.

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

**How the numbers were chosen.** Two constraints, in order. First, a fade band cannot carry a link the gates depend on: two lossy hops need `p^2 >= 0.95`, so each hop needs `p >= 0.9747`, which lands within a few metres of `r_full` and no moving vehicle holds that. So every asserted link sits in the full band and the fade band exists only to be exercised and reported. Second, the whole geometry scales with the radio, and a smaller radio means shorter recovery flights and a shorter demo. 200 and 250 keep the topology in section 6 and put the relay flight at 195.0 m, which is 19.5 s rather than 39.

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
| store-and-forward queue | 512 packets per node, oldest dropped first |
| route key | `(hops, temporary relays on the path)`, compared left to right |

Each node floods its neighbour table, builds a graph, and runs Dijkstra to `gcs`. Data forwards hop by hop. A node with no route holds what it cannot send.

Hysteresis and the symmetry rule exist to stop route flapping when a link sits near a band edge.

### Path cost is not just hops

Round 5 finding 1 found the two frozen rules that made the `link_loss` handback impossible. Hop count was the only cost, and a new route had to win twice before replacing the installed one. When `uav_2`'s radio returns, the two candidate routes are:

```
uav_4 -> uav_2 -> uav_1 -> gcs        3 hops
uav_4 -> uav_3 -> uav_1 -> gcs        3 hops, and uav_3 is the temporary relay
```

A tie. A tie never wins twice, so the route through the relay is never replaced, so a release rule phrased as "the component holds a route that does not pass through it" can never fire. Read the other way, the relay leaves anyway and tears down the installed next hop before its replacement is selected, which is the second outage the same design forbids.

So the cost gains one term:

```
key(path) = (hops, temporary relays on the path)
```

Dijkstra compares that pair left to right. `(3, 0)` against `(3, 1)`, so the recovered path wins on its own merits and installs itself normally.

The first version of this was a scalar, hops plus 0.5 per relay, and round 6 finding 9 is right that it only behaves while a path holds at most one relay. Two relays on a three hop path come to 4.0, which ties a four hop path holding none, and at three relays the shorter path loses. Stage 1 never elects a second relay while the first is still held, so none of the seven scenarios would have shown it, and the rule would have gone into the proposal wrong.

A pair needs no weight and cannot be wrong at a larger relay count. Fewer hops always wins. Relay count only ever decides a tie. `check_geometry.py` enumerates every loop-free path, asserts the tie on hop count alone and the strict win on the pair, and then checks the ordering property over every hop count to 7 against every relay count a path that long could carry.

What this expresses is real, not a trick to make one route win: a route as short as another but which ties up a surveyor is worse, and hop count has no way to say so. Fixing it with geometry, by nudging a position until one route happens to be shorter, would have brought the same bug back with the next topology.

### Why the queue is 512 and not 50

Round 4 finding 3 is right, and it is the sharpest kind of finding: two frozen numbers that contradict each other, where a correct implementation of one breaks the other.

The queue was 50 packets. At 5 Hz that holds 10 seconds. The gate allows an outage of up to 45 seconds, so a surveying node that kept working through an outage would have been required by the design to drop about three quarters of its observations, while the same design claimed none were silently dropped. No implementation could satisfy both.

So the queue is sized from the gate rather than from the expected result. 45 seconds at 5 Hz is 225 packets per origin, two survey origins make 450, and a relay carries its own plus everything it holds for others, which is why the figure is 512 rather than 225.

### What "delivered once" means

Round 5 finding 5: the paragraph above sizes a box and stops. It says nothing about what a packet is, when it may be thrown away, how a duplicate is recognised, or how long the backlog takes to clear once the route returns. An implementer under deadline has to invent all four, and every invention satisfies the gate differently.

Every observation carries four fields, which is the smallest identity that makes the claim checkable:

| Field | Why |
| --- | --- |
| `origin_id` | which vehicle saw it |
| `sequence` | monotonic per origin, so a gap is visible |
| `created_at` | when it was observed, not when it was sent |
| `expires_at` | `created_at` plus `observation_lifetime` |

| Parameter | Value |
| --- | --- |
| `observation_bytes` | 256 |
| `forward_rate` | 200 packets per second per node |
| `observation_lifetime` | 300 s |
| retention | until the GCS acknowledges `(origin_id, sequence)` |
| retry | on route recovery, oldest first |
| deduplication | at the GCS, by `(origin_id, sequence)` |

**Priority.** Observations never delay control. `HELLO`, `LSA` and role messages go in a separate queue that is always served first. Without that rule a 450 packet backlog sits in front of the very traffic that would end the outage, and the swarm takes longer to recover the harder it was working.

**Drain time, derived.** A 45 second outage at 5 Hz per origin leaves at most 450 packets. At 200 per second that is **2.25 s** to clear, against a `stability_window` of 3.0 s, so the backlog is gone before recovery is even declared. 256 bytes each makes the whole backlog 115 kB, which no link in this model has trouble with.

The run record carries one `observations` object, and every name below is a field inside it. Round 6 finding 5 found three spellings of these numbers in play at once: flat `observations_generated` here, a nested object in the schema, and older flat names again in the gate. Three shapes for one measurement is a promise that at least two of the readers are checking nothing.

| Field | Meaning |
| --- | --- |
| `generated_ids` | `origin_id:sequence` for every observation the swarm produced |
| `delivered_ids` | `origin_id:sequence` for every observation the GCS accepted, deduplicated |
| `generated`, `unique_delivered` | the sizes of those two sets |
| `duplicated` | arrivals of an id the GCS already held |
| `expired`, `evicted` | dropped for age, dropped for space |
| `peak_queue_depth` | deepest any node's queue got, against 512 |
| `backlog_drain_s` | route restored to backlog empty |
| `control_queue_max_delay_s` | worst time a control message waited behind anything |

The id sets are the point. `generated: 450, unique_delivered: 450` is satisfied by delivering the wrong 450 packets, and counting deliveries alone lets a relay pass by sending the same packet twice. `uavx_eval.check` recomputes `observations_set_equal`, `observations.missing_count` and `observations.unexpected_count` from the two sets, and rejects any record whose own counts contradict its own sets. [RFC 9171](https://www.rfc-editor.org/rfc/rfc9171.html) draws the same line: identity is source plus creation sequence, and delivery is reported against that identity rather than counted as transmissions.

The gate asserts set equality, no unexpected ids, zero expired, zero evicted, a peak depth inside the queue, `backlog_drain_s <= 2.25` and `control_queue_max_delay_s <= 0.05`. That last bound is one in-flight observation at the 200 per second forward rate, 5 ms, with an order of magnitude of margin. The schema requires the whole block for every scenario that produces an outage, through an `if`/`then` pair that `jsonschema_mini.py` now implements rather than ignores.

## 4. Roles and the recovery state machine

Three roles: `SURVEY`, `RELAY`, `GCS_ANCHOR`. Round 2 finding 8 is right that "the swarm elects" is not a specification, so here is the machine.

### Who elects

Round 3 finding 1 broke the previous rule. It made the coordinator "the lowest id with a route to `gcs`", but in the frozen topology the only node left with a route after the relay dies is the anchor, and the anchor is never eligible to move. Nobody could have opened an election.

**The disconnected component elects its own relay.** That is also the more honest reading of "reconfigures itself as drones fail": the part of the swarm that lost contact is the part that has to solve it.

1. A node whose route to `gcs` has been absent for `neighbour_timeout` marks itself `DISCONNECTED` and floods that state within its own component.
2. The component's coordinator is its **lowest id member**. Every member computes the same one because they all see the same component. On opening an epoch that coordinator becomes the epoch owner, and it carries the epoch to its end. See *Who owns the handback*.
3. The **attachment node** is the last node the component could reach that still had a route to `gcs`, taken from the last link-state view before the split. In the frozen scenario that is `uav_1`.
4. Eligible movers are `SURVEY` members of the component. A `GCS_ANCHOR` is never eligible.
5. The mover is the eligible member **nearest the attachment node**, ties broken by lowest id.

### Relay slots

The component has **work** it still has to do. For a station-keeping member that work is a single point, its own position. For a member with a survey assignment it is the assigned area at that member's cruise altitude.

The slot sits on the segment from the **attachment node** to the **centroid of that work**, at the point where the hop back to the attachment node equals the longest hop forward to any point of the work. The relay parks where it balances the two links it has to carry.

When the work is one stationary point the balance point is the midpoint, so `relay_kill` gets the same answer it always did and nothing about that scenario moves.

Round 4 finding 2 is why the rule is stated this way. The old midpoint rule was written against a component whose surviving member stood still, and the integrated mission has one that keeps flying a survey box. Taking the midpoint to wherever the survivor happened to be at election time puts the far corner of that box 181.7 m away, past the 175 m limit, for an area the design elsewhere claims is in range. Balancing against the whole area instead gives 171.3 m and holds for the rest of the mission wherever the survivor goes.

`check_geometry.py` recomputes both figures every run, so the paragraph cannot end up arguing against a problem some other parameter has quietly fixed.

If either hop exceeds 175 m the component reports `RELAY_INFEASIBLE` rather than sending someone to an impossible place. Positions travel in `HELLO` and in role messages; LSAs carry neighbour tables only.

### The slot has to be empty

Balancing the hops answers a routing question and says nothing about airspace. In the integrated geometry it puts the relay **6.8 m** from `uav_2`, well inside the 10 m separation floor, because the best place to stand in a relay chain is roughly where the relay already is.

Nothing caught that, and the reason it did not is worth stating: `uav_2` is dead in every scenario that computes a slot, so the collision cannot happen. That is a property of the scenario list, not of the rule. **A vehicle that loses its radio is still flying**, and the challenge names that failure as often as it names outright loss.

The first fix was to raise the slot only when it collided. Round 5 finding 4 killed that one: no accepted scenario ever triggered it, so nothing proved an implementation would call the rule at all, and the 5 m staleness allowance was never derived. At 10 m/s a silent vehicle covers 30 m during `neighbour_timeout` alone, and far more by the time the relay arrives. 5 m covers half a second of that.

So separation is taken **vertically**, where it does not depend on knowing where anyone drifted.

| Parameter | Value |
| --- | --- |
| `relay_band` | 75 m, and no mission corridor may enter it |
| `slot_clearance` | 15 m |
| raise step, if the band itself is occupied | 5 m |
| ceiling | 95 m, above which `RELAY_INFEASIBLE` |

Every relay slot sits in the band. The mission altitudes are 30, 40, 50 and 60, so 75 clears the highest by 15 m, and `check_geometry.py` fails the design if that gap ever drops below `slot_clearance`. A silent vehicle can drift anywhere horizontally and the separation still holds, as long as it holds altitude, which is the last thing a radio failure touches: PX4 keeps flying whatever the link does.

The slot is still the balance point, now solved on the horizontal with the altitude fixed. It costs a little link budget and buys a guarantee: `relay_kill`'s hops become 163.0 m and the integrated mission's 171.3 m, both inside the 175 m limit.

The alternative was to slide along the segment until the vehicle was clear. On the integrated geometry that needs a 179.1 m anchor hop, which is not a worse answer, it is past the limit and therefore not an answer at all. `check_geometry.py` keeps that comparison as a live assertion so the choice stays justified rather than remembered.

### Giving the vehicle back

Without this a swarm that loses a link once is permanently one vehicle short, which is the wrong answer to a fault that ended. It only ever fires after a connectivity loss rather than a failure, because a dead vehicle does not come back, and that is why `link_loss.yaml` exists.

**Make before break.** The old link is never torn down until its replacement has carried real traffic. Round 5 finding 1 is right that "a route exists that avoids the relay" is not enough on its own: acting on the mere existence of an alternate breaks the installed next hop before the alternate is selected.

1. The epoch owner sees a route to `gcs` from a staying member that does not use the relay. On the route key above it is also the **cheapest** route, so it wins hysteresis and installs itself.
2. The epoch owner sends `PREPARE_RELEASE(e, staying_member, new_path)`. **The relay keeps forwarding.** Nothing has been given up yet.
3. The staying members send observations over the new path.
4. The GCS acknowledges, naming the observation ids it received and the path they arrived on.
5. Only on that acknowledgement does the epoch owner send `RELEASE(e)`.
6. The relay reverts to `SURVEY` and returns to its station or its unfinished survey work.

If no acknowledgement naming the new path arrives within `stability_window`, the release is abandoned and the relay stays where it is. A swarm that keeps a vehicle parked is worse off than one that does not; a swarm that drops the link to recover a vehicle is much worse off than both.

### Who owns the handback

Round 6 finding 7. Everything above said "the coordinator", and the coordinator rule is a property of the disconnected component: its lowest id member. The moment the radio comes back the component is no longer disconnected and there is no component left to take the lowest id of. Three readings were all defensible. Recompute over the merged graph, let the GCS take it, or let the original coordinator keep it, and only one of them makes the frozen release fire.

There is a worse version of the third reading. In `link_loss` the component is `{uav_3, uav_4}`, so its lowest id member is `uav_3`, and `uav_3` is also the member nearest the attachment node, so it is the one elected to fly. If it stayed the owner it would be evaluating routes from itself, and every route from the relay begins at the relay. The condition would never be true.

So ownership is carried, not recomputed, and the owner is never the relay:

- **`epoch_owner(e)` is fixed when the epoch opens** and holds until `RELEASE(e)` or until the epoch is abandoned. Merging graph components does not move it.
- **The owner is the lowest id member of the component that is not the elected relay.** In `link_loss` the lowest id member is `uav_3` and `uav_3` wins the election, so ownership passes to `uav_4`. This is deterministic and every member computes it identically from the same election result.
- **The owner evaluates the cheapest route to `gcs` for each staying member, with the relay barred as an intermediate node.** Not routes from itself, and not the mere existence of an alternate somewhere in the swarm. In `link_loss` that is `uav_4 -> uav_2 -> uav_1 -> gcs`.
- **That route must win two consecutive computations** before `PREPARE_RELEASE`, the same hysteresis every other route change obeys. A path that appears once as `uav_2` comes back into range is not a path to hand a vehicle back on.
- **The owner renews the relay's 30 s lease** each `LSA` period for as long as the epoch is open.

If the epoch owner dies, nothing takes over the open epoch. Two nodes each believing they own epoch `e` could send contradictory `RELEASE`, and a stale release is the outage this whole section exists to prevent. The lease simply stops being renewed, it expires inside 30 s, and the relay reverts to `SURVEY` on its own. Any member still `DISCONNECTED` after that opens epoch `e + 1` under the ordinary election rule. Losing a vehicle's time is recoverable; tearing down a working link on a message from a node that no longer owns the decision is not.

The run record carries `handback.epoch`, `handback.epoch_owner`, `handback.staying_member`, `handback.prepared_path`, `handback.confirmed_observation_id`, `handback.release_sender`, `handback.confirmed_at`, `handback.release_at` and `handback.observation_gap_count`. The gate asserts that exact trace: the owner is `uav_4` and not the relay, the prepared path is the named non-relay path, the confirmation arrived **before** the relay moved, the release came from the owner, and no unique observation went missing across the handover.

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

Round 2 finding 6 is right that 30 s was asserted, not derived. Every term below comes out of the geometry:

| Term | `relay_kill` | `mission_integrated` | Where from |
| --- | --- | --- | --- |
| Detect the loss | 3.0 s | 3.0 s | `neighbour_timeout` |
| LSA convergence | 2.0 s | 2.0 s | one `LSA` period |
| Election | 1.0 s | 1.0 s | `election_window` |
| Fly to slot | 19.5 s | 15.0 s | 195.0 m and 150.0 m at 10 m/s |
| Accelerate and settle | 4.0 s | 4.0 s | allowance |
| Stability window | 3.0 s | 3.0 s | `stability_window` |
| **Total** | **32.5 s** | **28.0 s** | |

One gate value covers both: **45 s**, which is the worse of the two plus 40%. It is not 30 s, because 30 s fails a correct implementation.

The two flights differ because the integrated mission kills the relay while its replacement is already out over the survey box rather than parked at a station-keeping position. Round 4 finding 2 caught the previous version reusing `relay_kill`'s 32.2 s for a scenario whose candidates were moving. `scripts/check_geometry.py` now derives each scenario's budget from its own frozen trajectories, so neither can inherit the other's arithmetic by accident.

Reconnection can be declared while the mover is still in the air. Crossing from 300 m to the slot takes it through the fade band, and a link that comes up there is a real link, so the route can return before the slot is reached. That makes the measured time shorter than the budget, never longer, and it is what `stability_window` and route hysteresis are for.

## 5. Safety

| Parameter | Value |
| --- | --- |
| `min_separation_m` | 10 m |
| `altitude_layer_m` | 10 m, so uav_1 at 30, uav_2 at 40, uav_3 at 50, uav_4 at 60 |
| `yield_horizon_s` | 4.0 s |
| cruise speed | 10 m/s |
| survey speed | 3 m/s |

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

**Round 4 finding 2 rebuilt this entire scenario.** The previous version claimed every survey point sat at least 250 m from `uav_1`. The closest point is not a corner, it is the middle of the near edge, and that was 240.8 m. A forbidden link was sitting inside `r_max`, so whether killing the relay caused an outage at all came down to the seed. The old timing was worse: the lanes added up to about 109 s of flying and the kill was set for 150 s, so the survey would have been finished before anything failed. Both numbers now come out of `scripts/check_geometry.py`, which walks every frozen trajectory at 10 Hz instead of checking a handful of stationary points.

| Parameter | Value |
| --- | --- |
| Survey area | 25 m by 120 m, south-west corner at (465, -60) |
| Grid cell | 5 m by 5 m, so 120 cells |
| Sensor footprint | 6 m radius disc |
| Surveying vehicles | `uav_3` west strip, `uav_4` east strip |
| Lanes | 2 per strip, at x = 468.125 and 474.375, then 480.625 and 486.875 |
| Static roles | `uav_1` anchor, `uav_2` relay, both station-keeping |
| Observation packets | 5 Hz per surveying vehicle |
| Takeoff and settle | `t = 0` to 20 s |
| Ingress to lane start | `t = 20` to 25 s |
| Survey | from `t = 25 s` at 3 m/s |
| Kill | `uav_2` at `t = 70 s` |
| Run duration | 240 s |

#### Why the box is that size and in that place

Two constraints fight each other, and the box is what is left over.

Every survey point has to stay within 175 m of `uav_2`, or the observations have no route home before the kill. Every survey point also has to stay at least 300 m from `uav_1`, or killing `uav_2` leaves the surveyors talking straight to the anchor and the whole failure proves nothing. Since `uav_1` and `uav_2` are themselves only 165.3 m apart, the triangle inequality caps the survey region at 350 m from `uav_1` no matter how the box is drawn. So the usable area is the sliver between 300 m and 350 m from the anchor, and it comes to about 3,000 m².

The worst cases, all of them recomputed by the checker rather than quoted from here: `uav_2` to the far lane peaks at 169.1 m, and `uav_1` to the near lane bottoms out at 303.8 m.

That box is small, and saying so is worth a paragraph of the proposal. **The area a swarm can survey behind a relay is bounded by radio geometry, not by battery or flight time.** That is the actual engineering trade-off this challenge is about, and a submission that states it with numbers is making the point the rubric rewards rather than hiding a limitation.

#### Why the two surveyors fly mirrored

`uav_3` starts its west strip from the north end, `uav_4` its east strip from the south end. At every instant of the survey they sit at equal distance either side of the centre line and exactly 12.5 m apart in x, with `uav_3` always the western one.

That is not decoration. When the relay dies, the component elects the member nearest the attachment node, and mirroring makes `uav_3` nearer by at least 13.0 m for the whole survey rather than by the 0.8 m that separates the two station-keeping candidates in `relay_kill`. On paper 0.8 m is deterministic. In SITL, where position hold carries its own error, it is a coin toss. The checker fails the design if that margin ever drops under 5 m.

#### What happens

At `t = 70 s` each surveyor is 58% of the way through its strip, so there is finished work and unfinished work on both sides of the failure. `uav_2` dies. Both surveyors lose their route; `uav_1` keeps the GCS link.

The component `{uav_3, uav_4}` elects. At the assign, 6 s later, `uav_3` is 311.8 m from `uav_1` and `uav_4` is 325.0 m. `uav_3` wins, flies 150.0 m to the slot at **(330.3, 0, 75)**, and both new hops are 171.3 m.

`uav_4` inherits `uav_3`'s remaining lane, finishes its own strip first, then flies the handover. The survey completes at about `t = 141 s`, inside the 240 s run with 99 s to spare.

Observations generated during the outage are **buffered and delivered once the route returns**, which is what a real BVLOS mission does, and it is why the gate asserts delivery by end of run rather than instantaneously. The outage here is the derived 28.0 s. The queue is sized for 45 s, so this run never comes close to filling it, and `queue_drain.yaml` exists to go there on purpose.

What this scenario has to show, and what its gate asserts:

- the survey completes despite losing a vehicle to the relay role
- coverage was genuinely unfinished when the relay died
- observations generated during the outage arrive after recovery, with zero evictions
- the named role transfer happened and the restored path is two hops
- reconnection happens inside the derived budget
- no separation violation across the whole 240 s

### `relay_required.yaml`

Common geometry, station-keeping, no survey motion. Routing enabled. 240 s. Each node sends 5 application packets per second to `gcs`, so `uav_4` sends 1200, well past the 100 minimum the gate requires.

### `direct_only.yaml`

Identical to `relay_required.yaml` with forwarding disabled, so only direct links deliver. `uav_4` is 484.6 m from the GCS, beyond `r_max`, so its delivery must be exactly 0. This is the control that proves the relay is doing the work.

### `relay_kill.yaml`

Common geometry. At `t = 120 s`, `uav_2` is killed. Both survey drones lose their route; `uav_1` keeps the GCS link.

The component `{uav_3, uav_4}` elects. Distances to the attachment node `uav_1` are 319.6 m and 320.4 m, so `uav_3` wins by 0.8 m. That margin is small but it is deterministic and reproducible, not a coin flip; the tie-break by lowest id would pick `uav_3` regardless.

The slot is the balance point between `uav_1` and `uav_4`, the member that stays, solved in the reserved relay band: **(317.3, -36.8, 75)**.

- `uav_3` flies (475, 75, 50) to (317.3, -36.8, 75): **195.0 m**, 19.5 s at 10 m/s.
- Both new hops are 163.0 m, inside `r_full`.
- Restored path: `uav_4 -> uav_3 -> uav_1 -> gcs`.
- `uav_3`'s survey strip is reassigned to `uav_4`, not abandoned.

Run duration 300 s, leaving 180 s after the kill for recovery and steady state.

### `link_loss.yaml`

The organisers name the failure twice, in the challenge statement and again in the FAQ: the swarm reconfigures as UAVs **fail or lose connectivity**. Stage 2 says the same thing in different words, promising hidden disturbances "such as UAV failures and communication outages". Every scenario above tests the first half. This one tests the second.

A dead vehicle and a quiet one are not the same fault:

| | `relay_kill` | `link_loss` |
| --- | --- | --- |
| `uav_2` after the event | gone | flying, station-keeping, radio gated both ways |
| Occupies airspace | no | yes |
| Comes back | no | yes, at `t = 240 s` |
| What it tests | election, reposition, restore | all of that, plus the slot clearing a live vehicle, plus giving the vehicle back |

Common geometry, identical to `relay_kill` in every respect except the injected event, so the pair differ in one thing and the comparison carries the argument.

| Parameter | Value |
| --- | --- |
| Event | `comms_blackout` on `uav_2` at `t = 120 s` |
| Radio restored | `t = 240 s` |
| Run duration | 360 s |

What happens. `uav_3` and `uav_4` lose their route exactly as in `relay_kill`, elect, and `uav_3` flies 195.0 m to the slot at (317.3, -36.8, 75). That slot sits **52.4 m** from the still-flying `uav_2`, far past the 15 m clearance, because the band puts every relay above every mission corridor. The route returns inside the same 32.5 s budget.

At `t = 240 s` the radio comes back, and this is where round 5 finding 1 landed. On hop count the recovered route and the relay route both cost three, a tie never wins hysteresis, and the relay could never be released. The route key in section 3 breaks it: `(3, 0)` against `(3, 1)`, so `uav_4 -> uav_2 -> uav_1 -> gcs` installs itself on its own merits.

Then the handback runs make before break. `uav_3` keeps forwarding while observations go over the new path, the GCS acknowledges naming that path, and only then does `uav_3` fly home. The old link is live throughout, so there is no second outage. Its flight out and back never comes within 38.1 m of another vehicle.

**The reconfiguration here is the role, not the route**, and that is the more interesting half. It is also the only scenario in which the swarm gets a vehicle back.

### `queue_drain.yaml`

Round 6 finding 5. The store-and-forward queue is 512 packets, and the 2.25 s drain bound is arithmetic off a 45 second outage: two disconnected origins at 5 Hz for 45 s is 450 packets, and 450 at the 200 per second forward rate is 2.25 s. The two accepted recoveries last 32.5 s and 28.0 s. Nothing had ever held the route down for as long as the numbers assume, so the depth the design claims to survive was never reached by anything that could fail.

Same common geometry and the same fault as `link_loss`, with one difference: the radio never comes back. `uav_2` goes quiet at `t = 60 s` and stays quiet, and the scenario forbids the election, so `uav_3` and `uav_4` buffer for the full 45 s before the route is restored by hand at `t = 105 s`.

| Field | Value |
| --- | --- |
| Fault | `uav_2` radio off at `t = 60 s` |
| Relay election | disabled, so nothing shortens the outage |
| Route restored | `t = 105 s`, a 45 s hold |
| Run duration | 180 s |

What it has to show: 450 observations generated, the delivered set equal to the generated set, nothing expired, nothing evicted, a peak queue depth between 450 and 512, the backlog cleared within 2.25 s, and no control message delayed more than 50 ms behind it. A run that drops one packet and reports clean counts fails on the id sets.

This is the only scenario that reaches the queue depth the design is sized for. Every other run leaves the claim untested and reads as though it passed.

### `encounter.yaml`

Round 3 finding 8: the previous version claimed every coordinate was frozen and then gave none, and its gate asked only for `yield_events>=1`. A logger emitting one event while the flight command carried on unchanged would have satisfied all three assertions. Worse, `collision_contacts==0` passes when no contact monitor was ever attached, which is the same shape as the package check that passed on a machine with no simulator.

So the trajectories are frozen, and there is a negative control.

| Parameter | Value |
| --- | --- |
| Both vehicles at | 45 m, deliberately the same altitude, so layering cannot save them |
| `uav_3` | (250, -120, 45) to (250, 120, 45), starting at `t = 20 s` |
| `uav_4` | (130, 0, 45) to (370, 0, 45), starting at `t = 20 s` |
| Speed | 10 m/s both |
| Crossing point | (250, 0, 45), reached by both at `t = 32 s` |
| Run duration | 90 s |

Both paths are 240 m and both vehicles start together, so without intervention they arrive at the crossing point simultaneously and pass within 0 m. `uav_4`, the higher system id, must hold until the conflict clears.

### `encounter_noyield.yaml`

The negative control, and the only scenario here that has to fail.

Identical to `encounter.yaml` in every frozen number, with the yield rule disabled. Same two vehicles, same converging paths, same commanded altitude, same 240 m to the crossing point, same seed. One flag differs.

| Field | Value |
| --- | --- |
| Geometry | exactly `encounter.yaml` |
| Yield rule | disabled |
| Required result | at least one separation violation |

Without it, a run where the two vehicles happened to miss each other is indistinguishable from one where the rule worked, and the 10% safety row rests on a coincidence. `check_geometry.py` proves the coincidence is impossible by construction: both paths are 240 m, so neither vehicle arrives first, and with nobody yielding they pass within 0.0 m. A control run that records no violation means the yield rule was never disabled, or the contact monitor was never attached, and either way the positive run proves nothing.

The gate requires, on the yield run: at least one yield event **naming `uav_4`**, a non-zero hold duration, `min_pairwise_separation_m` at or above 10, zero contacts, a non-zero contact-monitor sample count so the zero means something, enough pose samples, and both vehicles completing. On the control run it requires a violation.

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

The same checker runs again in W5 against every record the proposal cites, not just at the end of the week that produced it. Round 4 finding 6: W5 was matching filenames, so an empty file with the right name counted as evidence for a rubric row.

## 8. What each rubric row is actually earning

| Criterion | Weight | Evidence artifact | Also in the integrated run |
| --- | --- | --- | --- |
| Mission completion | 25% | `coverage_fraction` from pose samples, `survey_baseline` | `coverage_fraction` after losing a vehicle to the relay role |
| Communication resilience | 25% | `delivery_ratio_by_node.uav_4` in `relay_required` against `direct_only` | `observations_set_equal` and `observations.evicted` in `link_loss` and `queue_drain` |
| Autonomous relay and role management | 20% | `relay_role_moved`, the named transition in `relay_kill`, plus the seam test | `relay_role_holder`, `strip_reassigned_to` |
| Fault recovery and swarm reconfiguration | 15% | `time_to_reconnect_s` in `relay_kill` for a vehicle lost, and in `link_loss` for a vehicle gone quiet and returned | same, measured mid-survey rather than from a hover |
| Safety and collision avoidance | 10% | `yield_events` in `encounter`, `min_pairwise_separation_m`, `collision_contacts` | `separation_violations` across the whole run |
| Innovation and technical merit | 5% | this document, and the honest statement of what the link layer does and does not constrain | the radio-bounded survey area, stated with its arithmetic |

The right-hand column exists because a panel reading five separate runs has to take on trust that the subsystems compose. `mission_integrated` is the one run where they have to, and it is what the video shows.
