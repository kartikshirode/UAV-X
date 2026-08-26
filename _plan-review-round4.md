# Plan review, round 4 (Codex)

Reviewed: `context.md`, `stage-1/plan.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `stage-1/setup/`, `_plan-review-round1.md`, `_plan-review-round2.md`, `_plan-review-round3.md`, `stage-1/architecture.md`, `stage-1/human-preflight.md`, `handoff.md`, `scenarios/run-record.schema.json`, and every current file under `scripts/`, at HEAD `6376de89bd2163b6d99799ac10273c387bb56052`.

## Still open from earlier rounds

- Round 2 finding 7 and round 3 finding 2 are still open. The seam now has a static and snapshot split, but its W3 process manifest requires W4 code, it is not rerun after W4 adds roles, and several endpoint bypasses still pass. See finding 4.
- Round 2 finding 3 and round 3 finding 3 are still open. Receipts no longer follow moving HEAD, but the checker trusts the archive hash written in the manifest without hashing the archive. See finding 5.
- Round 2 finding 5 and round 3 finding 6 are still open. The run schema is stronger, but W5 still accepts evidence by filename without validating a run. See finding 6.
- Round 2 finding 10 and round 3 finding 9 are partly open. Work moved out of W3 and W4, but the W3 install and recording dry runs are still absent from the executable gate. See finding 1.
- Round 2 finding 11 and round 3 finding 7 are partly open. The four git repositories are pinned and preflight runs `verify.sh`, but ROS installation still floats while its locked version is never checked. See finding 8.
- Round 3 finding 4 is partly open. Preflight blocks on a receipt, but it does not validate most receipt fields and W5 does not enforce the recorded delivery budget. See finding 7.
- Round 3 finding 5 is still open. An integrated mission is described, but no gate runs it and its frozen geometry does not hold over the mission. See findings 1 and 2.
- Round 3 finding 10 is still open. `check_docs.py` reports agreement while the handoff, plan and decisions carry stale or conflicting state. See finding 9.

## Findings

### Finding 1, critical: the sole gate accepts W3 and W4 without their promised proof work

**The plan says** the fresh-install dry run and 60 second recording dry run are both in the W3 gate. It calls `mission_integrated.yaml` the only scenario that proves the swarm and says the proposal and video point at that run.

**The problem** `gate_w3` contains neither dry run. `gate_w4` never runs `mission_integrated.yaml` and has no integrated coverage, observation delivery or strip-reassignment assertion. It runs only the isolated `relay_kill`, `encounter` and `encounter_noyield` scenarios. Because `gate.sh` is the sole acceptance contract, W3 can hand both late risks to W5 and W4 can go green without the one proof of concept the submission claims. The file consistency checker only looks for a small set of numeric threshold strings, so it misses this action-level gate drift.

**Fix** Add explicit W3 commands for a fresh-install dry-run receipt and a decoded 60 second recording receipt, with freshness tied to the current source. Add `mission_integrated.yaml` to W4 and gate its final coverage, observations generated during the outage and delivered after recovery, named role transfer, restored multi-hop path, reconnect time and full-run safety. Make `check_docs.py` compare the scenarios and named receipts promised by each week against the scenarios and checks actually invoked by `gate.sh`.

### Finding 2, critical: the integrated mission does not preserve the topology or timing it claims

**The plan says** every point in the 50 m by 210 m survey box is at least 250 m from `uav_1`, so killing `uav_2` disconnects both surveyors. It then reuses the station-keeping election, slot and 32.2 second recovery calculation.

**The problem** The closest survey point is not a corner. At `(405, 0, 50)`, distance to `uav_1` is 240.8 m; at altitude 60 it is 241.9 m. Both sit in the probabilistic fade band and violate the architecture rule that an absent link must be at least 300 m. A direct surveyor to `uav_1` link can therefore appear during the relay kill, making the outage and election seed-dependent. `check_geometry.py` returns 0 because it checks only the five common station-keeping positions, not the integrated box or moving trajectories. The reused recovery result is not frozen either: during the mission both candidates are moving, so candidate distance, the member that stays, the midpoint slot and flight time need not match `relay_kill`. Timing is also suspect. Five 210 m lanes plus four 10 m turns are about 1,090 m, or 109 seconds at the frozen speed even for one surveyor before a short ingress. Two surveyors split that work, yet the kill is at 150 seconds. Nothing currently proves there is unfinished coverage when the relay dies.

**Fix** Extend the geometry checker over every relevant trajectory segment and the exact positions at the kill. Pick a box and kill phase where all required links stay at or below 175 m, all forbidden links stay at or above 300 m, both surveyors are still working, and the disconnected component remains connected internally. Recompute the winner, slot, flight distance and recovery budget from those kill positions. Gate a nonzero amount of unfinished coverage and observation traffic on both sides of the failure.

### Finding 3, significant: the outage buffer guarantees the data-loss claim is false

**The plan says** observations generated during the outage are buffered and delivered after recovery, with none silently dropped.

**The problem** Routing buffers only 50 packets per node and observations run at 5 Hz. That is 10 seconds of storage. The derived outage is 32.2 seconds, which produces 161 packets per continuously surveying node, while the gate permits 45 seconds, or 225 packets. Oldest-first dropping is therefore required by the frozen design long before the route returns. A correct implementation cannot satisfy the integrated no-loss statement.

**Fix** Size the queue from the gate, not the nominal result. Require at least `ceil(5 * 45) = 225` observation packets per active origin, plus a stated margin or a bounded persistent queue. Add counters for generated, queued, evicted and delivered-after-recovery packets, then gate zero evictions for this scenario.

### Finding 4, critical: the seam gate can reject correct W3 code and accept real bypasses

**The plan says** the seam checker enforces the endpoint allowlist statically and on the resolved live graph, and its nine fixtures prove every named bypass is caught.

**The problem** The snapshot manifest always requires four `role_manager` nodes, but `uavx_roles` is not produced until W4. A correct W3 `relay_required` graph therefore cannot satisfy the W3 seam pass. The reverse hole is worse: W4 adds those role managers and never runs the seam checker again. They can bypass the radio after W3 is accepted. The live code ignores arbitrary publisher and subscriber topics such as `/swarm/broadcast`, even though the allowlist says no such endpoint is legal. It discards topic types, so it cannot enforce the stated ban on `SwarmPacket` outside tx/rx. It also skips any node whose name merely contains `/link_layer`, `/metrics_collector` or `/scenario_runner`, rather than matching the exact outside processes. The fixture runner only compares zero versus nonzero. In the current clean tree it exits 1 because `uavx_ws/src` is absent, yet all eight negative cases are printed as successful catches because the same missing-directory failure is accepted as evidence. It never checks the stated diagnostic.

**Fix** Give each scenario an exact process manifest and exact outside-process names. Reject every publisher, subscriber, service and action outside that process's full allowlist, retain message types in graph snapshots, and reject `SwarmPacket` on every other topic. Run the static pass after W4 code exists and the live pass on the integrated W4 graph. Make fixtures create their own minimal source tree, require the expected violation text and fail on setup errors. Add cases for a W3 graph without role managers, `/swarm/broadcast`, an unknown helper using its own tx and a node name that only contains an outside-process substring.

### Finding 5, critical: W5 trusts a claimed archive hash instead of the submitted archive

**The plan says** the source manifest binds frozen commit `C`, the archive, the fresh-install receipt and the send receipt.

**The problem** `check_submission.py` imports `hashlib` but never hashes the archive. It reads `archive_sha256` from `source-manifest.json` and only checks that the fresh-install receipt repeats the same string. Two invented matching strings pass even if `uavx-source.zip` has different bytes. The manifest does not name the selected archive, and the checker takes the first matching glob, so multiple archives are ambiguous. It also proves only that commit `C` exists, not that archive contents came from `C`. The sent receipt binds to `C`, not to the actual proposal, video, install file and source archive that were attached.

**Fix** Put the exact archive filename in the manifest, require exactly one candidate, compute its SHA-256 from its bytes and compare it with the manifest and fresh-install receipt. Build from `git archive C` or embed and verify a tree manifest so the archive contents are tied to `C`. Generate one attachment manifest covering the PDF, video, install file and source archive, then bind the sent receipt to that manifest hash.

### Finding 6, significant: W5 still counts filenames as run evidence

**The plan says** provenance is validated before any metric is read and W5 requires a recorded run per scenario.

**The problem** The W5 loop only asks whether a `*.jsonl` filename contains five substrings. An empty file, stale run, wrong scenario hash, crashed run or evidence from a different source tree counts. `mission_integrated` and `encounter_noyield` are not in the required list. No W5 path calls `uavx_eval.check`, validates the schema, checks the archived commit or confirms that the proposal's numbers refer to these records. The stronger run schema therefore protects weekly checks only if the future checker implements it; it does not protect the final package.

**Fix** Freeze an exact evidence manifest listing every required run ID, including the integrated mission and negative control. Re-run the real provenance and scenario-specific validator on each file in W5, require complete duration and observed events, and bind every run's source tree to the archived source. Make proposal metrics cite those exact run IDs.

### Finding 7, significant: human preflight can pass without the recorded human work or a deliverable route

**The plan says** every human dependency has a dated receipt and W5 checks package size against the recorded attachment budget.

**The problem** Preflight checks `registered.done`, an eligibility string and an attachment limit, but most other sections only need to be truthy objects. It does not require the registered email, eligibility date, clarification join date, organiser send date, answers or fallback, delivery route or attachment fallback. `check_submission.py` never reads `human-preflight.json`, totals attachment sizes or checks the delivery route. A plausible-looking partial JSON passes preflight, and an oversized package can still reach W5 with no accepted way to send it.

**Fix** Add a JSON schema for the human receipt with the fields and dates shown in `human-preflight.md`, then validate it in preflight. In W5, total the exact attachment-manifest bytes, compare them with `attachment_limit_mb`, and require a recorded organiser-approved route or the explicit fallback for anything over budget.

### Finding 8, significant: the version lock still allows a different ROS stack

**The plan says** `versions.lock` is the authority, setup installs it and `verify.sh` compares the installed stack with every locked value.

**The problem** The four git checkouts and Gazebo package are now checked. ROS is not. `02-ros2-humble.sh` resolves the latest apt-source release and installs unversioned Humble packages. `versions.lock` records `ros_core_version`, Ubuntu and the WSL kernel, but `verify.sh` compares none of them. The run schema's `versions` block also omits `px4_ros_com_sha` and the ROS package version. A fresh install can use different ROS binaries, pass preflight and label its run with an incomplete version object. The setup table still says script 03 uses the osrfoundation repository while the script and later prose correctly use Ubuntu universe.

**Fix** Either install the locked ROS package versions or declare the exact set that can still be obtained and block if it differs. Compare every authoritative lock field that affects results, and require the same observed fields in run records. Remove lock entries that are intentionally informational so "authority" has one meaning. Correct the setup table and the stale "newest v1.15.x" header.

### Finding 9, significant: the document checker reports green on state the loop will misread

**The plan says** `check_docs.py` prevents contradictory instructions from reaching a week-agent.

**The problem** It exits 0 while `stage-1/plan.md` still labels registration and eligibility "not blocking", `stage-1/decisions.md` still says `gh` blocks the repository step and round 2 review is open, and `handoff.md` says round 3 has not run, tells the reader to use 4 to 6 vehicles and lists three round 3 findings as still open after their fixes. The stale-state regex only knows rounds 1 and 2. The distance check uses a hard-coded exception list, so the stale-number class that motivated it can return whenever a new derived number is added. These are the files the autonomous loop hands to each agent.

**Fix** Update the three documents to one current state. Replace review-specific regexes with a single machine-readable current round and status, require D4's exact four vehicles wherever an execution count is stated, and compare blocking human items with preflight. Derive allowed distances by named formula or structured scenario data rather than a hand-maintained exception tuple.

## Verdict

NOT READY. The common station-keeping topology is now correct, the headless launcher and versioned git checkouts are real improvements, and the syntax checks are clean. The executable contract still omits the only integrated proof, that proof's moving geometry can recreate the fatal shortcut from round 3, and its fixed buffer cannot retain the claimed data. The seam and submission checkers also have false-green paths of the exact class this audit is meant to catch. Fix findings 1, 2, 4 and 5 before implementation starts; the remaining findings close evidence, reproducibility and delivery risks that otherwise surface in the last two weeks.

## For the next round

Run the integrated geometry checker over exact kill-time positions and the whole survey path, then show the integrated W4 run and the two W3 dry-run receipts in `gate.sh`. Exercise the seam suite with setup failures separated from expected violations and a real W3 manifest. Finally, mutate one byte of the source archive and one required run record and prove W5 rejects both, then test the attachment budget with an oversized package.

Findings: 9
