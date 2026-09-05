# Week 3: the mesh, the two runs that argue with each other, and a camera

13 to 19 September on the plan. Built 5 September, ahead of it, because weeks 1
and 2 both finished early.

All six chunks are in and the week is accepted. `bash scripts/gate.sh 3` exited
0 on 5 September, so WEEK-3-DONE. Three runs stand behind it:
`relay_required_20260905T112903Z`, which is the measurement, `direct_only_20260905T113527Z`, which is
its control, and `rehearsal_relay_required_20260905T114516Z`, which is the recording rehearsal.

## What landed

| Chunk | What it is | Proved by |
| --- | --- | --- |
| `3.1` | the codec, the link layer, the router node and `uavx_gcs` | 146 tests across the three packages, and the seam shape pinned in both directions |
| `3.2` | the frozen geometry, now compared with the files that fly it | `check_geometry.py`, and three mutations of the scenarios each caught |
| `3.3` | the static seam pass over a package that now holds nodes | clean over `uavx_ws/src` |
| `3.4` | `relay_required`, the mesh delivering from 484.6 m out | `uav_4` at 0.9968 over two forwarders |
| `3.5` | `direct_only`, the same file with forwarding off | `uav_4` at exactly 0 |
| `3.6` | the rebuild rehearsal and the recording rehearsal | a 60 s clip with the run id burned in, hashed into its receipt |

## The evidence worth reading

**The pair is the argument.** `relay_required` and `direct_only` differ in one
flag. Same seed, same stations, same roles, same duration, and
`check_geometry.py` compares the two files field by field so they cannot drift
into two different measurements. With forwarding on, `uav_4` delivers 1235 of
1239 observations from 484.6 m away, which is 1.9 times the range at which any
link exists. With forwarding off it delivers 0 of 1243 and the anchor, at
167.7 m, keeps delivering.

**The topology is read back out of the traffic rather than asserted.** The
ground station reports, per origin, the fewest forwarders any accepted packet
went through. It came back 0 for the anchor, 1 for the relay and 2 for both
surveyors, which is `uav_4 -> uav_2 -> uav_1 -> gcs`. Nobody told it to expect
that. The edge count is reported beside it, one higher in every row, so the two
numbers cannot be quietly swapped: a delivery that arrived direct has zero
forwarders and one edge, and the gate's threshold of 2 is one no direct
delivery can reach.

**The vehicles were where the design says they were.** A station-keeping run is
a claim about where four vehicles stand, so the run refuses to start until
every one of them is inside 5 m of its frozen position, and the record carries
each vehicle's final distance from it. Both runs reached station in 41 s of
ingress and finished between 5 and 20 cm out. Without that gate a vehicle 200 m
short would still fly, still deliver, still write a record, and nothing
downstream could tell.

**The comms nodes start after the ingress and never before it.** Traffic minted
in transit would inflate a denominator the record divides by, and in
`direct_only` it would be minted while `uav_4` was still a few metres from the
ground station, which is the one thing the control exists to deny.

**Everything runs on simulated time.** The frozen protocol periods and every
`_s` in the record are the same clock. A router on wall time would generate its
rate times the wall seconds while the record divided by simulated ones, and the
two differ by about a fifth on this stack, so every denominator in the
communication row would have come out a fifth too large.

**The capture path exists and is proven.** gzserver renders a camera sensor
headless here, which was not known before this week and which the whole video
deliverable rests on. The rehearsal captured a minute of simulated time at
7.75 frames a second, encoded at the rate the frames were actually captured
at, with the run id burned into every one of them and the clip's sha256 written
into a receipt that also carries the run record and the graph snapshot.

## Defects found this week

Week 1 found fourteen in the acceptance harness, week 2 seven. Week 3 has found
five more there, and three in its own code that only running it exposed.

In the harness:

1. **The rebuild rehearsal could not have passed.** Its overlay step ran
   `set -u` and then sourced colcon's `setup.bash`, which has never been
   `set -u` clean. It exited 127 on `COLCON_TRACE` being unbound, against a
   build that had just finished cleanly. `gate-env.sh` has carried
   `uavx_source` for exactly this since week 1 and this was the one place that
   did not use it. Nothing had ever run the rehearsal, which is what the
   rehearsal is for.
2. **That step also asserted nothing about this workspace.** It sourced an
   overlay and looked for `ros2`, which proves the underlay is on the path.
   Chunk 3.1 shipped `uavx_gcs` without `setup.cfg`, so its console script
   installed to `bin/` instead of `lib/uavx_gcs/`, the package built and every
   test passed, and `ros2 run uavx_gcs gcs_node` would have failed at the first
   scenario that needed it. The step names all six executables the frozen
   scenarios invoke now and says where a stray one went. Proved by moving
   `gcs_node` back to `bin/` and watching it fail with the fix in the message.
3. **The week's gate did not test the packages the week changes.** `w3_tests`
   ran `uavx_comms`, `uavx_msgs` and `uavx_gcs`. Chunk 3.4 adds station keeping
   to `uavx_mission` and the whole comms wiring plus the delivery block to
   `uavx_sim`, and neither was in the list. That is week 2's second defect one
   week later: a gate that skips the packages the week changed is a gate
   measuring last week.
