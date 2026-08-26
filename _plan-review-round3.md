# Plan review, round 3 (Codex)

Reviewed: `context.md`, `stage-1/plan.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `stage-1/setup/`, `_plan-review-round1.md`, `_plan-review-round2.md`, `stage-1/architecture.md`, `handoff.md`, `scenarios/run-record.schema.json`, and every current file under `scripts/`, at HEAD `71d245724b8b3953a334d2b21416d79c261d0d00`.

## Still open from earlier rounds

- Round 2 finding 3 is still open. The submission checker is much better, but the W3 fresh-install receipt cannot match W5 HEAD after W4 and W5 commits, and committing the sent receipt changes HEAD again. See finding 3.
- Round 2 finding 5 is still open. The schema exists, but packet denominators and injected events are optional, elapsed simulation time is absent, and W5 accepts run filenames without validating their contents. See finding 6.
- Round 2 finding 7 is still open. The seam script exists, but W3 invokes its live pass with no graph running, and the pass neither implements nor permits the stated allowlist correctly. See finding 2.
- Round 2 finding 9 is still open. An encounter was added, but zero collision contacts can still mean no contact monitor and the gate does not require the named `uav_4` yield. See finding 8.
- Round 2 finding 10 is still open. Work moved out of W3, but the fresh install and recording checks are not in its gate, while W3 and W4 are still above the likely solo budget. See finding 9.
- Round 2 finding 11 is still open. The current machine matches `versions.lock`, but the installers do not install from that lock and the weekly preflight never runs the version verifier. See finding 7.

## Findings

### Finding 1, critical: killing the relay does not disconnect `uav_4`

**The plan says** `relay_kill.yaml` kills `uav_2`, after which `uav_3` is elected and flies to `(655, 0, 45)` to rebuild `uav_4 -> uav_3 -> uav_1 -> gcs`. Section 6 says `uav_4` reaches the GCS only through `uav_2` before the kill.

**The problem** The full distance matrix contradicts that topology. At the frozen starting coordinates, `uav_1` to `uav_3` is 369.5 m and `uav_3` to `uav_4` is 394.6 m. Both are inside `r_full = 400 m`. The chain `uav_4 -> uav_3 -> uav_1 -> gcs` already exists before `uav_2` dies, so killing `uav_2` causes no outage and no election. The slot rule is inconsistent too. It says to use the connected node nearest `uav_4`, which is `uav_3` at 394.6 m, but the worked scenario silently uses `uav_1` instead. `check_geometry.py` reports green because it checks only eight hand-picked pairs and omits these two. It is also never called by `gate.sh`.

**Fix** Choose coordinates whose complete adjacency graph has the intended edges and no others. The checker must build all ten pair distances, calculate the shortest path before the kill, remove `uav_2`, prove `uav_4` is then disconnected, and prove the moved `uav_3` restores the named path. Define the relay slot from a stationary upstream attachment node, not "nearest connected node" if that node may itself be the moving candidate. Call the geometry check from W3 and W4 before launching SITL.

### Finding 2, critical: the W3 seam gate cannot run, and it still misses bypasses

**The plan says** `check_seam.sh` performs a static pass and a live graph pass in both W3 scenarios, with remaps resolved.

**The problem** `gate_w3` calls `check_seam.sh` before either scenario starts. Preflight has just required that no simulator, agent or PX4 process is running. The seam script therefore reaches `ros2 node list`, finds no graph and exits nonzero. W3 can never pass. Moving the call into a running scenario is not enough by itself. The static pass bans `VehicleLocalPosition` and `VehicleOdometry` outside the link layer, even though section 1 explicitly allows a mission node to use its own PX4 namespace and the mission executor needs its own state estimate to fly. The live pass skips any swarm node whose node name lacks a vehicle id, ignores a shared topic such as `/swarm/broadcast`, does not check whether a node publishes to `rx` or subscribes to `tx`, and treats normal ROS parameter services under `/uavx/` as illegal swarm services. Actions are claimed but not checked.

**Fix** Run the static pass on its own, then have the scenario runner take a live graph snapshot while each W3 scenario is up. Identify every expected swarm process from a manifest and fail on an unknown or missing process instead of skipping it. Check exact publisher, subscriber, service-client, service-server, action-client and action-server allowlists after remapping. Ban Gazebo ground truth from swarm nodes, but allow each node's own PX4 state estimate. Exempt standard ROS parameter services by exact name. Add fixtures for all six named bypasses and one clean graph before trusting this gate.

### Finding 3, critical: the submission receipts are tied to a moving commit

**The plan says** the fresh install runs in W3, its receipt carries the submitted commit SHA, and `check_submission.py` requires both the fresh-install and sent receipts to match current HEAD.

**The problem** W4 and W5 necessarily add code, results and submission files after the W3 install, so that receipt is guaranteed to be stale by W5. The sent receipt is self-invalidating under this repo's commit rule: write it with current HEAD, commit the new file, and HEAD changes. The checker then rejects the receipt on the next run. `gate_w5` also converts the checker's meaningful exit 2 into a generic exit 1 through `|| gdie`, so the supervisor cannot distinguish "package ready, waiting for human" from "package broken." A nonempty `to` field passes even if the email went to the wrong address.

**Fix** Freeze source at commit `C`, create the source archive from `C`, and put `C` plus the archive SHA-256 in its manifest. Run the final fresh install from that archive after code freeze, and bind its receipt to `C` and the archive hash rather than mutable repository HEAD. Bind the sent receipt to the exact attachment manifest and require `to == pushpak_gc2026@aero.iitb.ac.in`. Let later packaging or receipt commits move HEAD without invalidating the source claim. Preserve exit 2 through `gate.sh` so the loop has a real human-handoff state.

### Finding 4, critical: registration and eligibility are treated as optional notes

**The plan says** registration, joining the clarification channel, sending the organiser email and confirming eligibility are "Open items, not blocking." The loop is forbidden from doing any of them.

**The problem** An unregistered or ineligible entrant has no valid submission, however good the simulation is. Eligibility can disqualify the entry at any stage, and registration is the competition's entry mechanism. These are external human dependencies, not harmless backlog. The source and video delivery method is still unanswered too, while a three-minute video can exceed an email server's attachment limit. Waiting until W5 to discover that the intended attachment cannot be delivered risks the submission itself.

**Fix** Put a human preflight before W1. It needs dated receipts for registration, the eligibility declaration, joining the clarification channel and sending the organiser questions. Record the answer or the stated fallback for repository link, archive and video delivery. The loop must not start until registration and eligibility are confirmed. Add a W5 size and attachment-manifest check against the chosen email method.

### Finding 5, significant: the plan never demonstrates one working disaster mission

**The plan says** `survey_baseline` measures coverage with communications disabled, `relay_required` uses station-keeping with synthetic application packets, and `relay_kill` tests recovery on the common fixed geometry.

**The problem** The official task couples these behaviors: survey a disaster zone, relay its data through a multi-hop network and reconfigure while failures occur. The plan proves each subsystem in isolation but never runs them together. Worse, the recovery state machine marks the elected relay's survey strip unassigned. Even if the network recovery worked, the swarm would abandon part of the mission and no gate would notice. A demo that shows stationary dots recover a synthetic packet stream does not prove a working disaster-response swarm.

**Fix** Add one integrated submission scenario, or turn `relay_kill` into it. Vehicles must cover the frozen area, send observation packets through the modelled mesh, lose the relay, restore the route, redistribute or finish the abandoned strip and complete the mission safely. Gate its post-failure coverage, delivered observation count, reconnect time and separation. Use that same run ID in the proposal and demo video so the five deliverables point to one proof of concept.

### Finding 6, significant: run provenance can still go green without the claimed run

**The plan says** the schema requires the provenance needed before a metric is read, and W5 requires a recorded run for every scenario.

**The problem** `run-record.schema.json` does not require `app_packets_sent_by_node`, `app_packets_delivered_by_node`, `delivered_hops_by_node` or `injected_events`. It has no scenario duration or simulated elapsed time, so a 20 second W3 run can send the minimum 100 packets and claim completion instead of running the frozen 240 seconds. It requires only one observed vehicle at schema level. The future checker may add conditional rules, but those rules are not frozen anywhere an agent can implement without guessing. `check_submission.py` is weaker still: any empty file whose name contains `relay_kill` counts as evidence, and it never validates the schema, scenario hash, metrics or commit. The gate also allows `UAVX_RECONNECT_BUDGET_S` to replace the frozen 45 second limit from the environment.

**Fix** Add scenario-specific required fields and constraints now, either with JSON Schema conditionals or an explicit table for `uavx_eval.check`. Record requested duration, simulated elapsed time, expected vehicle ids and monitor sample counts. Require a clean git tree at launch or record a source-tree hash, since HEAD alone ignores uncommitted code. Make W5 rerun the real validator on every evidence file and match each one to the archived source commit. Remove the reconnect environment override. A frozen gate cannot include its own escape hatch.

### Finding 7, significant: the version lock verifies this machine but does not reproduce it

**The plan says** exact versions are pinned in `versions.lock`, setup installs them and every gate attributes results to that stack.

**The problem** The live verifier passes today, which is useful. The install scripts still ignore the lock. `04-px4.sh` resolves the newest future `v1.15.*`; `05-ros2-bridge.sh` clones moving `release/1.15` branches and still falls back to `main`; ROS apt packages float. A fresh install can spend hours building the wrong commits and only fail afterwards when `verify.sh` compares them. Weekly `gate_preflight` never calls that verifier, so gates can run after a checkout changes. The run-record contract says `versions` is the contents of the lock, not versions observed from the running files, which can mislabel a result from a different stack.

**Fix** Load `versions.lock` in the setup scripts. Clone or fetch, then checkout the exact PX4, agent, `px4_msgs` and `px4_ros_com` SHAs before building; delete the `main` fallback. Install locked apt versions where available and report a clear blocked state if the package repository no longer serves them. Run the version verifier in weekly preflight. Populate run records from observed git SHAs and package queries, then compare that object with the lock. Also correct the setup table that still says script 03 installs from osrfoundation when the script correctly uses Ubuntu universe.

### Finding 8, significant: the safety gate can pass without a working safety monitor

**The plan says** `encounter.yaml` forces `uav_4` to yield, and the safety evidence is a yield event, minimum separation and collision contacts.

**The problem** The encounter has no frozen start points, end points, start time or duration, despite the file claiming every coordinate is fixed. Its gate asks only for `yield_events>=1`, not a yield by `uav_4`, and does not check `min_pairwise_separation_m` or pose samples for this scenario. `collision_contacts==0` passes when no Gazebo contact source was ever attached. A logger can emit one yield event while the flight command continues unchanged and still meet all three current assertions.

**Fix** Freeze the two trajectories and timing. Require the named `uav_4` yield, a nonzero hold duration, enough pose and contact-monitor samples, `min_pairwise_separation_m>=10`, zero contacts and successful completion by both vehicles. Add a control run or integration test with yielding disabled that proves the same paths would violate separation. Without that negative control, the scenario does not show that the rule caused the safe result.

### Finding 9, significant: W3 and W4 still exceed the solo budget

**The plan says** W3 builds the link layer, router, GCS and two scenarios, then also runs a fresh install and recording dry run. W4 builds the election state machine, failure injection, three safety layers and two scenarios. Each week has roughly 28 to 42 hours.

**The problem** Moving messages and the pure link model helped, but W3 still looks like 38 to 50 hours before the clean install and recording work. W4 is roughly 38 to 52 hours, and the integrated mission missing in finding 5 adds more. Neither auxiliary W3 task is in `gate.sh`, so the week can be accepted without doing them and hand the bill to W5. W2 now owns mission flight, metrics, the checker and link-model tests, so it has little spare room to absorb another slip.

**Fix** Build the generic scenario event injector in W1 with the runner. Move pure routing and election state-machine tests into W2 while they need no SITL, and make W3 only ROS integration of already-tested logic. Put the recording dry run in the W3 gate with a receipt. Treat the current fresh install as a dry run only; schedule the binding archive install after code freeze as required by finding 3. Remove or simplify anything else before adding another W4 deliverable.

### Finding 10, significant: the files handed to a week-agent still contradict the frozen plan

**The plan says** the design is frozen, four vehicles are mandatory, the repo-owned launcher replaces PX4's launcher and W5 has four days.

**The problem** `decisions.md` still says multi-vehicle work runs through PX4's `sitl_multiple_run.sh`, calls W5 five days, says W2 may cut vehicle count and lists round 2 as not done. Cutting vehicles contradicts D4 and makes the recovery topology impossible. `handoff.md`, which the loop config explicitly hands to agents, later says Ubuntu is not installed, nothing runs, WSLg has never produced a display, the reconnect limit is still an underived 30 seconds and round 2 has not run. Those statements appear after the correct current state in the same file. An autonomous week-agent is being told both to use and never use the crashing launcher, and both that the environment exists and that it does not.

**Fix** Replace the handoff with one current state section and delete the superseded machine snapshot and old honest-gaps list. Update D1 to name `sitl_multi.sh`, make W2's fallback "fix or halt," state four W5 dates rather than five days and close round 2. Add a consistency check for the locked launcher name, vehicle count, week dates and review state across plan, decisions, handoff and loop config.

## Verdict

NOT READY. The shell harness, single gate entry point, headless four-process launch and current version checks are real improvements, and I confirmed the WSL preflight and verifier both exit 0. The plan still cannot execute its two central proof runs. W3 asks for a live graph before starting one, and W4 kills a relay that is already bypassed by a full-range link. W5 then binds receipts to a commit that must keep changing. Fix those three contracts before implementation. The integrated mission, provenance, safety and schedule issues are the next layer; leaving them in place would produce green subsystem tests without the proof of concept the organisers asked for.

## For the next round

Recompute and print the complete topology before and after the relay kill, then run that checker from the gate. Exercise `check_seam.sh` against one clean live graph and six deliberate bypass fixtures. Walk the source-freeze, archive, fresh-install and sent-receipt sequence through actual commits without a circular HEAD comparison. After that, validate one integrated mission record end to end and recheck that the shortened W3 and W4 scopes fit 28 to 42 hours.

Findings: 10
