# Week 2: the survey, the evaluator and the comms logic

6 to 12 September on the plan. Built 3 September, ahead of it, because week 1
finished early and the cluster became reachable the same day.

Three of the four chunks are implemented and one is not started. The week is
**not accepted**, for the same reason week 1 is not: acceptance is
`bash scripts/gate.sh 2` and that cannot run until
`submission/human-preflight.json` exists.

## What landed

| Chunk | What it is | Proved by |
| --- | --- | --- |
| `2.1` | `uavx_mission`, the planner, the partitioner and the executor | 81 tests, and 24 mutations of wrong implementations all caught |
| `2.2` | `uavx_eval`, the collector and the provenance check | 64 tests, and each of 13 rejection rules disabled in turn to watch its test fail |
| `2.3` | `uavx_comms`, the link model, routing and election | 91 tests, and 52 mutations all caught |
| `2.4` | the survey scenario and the coverage run | **not started** |

415 package tests across four packages, 51 seam fixtures, 53 submission checks,
19 launcher geometry checks, 113 gate expressions, 25 shell scripts. The three
chunk gates were run directly, because every gate calls `gate_preflight` first
and that refuses without the human receipt.

## The evidence worth reading

**The planner and the frozen geometry agree without being told to.**
`uavx_mission` knows nothing about `mission_integrated`, and given the frozen
box it reproduces the four lane positions `architecture.md` freezes and
`check_geometry.py` flies, to the digit. Two independent routes to the same four
numbers is the one thing a mirror test cannot be. Timing has 164.9 s of slack in
a 420 s run at the slower of the two speeds, so nothing about the plan is tight.

**Each rejection in the provenance gate was watched failing.** `uavx_eval.check`
refuses a record whose source hash is not the tree, whose scenario is not the
expected one, whose scenario file has moved, whose commit is not real, that was
cut short, that did not complete, whose injected event never fired, or that has
a zero denominator, and it reads no metric at all while provenance fails. Each
rule was disabled in turn and its test confirmed to fail. Round 4 of the reviews
found two checkers in this repository that had never been shown to fail, and
this one has been shown thirteen times.

**Driving the comms state machines found five defects that reading them did
not.** The component attached itself to the dead relay rather than the anchor,
so the chain never reformed and every path news could travel went through a
corpse. A node computed routes over a link it had already given up on, because
it read its own row out of the link-state database. Retention nothing acted on,
so packets dropped in the fade band were held forever and the delivered set
could never equal the generated set. A lease renewed for a relay that had died,
so the epoch never closed. And an epoch counter that reached 56 for a single
failure, because the give-up path did not arm its own suppression.

## Defects found in the acceptance harness

Week 1 found fourteen. Week 2 has found four more, and three of them would have
failed correct code rather than passed broken code.

1. **A chunk was judged by every package in the build base.** `colcon test` is
   filtered by `--packages-select` and `colcon test-result` was not, so it read
   the whole shared build base: unfiltered it reported 145 tests, which is
   `uavx_mission`'s 81 plus `uavx_eval`'s 64. Two chunks worked in parallel
   could fail each other, and the plan's promise that a chunk is the unit of
   work was not true. Found by chunk 2.1, which correctly refused to edit the
   gate to make itself pass.
2. **A package with no tests passed its own gate.** Underneath the first.
   `colcon test-result` over a package that produced nothing says "0 tests, 0
   errors, 0 failures" and exits 0. That is the exact failure the plan records
   for this week, where the tests the week was built around lived in a package
   the gate never tested, one level further down. It mattered immediately:
   `uavx_comms` had 2,618 lines and no tests at the moment it was found.
3. **The seam pass could not have exempted the link layer.** The exemption named
   `uavx_comms/link_layer.py` relative to `uavx_ws/src`, and an ament_python
   package puts its code at `package/package/module.py`. The real link layer is
   one directory deeper and matched nothing, so `w3_seam_static` would have
   reported a correct link layer as reading simulator ground truth. The fixture
   that proved the exemption worked was written from the same assumption and
   built the file at the same impossible path, so the suite was testing the
   assumption rather than the checker. Three new cases pin the edges.
4. **The PX4 setup step undid the step before it.** PX4's own
   `Tools/setup/ubuntu.sh` at the pinned commit adds the osrfoundation apt
   source and installs `gz-garden` on jammy, and `setup-all.sh` runs
   `03-gazebo-classic.sh`, whose whole job is removing that, and then `04-px4.sh`
   which called it with the simulation branch live. A judge following
   `INSTALL.md` on a clean machine would have finished with the Garden repo
   enabled and Classic at risk on the next upgrade. This machine escaped because
   03 was re-run by hand afterwards, and the residue is still installed. Found
   while building the cluster image, which is a fair argument for having built
   it.

## The cluster

Reachable since 3 September and the container question is answered. Four PX4
SITL vehicles reach ready for takeoff inside a rootless podman container on a
compute node, headless, driven by this repository's own launcher, with every apt
pin and git SHA matching `versions.lock`. Detail and the four things that
blocked it first are in [stage-1/hpc/README.md](../../stage-1/hpc/README.md),
and what the machine actually is now sits in [_compute.md](../../_compute.md).

Week 4's sweep has a floor under it. It still has no image distribution, no
harness turning a scenario into a job, and no measurement of several containers
sharing a node.

## Outstanding

**Chunk 2.4 is not started.** The survey scenario, the coverage run and the
`coverage_source` the gate requires. It is the first chunk this week that needs
the simulator and the first that integrates the three packages above.

**Poses reach 9.78 Hz against a frozen 20 Hz.** Carried from week 1 with its
cause now known: gazebo publishes `/clock` at 10 Hz and the sampler cannot
outrun it. Chunk 2.1 measured what that costs and it is less than it sounds. At
survey speed the along-track sample spacing is 0.307 m against a 12 m sensor
footprint, so the rate changes the resolution of the measurement rather than
whether the plan covers. The remaining route is a world file carried in this
repository instead of PX4's copy.

**Two frozen documents need a person, and no agent may settle either.**
`architecture.md` section 4 step 1 puts `slot_position` in the ELECTION message,
while the slot rule defines the slot against the work of the members that stay,
which is not known until step 3. Chunk 2.3 put it in ASSIGN and recorded the
contradiction rather than editing the document. Separately, `survey_baseline`
names a cruise speed and no survey speed, where `mission_integrated` freezes
both; chunk 2.1 made it configuration and tested feasibility at the slower
figure, which is the conservative direction.

**`check_geometry.py` carries the survey box as function locals.** The same four
numbers now live there and in `architecture.md`, and nothing compares them. It
is the shape of every drift finding this project has had.

## Why this file has no done marker

The done marker goes in when `bash scripts/gate.sh 2` passes, and this file does
not spell it out, because the loop greps for the string rather than reading the
sentence around it. The gate cannot run: `gate_preflight` refuses without
`submission/human-preflight.json`, which records registration, the eligibility
declaration, the clarification channel, the organiser email, the delivery route
and a compliance sign-off. Registration is done and the issued id is recorded.
The other five are open and no amount of code closes them.
