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
| `1.6` | the resource sampler | 27 tests, built on the gap between one process and its children |
| `1.7` | the runner, the record writer, `run_scenario.sh` | a real run satisfying all ten gate requirements |

179 package tests, 48 seam fixtures, 53 submission checks, 20 rehearsal
checks, 8 preflight decisions, 19 launcher geometry checks, 113 gate
expressions parsed, 21 shell scripts, static seam pass clean. Every one of
those was run again at the end of the week, in a shell that did not belong to
whoever wrote the code. Ten suites now, and until 1 September no gate ran any
of them; see defect 14.

## The run that proves it

`runs/harness_check_20260901T111030Z.jsonl`, made against the tree as
committed at `3415c3d`. Its `source_tree_sha256` is
`7bc9a5566937d26ebf6ece4a37938e9502a5f27e9a2cf92d72de764e8110285b`, which is
the digest of that commit, so the record covers the runner and the launcher
that produced it and not some edit sitting beside them.

| Requirement | Value |
| --- | --- |
| `completion` | complete |
| `clock_source` | ros_sim_time |
| `pose_sample_count` | 836 |
| `vehicle_ids_observed` | uav_1, uav_2, uav_3, uav_4 |
| `injected_event_observed` | true, a JSON boolean |
| `injected_event_count` | 1 |
| `resources.peak_rss_mib` | 504.262 |
| `resources.swap_used_mib` | 0.0 |
| `resources.samples` | 60 |

`check_seam.sh` accepts the captured graph against that record. `run_scenario.sh`
against an absent path returns 10 and leaves `latest` byte for byte unchanged.

Six runs sit under `runs/`, and the altitudes are the reason there are six.

| Run | uav_1, 30 m | uav_2, 40 m | uav_3, 50 m | uav_4, 60 m | preparation |
| --- | --- | --- | --- | --- | --- |
| `...095331Z` | 30.18 | 40.23 | **11.98** | 60.23 | 121.1 s |
| `...104152Z` | 30.41 | 40.44 | 50.45 | 60.45 | 26.2 s |
| `...104415Z` | 30.40 | 40.43 | 50.40 | 60.46 | 25.8 s |
| `...104637Z` | 30.38 | 40.47 | 50.47 | 60.48 | 25.6 s |
| `...105016Z` | 30.38 | 40.48 | 50.44 | 60.45 | 25.9 s |
| `...111030Z` | 30.38 | 40.44 | 50.44 | 60.45 | 25.8 s |

The first is from before the launcher fix in defect 13 and is kept as the other
half of the comparison. Preparation is wall clock from launch to every vehicle
being ready, and it fell by 95 seconds because nothing is fighting a failsafe
any more. I re-ran all ten expressions and the graph pass on the last row
rather than assuming the fix left them alone.

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
8. **25 gate requirements had no definition in the run record schema**, so
   nothing pinned their type. Two are typed now, `injected_event_observed` and
   `injected_event_count`, and the remaining 23 are declared in a list that can
   only shrink. `check_docs.py` prints that number on every run, so it is worth
   reading rather than trusting this sentence: the first version of this line
   said 27 and 25, and both were wrong.
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
    to report the stack healthy above 2 degrees of tilt. The guard was watched
    failing for the right reason: `--vehicles 2 --spacing 20` puts both back on
    the lip at y = +/-10, they settle at 9.58 and 9.74 degrees and the launcher
    exits 1 naming the asphalt edge.
14. **No gate ran the suites that prove the checkers work.** Nine fixture
    suites sit under `scripts/`, and `scripts/gate.sh` ran none of them. Every
    threshold in the gate is enforced by a checker, so a regression in
    `seam_graph.py` or in the `--require` grammar would have gone through every
    week in silence. Twelve of the thirteen defects above are in that layer and
    every one was caught by hand. Seven of the suites cost 7 seconds between
    them and run in preflight now, on every chunk, with no ROS environment
    needed. The two that cost about two minutes run once per week and again on
    chunk 4.8, the last thing before a human sends.
15. **The ten expressions the gate asserts were copied into three files and
    compared with nothing.** Two test files carried the list by hand, each under
    a comment saying it came from `gate.sh`, and the memory ceiling had five
    homes. A copy that drifts here asserts last week's contract and passes, so
    it fails silently rather than loudly. `check_docs.py` reads the list out of
    `w1_runner` and compares both copies now, and the ceiling has one home that
    the others import.
