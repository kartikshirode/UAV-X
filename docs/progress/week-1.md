# Week 1: the scaffolding every later week writes into

30 August to 5 September. Built 31 August and 1 September.

All seven chunks are implemented and every check in the repository passes.
The week is **not accepted**, because acceptance is `bash scripts/gate.sh 1`
and that cannot run until `submission/human-preflight.json` exists. See the
bottom of this file.

## What landed

| Chunk | What it is | Proved by |
| --- | --- | --- |
| `1.1` | `uavx_msgs`, the five frozen messages | `check_message_contract.py --generated`, against the built classes |
| `1.2` | `run_smoke.sh`, four vehicles up, hold, land | two consecutive real flights, 4/4, nothing left running |
| `1.3` | `harness_check.yaml` and the scenario loader | `check_scenario.py` plus 46 rejection tests |
| `1.4` | the event injector | 17 tests, built around requested against observed |
| `1.5` | graph capture | 27 tests, asserted against the real `seam_graph.py` |
| `1.6` | the resource sampler | 27 tests, measured 12.5 MiB root against 639 MiB group |
| `1.7` | the runner, the record writer, `run_scenario.sh` | a real run satisfying all ten gate requirements |

179 package tests, 48 seam fixtures, 51 submission checks, 20 rehearsal
checks, 8 preflight decisions, 113 gate expressions parsed, 21 shell scripts,
static seam pass clean. Every one of those was run again at the end of the
week, in a shell that did not belong to whoever wrote the code.

## The run that proves it

`runs/harness_check_20260901T095331Z.jsonl`, made against the tree as
committed at `24a4f78`, so its `source_tree_sha256` covers the runner that
wrote it.

| Requirement | Value |
| --- | --- |
| `completion` | complete |
| `clock_source` | ros_sim_time |
| `pose_sample_count` | 832 |
| `vehicle_ids_observed` | uav_1, uav_2, uav_3, uav_4 |
| `injected_event_observed` | true, a JSON boolean |
| `injected_event_count` | 1 |
| `resources.peak_rss_mib` | 503.047, about 5 percent of the ceiling |
| `resources.swap_used_mib` | 0.0 |
| `resources.samples` | 60 |

`check_seam.sh` accepts the captured graph. `run_scenario.sh` against an
absent path returns 10 and leaves no `latest` artifact, in both orderings.

That run is the one the gate expressions were checked against, and it is also
the run where uav_3 stopped at 11.98 m. Four more followed the launcher fix in
defect 13, and every vehicle reached its layer in all of them.

| Run | uav_1, 30 m | uav_2, 40 m | uav_3, 50 m | uav_4, 60 m | preparation |
| --- | --- | --- | --- | --- | --- |
| `...095331Z` | 30.18 | 40.23 | **11.98** | 60.23 | 121.1 s |
| `...104152Z` | 30.41 | 40.44 | 50.45 | 60.45 | 26.2 s |
| `...104415Z` | 30.40 | 40.43 | 50.40 | 60.46 | 25.8 s |
| `...104637Z` | 30.38 | 40.47 | 50.47 | 60.48 | 25.6 s |
| `...105016Z` | 30.38 | 40.48 | 50.44 | 60.45 | 25.9 s |

Preparation is wall clock from launch to every vehicle being ready. It fell by
95 seconds because nothing is fighting a failsafe any more. All ten gate
expressions and the graph pass hold on the last of those runs as well; I ran
them again rather than assuming the fix left them alone.

## Defects found while building, and fixed

The week found more in the acceptance harness than in the code it was
supposed to be writing. Every one of these would have failed a correct
implementation or passed a broken one.

1. **The `--require` grammar had no boolean case.** 17 gate expressions read
   `==true` and `bool` fell through to a string compare, so `str(True)` gave
   "True" and every one of them failed a correct record. `relay_slot.band_reserved`
   is schema-typed boolean, so that requirement was unsatisfiable by any record
   the schema would also accept. `!=false` passed whichever way the flag was
   set, and because `bool` subclasses `int`, a count field carrying `false`
   satisfied a comparison against zero. Those expressions carry W3 and W4,
   which is 60 percent of the rubric.
2. **W1 contract tests resolved one directory above the packages.** Chunks 1.3
   to 1.6 would have reported a missing test file against a correct
   implementation. Round 7 finding 1 under a new function name.
3. **`harness_check` had no seam manifest**, so chunk 1.7's seam line exited 2
   and the chunk could never have gone green.
