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

178 package tests, 48 seam fixtures, 49 submission checks, 20 rehearsal
checks, 113 gate expressions parsed, 21 shell scripts, static seam pass clean.

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

## Outstanding, carried into week 2

**Instance 2, which is uav_3, does not reach its layer altitude.** Across seven
runs it has never made 50 m inside the 120 second climb budget, reaching
between 7.8 and 12.0 m, while uav_1, uav_2 and uav_4 hit 30, 40 and 60 m every
time to within 0.4 m. It is slow rather than stuck: in one run it was written
off at 9.6 m and then climbed to 49.65 m during the scenario. Clearing the
persisted parameters did not explain it. No gate requirement depends on
altitude and `harness_check` is never cited as evidence, so week 1 passes
honestly, but week 2's survey does depend on vehicles reaching their layers.
**This is the first thing to fix.**

**`--record` is validated and not implemented, and exits 40.** There is no
headless capture path on this stack: the world carries no camera sensor and
gzclient must never be launched. Every flag rule the contract freezes is still
enforced first. Building the offscreen path belongs to chunk 3.6, and
`rehearse_recording.sh` will fail until it exists.

**The ROS 2 CLI daemon wedges**, answering `ros2 topic list` with an XMLRPC
traceback and an empty stdout, which the launcher reads as zero topics. The
runner restarts the daemon before each bring-up and retries up to three times.
`sitl_multi.sh` was not changed; its 15 second settle is the underlying
tightness.

## Why this file has no done marker

`WEEK-1-DONE` goes in when `bash scripts/gate.sh 1` passes. It cannot run:
every chunk gate calls `gate_preflight` first, and that refuses to start
without `submission/human-preflight.json`, which records registration, the
eligibility declaration, the clarification channel, the organiser email, the
delivery route and a compliance sign-off. Those are human steps and none is
done.

Everything the chunk gates would run has been run directly and passes. The
gate becomes real acceptance the moment that file exists, and nothing here
should be treated as accepted until it does.