16. **The tilt check added in defect 13 failed open.** `awk` reads a
    non-numeric field as zero, so an error line, an empty answer or a changed
    output format each produced a confident 0.00 degrees and the launcher would
    have called a vehicle level that it had never measured. It refuses anything
    that is not six numbers now, and `scripts/test_launcher_geometry.py` runs
    both of the launcher's calculations without a simulator: 19 checks over the
    spawn line and the pose reading, seven of them malformed answers. The suite
    found on its first run that five vehicles at the default spacing put two of
    them on the lip. The count is fixed at four, so nothing depends on it, and
    the guard would now stop it.
17. **Any scenario could narrow what the seam pass demanded of it.** The
    per-scenario `required_outside` override went in for the week 1 harness,
    which runs before a swarm exists. Nothing stopped a week 3 or 4 scenario
    from using it to drop the radio from its own list, which would have produced
    a clean graph report for a run with no radio in it. Week 1 only now.

## Where the numbers in defects 11 to 14 come from

Standing rule 3 says every number in a document traces to a run record under
`runs/`. The altitude, preparation and resource figures above do. The
diagnostic figures in defects 11 to 14 do not, and it is worth being exact
about that rather than leaving them looking like run data.

| Figure | Read from | In the repository |
| --- | --- | --- |
| innovation ratios, sample counts, ground speed, roll | PX4 ULOG under `~/.ros` and the PX4 log directory | no |
| resting pose and tilt per model | `gz model -m iris_N -p` while the stack was up | no |
| tilt 9.58 and 9.74 degrees | the launcher's own refusal message | no |
| the 5 cm lip and the plate size | `~/.gazebo/models/asphalt_plane/model.sdf` | no, it is outside the repo |
| altitudes, preparation, resources | the six run records | yes |

Nothing in the first four rows can be re-derived from a checkout, because the
logs live outside it and the runs that produced them are gone. They are honest
readings and they are not reproducible evidence, which is a different thing.
The claim they support, that a vehicle standing on a 5 cm lip fails to fly, is
reproducible: `--vehicles 2 --spacing 20` puts two vehicles back on it and the
launcher refuses, every time.

## Outstanding, carried into week 2

The audit is [docs/audits/week-1.md](../audits/week-1.md). Four of its findings
are open and three of them are here.

**Poses are sampled at a quarter of the frozen rate.** The runner samples at
5 Hz, hardcoded in the module, while `architecture.md` names 20 Hz as the
coverage source and again for the separation monitor. Week 2 computes coverage
off sampled poses, so a survey scored at a quarter of the intended resolution is
a different measurement, and no scenario file can correct it because the rate is
not read from one. **This is the first thing to fix.**

**Nothing records where each vehicle stands, and defect 13 moved them.**
Position comes from PX4's local frame, whose origin is each vehicle's own spawn
point, while the design fixes all geometry in one frame with the ground station
at the origin. The record carries a home altitude per vehicle and no home x or
y. Any week converting sampled poses into the frozen frame needs those offsets,
and the only place they are written down is an awk expression in a shell script.
The launcher prints them; the runner should record them.

**`run_smoke.sh` writes no valid run record, and `architecture.md` says it
does.** The four smoke files carry `kind: smoke-placeholder` and admit in a note
that they do not validate. The deferral was reasonable when the record writer
did not exist. Carrying it silently while citing those runs as chunk 1.2's proof
was not, which is why it is written here now.

**The proposal's architecture section was not written.** The plan asks for it
this week, outside the chunk table, and `check_docs.py` matches only the 25
chunk rows, so nothing looked. Three more of these sections are due and the
submission cannot go without the proposal.

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

**`sitl_multi.sh` gives gzserver a 2 second grace on teardown and no KILL.**
Reproduced while testing the tilt guard: two direct invocations back to back,
and the second died on the launcher's own check that port 11345 is free, with a
gzserver still holding it. The scenario runner sends TERM and then KILL and is
unaffected, which is why no gate has ever seen this. It will bite whoever runs
the launcher by hand, and the leftover process then blocks the next gate at
preflight.

## Why this file has no done marker

The done marker the loop greps for goes in when `bash scripts/gate.sh 1`
passes, and this file deliberately does not spell it out, because a sentence
explaining a marker is indistinguishable from the marker itself to the thing
looking for it. That very sentence used to carry it, so the supervisor would
have read this week as accepted while the gate had never run. It cannot run:
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
