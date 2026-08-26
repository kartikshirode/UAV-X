# Plan review, round 2 (Codex)

Reviewed: `context.md`, `stage-1/plan.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `stage-1/setup/`, and `_plan-review-round1.md`, at HEAD `62c0bc44b2c39361e148405a5f52a4a9cce622f0`.

## Still open from earlier rounds

None. Round 1's UAV-X issues were either closed or replaced by more specific defects below. The missing `gh` binary no longer blocks the source link because this repo already has an `origin` remote.

## Findings

### Finding 1, critical: the supervisor shell does not load the ROS environment

**The plan says** `.claude/weekly-loop.md`, Shell: "`~/.bashrc` already sources `/opt/ros/humble/setup.bash` and `~/ws_uavx/install/setup.bash`, and `bash -lc` picks both up." It also says sourcing is the week-agent's job.

**The problem** I ran the exact `wsl.exe ... bash -lc` shell at the reviewed HEAD. `AMENT_PREFIX_PATH` and `COLCON_PREFIX_PATH` were unset, `ros2` was not on `PATH`, and only `/usr/bin/colcon` was found. A noninteractive login shell does not get the appended `.bashrc` lines on this machine. `verify.sh` passes because it sources ROS inside its own child process, but that environment disappears when the command exits. Every later gate starts a new shell. The first `colcon build` can therefore fail before W1 starts, and `python3 -m uavx_eval.check` will not see the repo overlay even after a build.

**Fix** Put all gates behind one repo-owned gate wrapper. It must source `/opt/ros/humble/setup.bash` and `~/ws_uavx/install/setup.bash`, run the build, source the repo install overlay, then run the week's commands in the same shell. Add preflight assertions for `ros2`, `px4_msgs` and `uavx_eval`. Use that wrapper in both the plan and loop config, then test it through the exact `wsl.exe` invocation rather than from an interactive Ubuntu prompt.

### Finding 2, critical: the W3 gate in the loop is not the gate in the plan

**The plan says** the relay run requires `delivery_ratio_by_node.uav_4>=0.95`, and the direct-only control requires `delivery_ratio_by_node.uav_4<=0.05`. It explains that the aggregate direct-only ratio should be about 0.75 because three of four drones still have a direct link.

**The problem** `.claude/weekly-loop.md` drops the positive per-node check and changes the control to aggregate `delivery_ratio<0.5`. `stage-1/decisions.md` repeats the aggregate form. A correct direct-only run near 0.75 fails the supervisor gate. A broken relay run can pass without showing that `uav_4` delivered anything, because traffic from the other three nodes can satisfy the aggregate ratio and hop calculation. There is more drift too: the loop adds `colcon test-result --verbose` and an every-week build that the plan's literal gates do not contain. The added test-result command is good, but it proves there is no single gate contract.

**Fix** Keep one canonical gate list and include it from the other two files. W3 must check `uav_4` in both runs, require a nonzero packet count, and use `==0` for the direct-only leak check because distance beyond `r_max` is defined as a deterministic drop. Keep `colcon test-result --verbose` in the canonical list. Add a test that compares the rendered plan gates with the loop config so this cannot drift again.

### Finding 3, critical: W5 can go green without submitting anything usable

**The plan says** W5 ends with five deliverables in one email, sent on 26 September. Its only gate is `python3 scripts/check_submission.py`. The loop separately marks sending email as a human-only blocked action.

**The problem** The autonomous loop cannot complete the stated W5 goal. Its gate can pass before an email is sent, and there is no later mechanical or human acknowledgement in the state machine. The planned checker only tests existence, page count, video duration and a checklist that claims the files are present. A blank seven-page PDF, a corrupt three-second MP4, an archive missing half the source and a hand-written checklist can all satisfy that contract. `fresh_install_test.sh` "must have passed once" but is not a gate command and is not tied to the source commit being submitted.

**Fix** Split W5 into a machine gate and a named human handoff. The machine gate should extract required section text from the PDF, decode or probe the whole video, inspect the archive manifest, reject build products and secrets, verify `INSTALL.md`, and require a fresh-install receipt containing the submitted commit SHA. It should also build the final email draft and attachment manifest. The supervisor then halts for the human to send it. A human-created receipt with send time and message ID closes the submission state. Build this checker and test the video path before W5.

### Finding 4, critical: W1's headless path and its fallback both fail the chosen architecture

**The plan says** the stack uses PX4's Gazebo Classic `sitl_multiple_run.sh`, all scenario gates run headless through `HEADLESS=1`, and W1 falls back from four vehicles to two while carrying the rest of the plan forward unchanged.

**The problem** The installed PX4 v1.15.4 script does not read `HEADLESS`. After starting `gzserver` and the vehicles, it always starts `gzclient` in the foreground. This machine's recorded failure mode says the Gazebo GUI path can terminate the WSL distro, so the documented W1 launch can kill its own gate. Dropping to two vehicles is not a recovery. D4 needs an anchor, a relay and two survey vehicles. After a relay kill, two vehicles leave no spare that can take the relay role while a mission vehicle remains. W3 and W4 cannot be carried forward unchanged.

**Fix** Lock one tested headless launch path in W1. It may use a repo-owned adaptation of the PX4 launcher, but the gate must prove that `gzserver`, four distinct PX4 instances, four ROS namespaces and the agent stay alive with no `gzclient` process. Keep the launch in the foreground with a timeout and cleanup trap. Remove the two-vehicle continuation. If four vehicles cannot pass the resource gate, halt and change the architecture before W2.

### Finding 5, significant: every scenario gate trusts a stale or invented `latest.jsonl`

**The plan says** a scenario command runs first, then a separate checker reads `runs/latest.jsonl`. It also says every run is seeded and replayable.

**The problem** Nothing in the gate contract binds `latest.jsonl` to the scenario command that just ran. A prior green file survives a crashed simulation. A metrics collector can also write the requested values without seeing four vehicles, the injected kill or a completed mission. W4 is especially easy to false-green: no separation samples can look like zero violations, an event that never fired can leave a default reconnect time, and an initial role assignment can satisfy `role_changes>=1`. This is the same failure shape as the Gazebo package check that passed without a `gzserver` artifact.

**Fix** Define a run record contract now. Before launch, remove or invalidate `latest`; write a unique run ID, scenario path and hash, seed, commit SHA, start time, end time, completion state and observed vehicle IDs. Record sent and delivered packet counts, pose sample counts, the exact injected event and its observed timestamp. Publish `latest.jsonl` atomically only after every required process exits cleanly. The checker must reject a scenario mismatch, missing event, zero denominator, incomplete run or file produced before the current launch.

### Finding 6, significant: the thresholds do not yet describe a passable experiment

**The plan says** W2 requires `coverage_fraction>=0.95`; W3 requires 0.95 delivery and `mean_hop_count>1.0`; W4 requires reconnect within 30 seconds and 0.90 delivery after recovery. It allows W2 to shrink the survey area and W4 to move the reconnect threshold after doing the arithmetic.

**The problem** W2 has no locked area size, cell size, footprint or rule saying coverage comes from actual vehicle poses rather than the planned path. Shrinking the box until it passes moves the goal instead of recovering the implementation. For W3, a link in the 300 m to 500 m fade band has probability `p=(500-d)/200`. Two equal lossy hops need `p^2>=0.95`, so each hop needs `p>=0.9747` and `d<=305.1 m`. At 350 m per hop, correct end-to-end delivery is only 0.5625 before any protocol loss. No scenario coordinates or packet count are locked. Aggregate hop count is weak too. With three direct one-hop streams and one two-hop stream, the expected mean is only 1.25, and counting HELLO or LSA traffic could make it pass without relayed application data. The reconnect limit has the same problem. A 5 second timeout, 1 second election, 200 m flight at 10 m/s and 5 second stability window already total 31 seconds. Letting W4 edit its own threshold is a moving gate.

**Fix** Freeze baseline coordinates, survey dimensions, grid size, traffic rate, run duration and metric windows before execution. Derive coverage from sampled poses. Put relay links inside `r_full` with margin, send at least 100 application packets per node, require `uav_4` delivery of at least 0.95 and its delivered-data hop count of at least 2, and require zero direct-only deliveries from it. Fix HELLO period, neighbour timeout, election bound, relay target, speed and stability window, then set reconnect time from their sum plus stated margin. Do not let a week-agent change a gate it is trying to pass.

### Finding 7, significant: the tx/rx seam test is not specified well enough to enforce the seam

**The plan says** unit tests include "a test asserting no swarm node subscribes to another vehicle's topics directly." The architecture makes the stronger claim that every swarm node reads only its own `rx` and writes only its own `tx`.

**The problem** The test sentence covers only one bypass. A node can use a shared broadcast topic, subscribe to simulator poses, call another node through a service or action, remap a legal-looking topic at launch, construct a topic name dynamically, publish straight to the GCS, or read another `/px4_*` namespace. None is necessarily a subscription to "another vehicle's topics." The GCS does not have a defined tx/rx endpoint, so an implementer may reasonably connect it straight to every router. A source grep alone misses remaps and dynamic names. A ROS graph check alone misses dormant code paths.

**Fix** Name every process covered by the rule and give the GCS a modelled endpoint such as `/uavx/gcs/tx` and `/uavx/gcs/rx`. Define an endpoint allowlist per node: own PX4 namespace, own tx/rx pair and nothing else carrying swarm data. Test the live ROS graph in both W3 scenarios for publishers, subscriptions, services and actions, including resolved remaps. Add a source-level check that only the link layer imports the ground-truth pose interface or creates endpoints for more than one vehicle. Fail on any SwarmPacket transport outside the allowlist.

### Finding 8, significant: the relay election has no executable state machine

**The plan says** each node decides roles from its own graph view, and when a node loses its GCS path "the swarm elects" the survey drone with the lowest total repositioning distance.

**The problem** The node that lost the path cannot tell the GCS-connected component what it needs, and two components can have different link-state views. Deterministic tie-breaking does not give them the same candidate when their candidate sets differ. "Repositioning distance" also has no meaning until the plan defines one or more relay target positions. LSAs carry neighbour tables, not positions, so a node cannot calculate the stated score from the described protocol. Sequence numbers, flood duplicate suppression, directed versus bidirectional links, election epochs and stale role expiry are all missing. Two competent implementations will behave differently, and one can oscillate forever at the probabilistic range boundary.

**Fix** Write the W4 state machine before W2 starts. Pick the coordinator in the still GCS-connected component, define fixed or computed relay slots, candidate eligibility, the position data allowed in role messages, election epoch, acknowledgement, lease timeout and completion condition. Define HELLO and LSA periods, sequence handling, link symmetry rule and hysteresis. The recovery scenario must name the old relay, the expected eligible replacements and the exact topology before and after the move.

### Finding 9, significant: the safety and role gates do not earn the rubric rows assigned to them

**The plan says** safety is altitude layers, a monitor and a yield rule. W4 gates `separation_violations==0` and `role_changes>=1`. The metric table also promises `min_pairwise_separation_m` and `time_in_connected_topology`.

**The problem** There is no safety scenario that makes the yield rule act. Distinct cruise altitudes can keep all aircraft apart while the yield code is absent. The gate does not check the promised minimum separation, collision contacts, number of samples or takeoff and landing. It also does not say how delayed radio data supplies relative position and velocity to the predictor. `role_changes>=1` can count startup assignment instead of autonomous reassignment, and `time_in_connected_topology` is never listed as a W4 output or gate. Mission completion has a W2 artifact, communication has W3 artifacts and innovation has a W5 writeup. The 20% role row and 10% safety row are the ones with no convincing evidence behind their claimed metrics.

**Fix** Add a deterministic encounter that activates the yield rule, plus a test for simultaneous takeoff and landing. Gate on a nonzero pose sample count, no simulator collision contacts, `min_pairwise_separation_m>=min_separation_m` and at least one logged yield with the correct vehicle ID. For roles, require a named transition from the killed relay to a different live vehicle and a sustained connected-topology window. Either produce and gate `time_in_connected_topology` or remove it from the metric spine.

### Finding 10, significant: W3 is the first overrun, and W5 has no recovery room

**The plan says** one person has 4 to 6 hours a day. W3 builds messages, a stochastic link layer, link-state routing, two integrated scenarios, four metrics and the seam test in seven days. W5 has 23 to 26 September for the proposal, video, package and fresh install.

**The problem** W3 is roughly 45 to 60 hours of work once process supervision, packet formats, routing timers, metrics and SITL debugging are counted. Its available range is 28 to 42 hours. W4 is another 40 to 55 hours if all three failure types and safety are kept. W5 has 16 to 24 hours, not the five days claimed in D3, while a proposal, edited video, clean install and archive check can take 22 to 35 hours. The "never take days from W3" rule has nowhere real to take them from.

**Fix** Use W1 slack after the flight gate for `uavx_msgs`, the foreground process runner and resource logging. Put the run-record contract, checker skeleton and pure link-model tests in W2. Limit W4 to the relay kill needed for Stage 1 and move `comms_blackout` and `gps_degrade` to Stage 2. Draft proposal sections and figures as each weekly metric lands. Run the fresh install and a recording dry run by W3. Leave W5 for final reruns, editing, packaging and the human email handoff.

### Finding 11, significant: the installed stack is present but it is not reproducible or resource-gated

**The plan says** D1 is a pinned version set and W5 repeats installation from nothing. The setup README says the bridge is Micro XRCE-DDS Agent v2.4.2.

**The problem** The live verifier passes and reports PX4 v1.15.4, but the scripts select the newest future `v1.15.*` tag, track moving `release/1.15` branches for `px4_msgs` and `px4_ros_com`, and fall back to `main`. `02-ros2-humble.sh` also downloads the latest apt-source package. The actual agent script installs v2.4.3, not the README's v2.4.2, and `verify.sh` only checks that an agent binary exists. A fresh install on 26 September can therefore build a different stack from the one used for the evidence. The live WSL limit is 11,821 MiB RAM with 3,072 MiB swap. No gate measures the four-vehicle peak or detects swap thrashing. Build outputs also default onto `/mnt/c`, the mount already known to drop long work.

**Fix** Record exact PX4 tag, bridge tag, message and bridge commit SHAs, and the ROS and Gazebo package versions from the working machine. Remove the `main` fallback and make verification compare exact versions. Correct the README to v2.4.3. In W1, run a timed four-vehicle soak, record peak WSL memory and fail above 9 GiB so there is about 2.8 GiB of headroom. Keep long build and install outputs on the WSL ext4 filesystem, keep scenario processes attached to the gate shell, and fail if swap use grows during the smoke run.

## Verdict

NOT READY. The local stack exists, which closes the biggest uncertainty from round 1, but the autonomous execution contract is broken before implementation starts. The supervisor shell cannot see ROS, its W3 gate rejects the correct control result, the selected multi-vehicle launcher is not headless and W5 has no state that means "submitted." Several later gates can also pass stale metrics or zero-work runs. Fix those contracts first. The routing, role and safety details then need one more pass so the week-agent is implementing a fixed design instead of making protocol choices under deadline.

## For the next round

Check one canonical gate definition rendered identically into the plan, decisions and loop config. Run its shell preflight through `wsl.exe`. Then check the fixed four-vehicle headless launcher contract, scenario provenance fields, W3 per-node controls, the fully derived reconnect budget, the role state machine and a safety scenario that actually triggers yielding. Recalculate the weeks after Stage 2-only failure modes and early document work have moved.

Findings: 11
