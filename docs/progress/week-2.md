# Week 2: the survey, the evaluator and the comms logic

6 to 12 September on the plan. Built 3 and 4 September, ahead of it, because
week 1 finished early and the cluster became reachable the same day.

All four chunks are in and the week is accepted. `bash scripts/gate.sh 2`
exited 0 on 4 September, so WEEK-2-DONE. The two runs behind it are
`harness_check_20260904T022051Z`, which 2.2 breaks on purpose to watch the
provenance check refuse it, and `survey_baseline_20260904T022321Z`, which is
the survey.

## What landed

| Chunk | What it is | Proved by |
| --- | --- | --- |
| `2.1` | `uavx_mission`, the planner, the partitioner and the executor | 81 tests, and 24 mutations of wrong implementations all caught |
| `2.2` | `uavx_eval`, the collector and the provenance check | 64 tests, and each of 13 rejection rules disabled in turn to watch its test fail |
| `2.3` | `uavx_comms`, the link model, routing and election | 91 tests, and 52 mutations all caught |
| `2.4` | the survey scenario and the coverage run | `survey_baseline_20260904T022321Z`, every cell of the frozen box covered |

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

**The survey covered the box completely.** Four vehicles, 420 s, 400
cells of 400 seen, off 16,692 sampled poses and 4,269 ground truth frames, in a
peak of 947.5 MiB. The figure comes from where the vehicles were and not from
where the planner meant to send them, and the label saying so is in the record
beside it. It took four attempts to get a number that meant anything, and the
first three are the section below.

## What flying the survey found

Chunk 2.4 is the first chunk in this project that started our own nodes inside a
simulator. Four defects surfaced in the first four minutes of doing that, and
every one of them sat in code that already had tests passing over it.

1. **The mission node could not construct itself.** `rclpy.node.Node` defines
   `executor` as a property with a setter, and the setter calls `add_node` on
   whatever it is handed, so `self.executor = MissionExecutor(...)` raised
   `AttributeError` in every vehicle. `executor` and `handle` are the only two
   settable properties the base class has and the node had picked one of them.
   Chunk 2.1 proved the planner and the executor as arithmetic with no ROS
   anywhere, which was right for the arithmetic and left twenty lines of wiring
   never once executed. `test_node_attributes.py` reads the reserved names out
   of rclpy and the assignments out of the tree, so neither side is a copy, and
   it scans every package rather than the one that had the bug.
2. **The topic the coverage metric is scored from did not exist.**
   `libgazebo_ros_state` is a WorldPlugin and `gzserver -s` loads SystemPlugins,
   so the launcher's attempt to load it produced one line in a log nobody was
   reading and no `/gazebo/model_states` at all. The captured graph proves it:
   `/gazebo` published the clock, the performance metrics and `/rosout`, and
   nothing else. Chunk 2.2 built a collector against a topic that has never been
   on this stack, and its 64 tests all passed because they hand it poses
   directly. The first survey flew the whole run and reported nothing covered.
   A WorldPlugin is declared inside a world, so this repository carries one now.
3. **The vehicles landed halfway through and kept reporting offboard.** The
   offboard heartbeat was published inside the position callback, so it depended
   on another topic's cadence and stopped outright when the plan finished, which
   is exactly when the vehicle is airborne and needs to hold. PX4's own rcS sets
   the offboard loss timeout to half a second in SITL. The log reads: offboard
   granted, failsafe activated, matching flight task was not able to run, landing
   detected, disarmed by landing. It has its own timer now.
4. **The collector's final payload could never publish.** `rclpy.init` installs
   signal handlers that shut the context down before the exception reaches any
   `finally`, so the one payload marked final, the one the runner waits for and
   builds the record from, always failed with an invalid context. The node owns
   its own handlers now and restores them on the way out.

The simulated battery is a fifth, and it is a property of the simulator rather
than a defect: it drains to half in a minute, so every run longer than that
flies on a warning. The runner raises the floor before a survey starts, through
the same echo and confirm path the cruise speed uses.

## Defects found in the acceptance harness