4. **The seam pass had never run over a real ROS graph.** All 48 fixtures were
   hand written. The first captured graph failed on three endpoints, none of
   them a bypass: `/gazebo` unaccounted for, `/parameter_events` published by
   every rclpy node, and `/clock`, which architecture.md requires the runner to
   read.
5. **The message contract compared the source file with itself.**
   `--symlink-install` makes the installed `.msg` a symlink back into the
   source tree, so `ros2 interface show` printed bytes the checker had already
   read.
6. **`source_tree_hash.from_worktree` hashed tracked files only**, so every
   chunk's first run recorded provenance that did not cover its own code.
7. **A literal `\n` sat where a line continuation was meant** in chunk 1.3's
   gate line, which bash reads as an argument called `n` while every syntax
   check stays green.
8. **27 gate requirements had no definition in the run record schema**, so
   nothing pinned their type. Two are now typed and the remaining 25 are
   declared, in a list that can only shrink.
9. **The checked example could not have passed the gate.** It disagreed with
   `versions.lock` on three pins and carried neither event field.
10. **`ros2 node info` rows without a colon** stored the topic as its own type,
    and round 8's guard only looked for an empty string.
11. **PX4 SITL inherited its own persisted state.** Instance 2 alone carried a
    gyro calibration from an earlier experiment, and it was the one vehicle
    that never finished landing. Standing rule 4 asks that a run replay
    exactly.
12. **The smoke verdict read altitude 326 seconds after touchdown**, while the
    vehicle sat parked and its estimator wandered between -1.60 and +1.63 m.
    Three correct flights failed by under 11 cm.
13. **The launcher stood one vehicle on the edge of the pavement.** Vehicles
    spawned at `y = i * spacing`, running out from the origin. The
    `asphalt_plane` Gazebo actually resolves for `empty.world` on this machine
    is the 20 x 20 x 0.1 m copy in `~/.gazebo/models`, which shadows the
    200 x 200 one PX4 ships, so the paved square ends at y = +/-10 m with a 5 cm
    lip. Instance 2 went to y = 10 exactly. It settled at 9 degrees of roll
    against 0.09 for the other three, lifted off tilted, failed the EKF's
    post-takeoff navigation test and left under failsafe at 17.8 m/s. Its
    velocity innovation ratio peaked at 6.52 with 6177 samples over 1, while
    the other three never passed 0.33 and logged none. Spawning the same four
    in reverse order moved the fault to whichever instance was handed y = 10,
    so it was the ground and never the vehicle. The line is centred on the
    origin now, and the launcher asks Gazebo for each resting pose and refuses
    to report the stack healthy above 2 degrees of tilt.

## Outstanding, carried into week 2

**`--record` is validated and not implemented, and exits 40.** There is no
headless capture path on this stack: the world carries no camera sensor and
gzclient must never be launched. Every flag rule the contract freezes is still
enforced first. Building the offscreen path belongs to chunk 3.6, and
`rehearse_recording.sh` will fail until it exists.

**The ROS 2 CLI daemon wedges**, answering `ros2 topic list` with an XMLRPC
traceback and an empty stdout, which the launcher reads as zero topics. The
runner restarts the daemon before each bring-up and retries up to three times.
Defect 13 changed `sitl_multi.sh` for a different reason and left this alone;
its 15 second settle is the underlying tightness.

**`sitl_multi.sh` gives gzserver a 2 second grace on teardown and no KILL**, so
two direct invocations back to back can trip the launcher's own check that port
11345 is free. The scenario runner sends TERM and then KILL and is unaffected,
which is why this has never shown up in a gate. It will bite whoever runs the
launcher by hand.

## Why this file has no done marker

`WEEK-1-DONE` goes in when `bash scripts/gate.sh 1` passes. It cannot run:
every chunk gate calls `gate_preflight` first, and that refuses to start
without `submission/human-preflight.json`, which records registration, the
eligibility declaration, the clarification channel, the organiser email, the
delivery route and a compliance sign-off. Those are human steps.

One of them moved on 1 September. The competition id came through, so the
registration block now requires `competition_id` matching `UAVX-` and 12 upper
case hex characters, and the checker prints it. The other two fields in that
block are a date and an address, both of which a person can type without ever
having registered; the id cannot be guessed and the organisers can be asked to
confirm it. Two negative fixtures hold the rule up: an id transcribed in lower
case is rejected, and so is a registration with the id left out. The eligibility
declaration, the clarification channel, the organiser email and the compliance
review are all still open, and the id alone does not open the gate.

Everything the chunk gates would run has been run directly and passes. The
gate becomes real acceptance the moment that file exists, and nothing here
should be treated as accepted until it does.