4. **The frozen positions were in two places and compared with nothing.**
   `check_geometry.py` held them as constants and the scenario files held them
   as YAML. A station typed 10 m out would fly, deliver packets and write a
   record, and the only thing wrong with the run would be that it was not the
   run `architecture.md` describes. The checker reads both files now. Proved by
   moving `uav_2` 10 m, by turning forwarding back on in the control and by
   changing one seed, and watching each fail.
5. **The launcher raced DDS discovery and lost.** It looked once, 15 seconds
   after the last spawn, through the ros2 daemon, and reported two of four
   namespaces missing with all four PX4 processes alive, the agent up and every
   model standing level. The agent registers its clients asynchronously, so a
   single look after a fixed sleep is a guess about how long that takes; and
   the daemon caches, which is why the scenario runner already restarts it
   before every bring-up. It waits for the condition with a deadline now and
   asks DDS rather than the cache.

In this week's own code, found by running it:

6. **The workspace stopped being orderable.** `uavx_sim` needs the frame
   conversion, which lives in `uavx_mission`, and `uavx_mission`'s tests
   borrowed one number from `uavx_sim`. colcon refused the cycle and would not
   build anything. The number was the wrong one to borrow: the test reasons
   about the rate a plan is sampled at when its coverage is checked, and it was
   reading the harness's MAVLink telemetry rate rather than the collector's
   ground truth rate. Both are 20 Hz, so the mis-citation showed as nothing at
   all until the cycle made it visible.
7. **A test file could not be collected on a clean checkout.** `test_codec.py`
   says in its own docstring that it needs the overlay because the message
   package is generated, and the `importorskip` that says so sat below an
   import of a module that imports the message package. Under colcon the
   overlay is sourced and it passed; `pytest` over the package on a clean
   checkout could not collect the file at all.
8. **The clip came out shorter than the window it covered.** The first capture
   to reach the encoder asked for 60 seconds, gathered 418 frames across
   exactly 60.0 s of simulated time, and produced 59.96 s, which the rehearsal
   refused. N frames have N-1 intervals between the first and the last, and the
   rate was computed by dividing the span by the frames. Off by one frame in a
   rate, and the whole rehearsal turned on it.

## Carried, and one that is new

**gzserver aborts occasionally during bring-up.** One smoke run inside the
rebuild rehearsal died with a boost `shared_ptr` assertion in
`gazebo::transport::Connection` after all four models had spawned and
connected. The same rehearsal passed on the next attempt with nothing changed.
The scenario runner already retries bring-up three times; `run_smoke.sh` does
not, and this is the first time it has mattered. Not chased further because it
has happened once in about forty bring-ups this week, and the note is here so
the second occurrence is not treated as new.

**The altitude layers still cannot guarantee the separation floor.** Carried
from week 2, and narrower than it was written down there. `altitude_layer_m`
and the floor are both frozen at 10 m, so two vehicles stacked one above the
other sit exactly on it and any altitude error at all breaches it, which is the
9.90 m the survey run measured. Standing rule 4 forbids moving either number.
What week 3 can add is where it bites. `check_geometry.py` walks the integrated
mission and the two surveyors are mirrored, 12.5 m apart in x and 10 m in z, so
16.0 m; the relay band puts every mover 15 m above the highest mission
altitude. The scenario that stacks vehicles on purpose is `encounter`, and
there it is the yield rule rather than the layers that has to hold them apart.
Neither week 3 run asserts separation.

**Poses reach the clock and no further, and week 4 will feel it.** `/clock` is
10 Hz and three approaches to raising it have failed. Week 3 puts every comms
node on simulated time, which is correct, and the consequence is that no timer
can fire faster than that tick. The frozen hop latency is 20 ms and the
delivery queue drains on a 10 Hz clock, so a control packet that waits for the
next tick reads a delay of about 100 ms against the 50 ms `queue_drain`
asserts. That rules out one implementation rather than all of them: a packet
served in the callback that enqueued it reads the same clock twice and the
delay is 0.0, so control has to be served on arrival and never behind data. The
drain bound survives the same tick. 450 packets at 200 a second is 2.25 s,
which on a 10 Hz timer is 20 a tick, and a first batch sent when the route
returns rather than at the next tick finishes at 2.2 s. Both hold with the
clock where it is, and both stop holding the moment a queue is drained only by
a timer. Neither week 3 gate reads either number.

**The demo footage needs a shot, not just a camera.** The capture path works
and that was the risk worth retiring. What it produces is not yet a demo. The
first framing put the whole chain in the sky above the horizon at about a pixel
each; narrowing the field and aiming it level brought the vehicles into frame
and the shot was still empty, because `model://ground_plane` is a hundred
metres across and the swarm stands 285 to 595 m away over nothing at all. The
recording world now carries an apron and a centre line, both visual only with
no collision geometry, which give the frame a horizon and something to read
distance against.

That is as far as a static camera goes. `relay_required` is also the wrong
scenario to film, because every vehicle is holding station and nothing moves
for 240 s. The demo shot belongs to `mission_integrated`, where two vehicles
fly a survey and a third relocates, with the camera near the survey box rather
than watching the corridor.

## Outstanding

The proposal is still at zero assembled pages. Week 3's own section, the
communication architecture, is drafted in
[docs/proposal/week-3-communication.md](../proposal/week-3-communication.md)
with every number citing the run it came off. Weeks 1 and 2 owe theirs.

The two dated attestations in `submission/human-preflight.json` go stale on
18 September.