Week 1 found fourteen. Week 2 has found seven more, and five of them would have
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
5. **W1.1 tested a package that had no tests.** `w1_msgs` calls
   `gate_test uavx_msgs`, and `uavx_msgs` was five `.msg` files and a
   CMakeLists that generates them, with nothing under `test/`. `colcon test`
   reported 0 tests, and until defect 2 was fixed the gate called that a pass,
   so the line was decoration and the only real check was the contract
   comparison after it. The first full run of `bash scripts/gate.sh 1`, on
   4 September once the preflight receipt existed, failed on its first chunk
   for exactly this. The question plan.md asks of 1.1 is whether the five
   types resolve and `SwarmPacket` carries its identity fields, so the fix is
   a test that asks it: `test/test_interfaces.py` imports all five types from
   the generated bindings and pins `SwarmPacket`'s fields, their order, the
   `uint32` sequence and the five `kind` constants. A widened type, a changed
   constant and a reordered field were each watched failing it.
6. **The publisher of ground truth was an unaccounted-for member of the swarm.**
   The world plugin registers its own node beside `/gazebo` rather than
   publishing through it, and nothing in `seam_manifests.json` knew about it, so
   the first graph that carried ground truth failed the seam pass. It is chunk
   1.7's `/gazebo` finding one plugin later, and the answer is the same: the
   seam rule is about who reads ground truth and not who publishes it.
7. **The one scenario with no radio was required to show one.** A scenario may
   narrow the list of outside processes its graph must contain, and that
   allowance was restricted to week 1 on the reasoning that a later scenario has
   a radio by definition. `survey_baseline` does not: `architecture.md` section 6
   disables communications in it so the run measures the mission alone. The rule
   moved rather than the scenario. Weeks 1 and 2 may drop a required process and
   must record which frozen decision removed it, weeks 3 and 4 may not drop one
   at all, and the checker prints the reason it accepted. `harness_check` had
   been overriding in silence since week 1 and six of its fixtures went red the
   moment a reason became mandatory, which is how the silence was noticed.

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

**The altitude layers cannot guarantee the separation floor.** `altitude_layer_m` and the
separation floor are both frozen at 10 m in `architecture.md` section 5, so two
vehicles on adjacent layers passing one above the other sit exactly on the
floor, and any altitude error at all breaches it. PX4 holds altitude to about a
tenth of a metre. The accepted survey run recorded a closest approach of 9.90 m
and one frame under the floor, which is that arithmetic and not a control
failure. Nothing in week 2 asserts separation, so this does not touch the gate
that just passed; `mission_integrated` in week 4 requires zero violations across
its whole run and will fail on it. Two frozen numbers are in conflict and
standing rule 4 forbids an agent from moving either, so it is written down here
instead. Widening the layer spacing and lowering the floor both change a figure
the proposal quotes.

**Poses reach the clock and no further.** Carried from week 1 with its cause
now known and its cost now measured. Gazebo publishes `/clock` at 10 Hz and
neither the pose sampler nor the ground truth sampler can outrun it; the survey
run took 4268 ground truth frames at exactly 10.0 Hz against a 20 Hz target. It
has been attacked three ways and the third is new: a plugin block in the world
file cannot carry it, because `libgazebo_ros_init` is a SystemPlugin and only
WorldPlugins may be declared in a world. What it costs is resolution rather than
coverage, which the survey now demonstrates rather than argues.

**Two frozen documents need a person, and no agent may settle either.**
`architecture.md` section 4 step 1 puts `slot_position` in the ELECTION message,
while the slot rule defines the slot against the work of the members that stay,
which is not known until step 3. Chunk 2.3 put it in ASSIGN and recorded the
contradiction rather than editing the document. Separately, `survey_baseline`
names a cruise speed and no survey speed, where `mission_integrated` freezes
both; chunk 2.1 made it configuration and tested feasibility at the slower
figure, which is the conservative direction, and the scenario file flies the
faster one.

**`check_geometry.py` carries the survey box as function locals.** The same four
numbers now live there, in `architecture.md`, in `uavx_mission.survey_area` and
in `scenarios/survey_baseline.yaml`. The last two are compared with each other
and with the document; the checker's copy is compared with nothing.
