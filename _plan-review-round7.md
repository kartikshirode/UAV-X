# Plan review, round 7 (Codex)

Reviewed: `_codex-context.md`, `_codex-prompt.md`, `stage-1/plan.md`, `scripts/gate.sh`, `stage-1/architecture.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `scripts/`, `scenarios/run-record.schema.json`, `submission/human-preflight.schema.json`, `.claude/review-status.json`, and `_plan-review-round1.md` through `_plan-review-round6.md`, at HEAD `7e8498be81a40c9085cca6bc3ad9ace384db8318`.

## Still open from earlier rounds

Round 6 finding 1 is partly open. `fresh_install.sh` now writes a receipt, but it still does not execute the delivered `INSTALL.md` and its default target is not a clean dependency environment. See Finding 2. The other round 6 findings appear addressed.

## Findings

### Finding 1, critical: W1 cannot pass from the stated starting point
**The plan says** W1 owns the run harness, event injector, graph snapshot and resource sampling, and every chunk has its own gate. W1 runs `scenarios/harness_check.yaml`; `uavx_eval.check` is produced in chunk 2.2.
**The problem** `gate.sh` calls `python3 -m uavx_eval.check` in chunks 1.4, 1.5 and 1.7. That module does not exist until W2.2, so an otherwise correct W1 implementation fails its own gates. The repository also has no `scenarios/harness_check.yaml`, and chunk 1.4 lists only the runner and shell wrapper as outputs. `run_scenario.sh` therefore has no input file to run. The per-chunk promise is false before any simulator work starts.
**Fix** either build a small W1 provenance checker and defer metric checks until W2, or move `uavx_eval` into W1. Add `harness_check.yaml` to a named W1 chunk, with its duration, vehicles and injected event checked by that chunk's gate. Make the full `gate.sh 1` and each 1.x command runnable on a clean checkout before W2 begins.

### Finding 2, critical: the fresh-install receipt can certify the wrong install
**The plan says** W4.8 freezes the archive, installs it on a clean target somewhere other than this machine, follows the submitted installation path and then runs a four-vehicle smoke test.
**The problem** Without `UAVX_FRESH_DISTRO`, `fresh_install.sh` uses a directory on the already provisioned distro. It removes that directory but leaves the setup stamps under the existing home, so `setup-all.sh` can skip dependency installation. With a distro set, the script still removes a host-side `TARGET`, not a directory inside that distro. The step labelled "the submitted INSTALL.md path" actually runs `stage-1/setup/setup-all.sh`; `INSTALL.md` is not in `freeze_source.sh`'s archive paths. Finally, `check_submission.py` checks only the recorded target kind and name, not that the target exists. A broken install guide or missing target can leave a passing receipt.
**Fix** put the actual delivered `INSTALL.md` in the archive and execute it. Require a disposable distro or an isolated home with fresh setup stamps, and fail when that isolation is unavailable. Clear the target inside the chosen environment, assert it exists after unpacking and building, and record the target identity. Have the checker verify those facts and the smoke run, not just receipt text.

### Finding 3, significant: evidence paths can point outside the submission
**The plan says** `evidence-manifest.json` names the exact run record behind every scenario, and W5 revalidates those records against the frozen source.
**The problem** `check_submission.py` resolves each name as `REPO / rel`. An absolute path therefore escapes the repository, and a path containing `..` can do the same. The checker then validates that external JSON against the schema and `uavx_eval`, while the attachment manifest does not require those run records to be delivered. A locally prepared record can make the package pass even though the judge receives no copy of the evidence.
**Fix** accept only relative paths below a repository `runs/` directory, reject absolute paths and traversal, and require every named record to be included in the package or copied into a declared evidence directory. Add a fixture with an absolute external record and one with `../` traversal; both must fail.

### Finding 4, significant: traffic gates allow a nearly silent or empty run
**The plan says** `relay_required` and `direct_only` run for 240 seconds at 5 application packets per second per node, while the W4 records prove delivered-once observation sets.
**The problem** W3 requires only 100 packets from `uav_4`, although the stated rate produces 1,200. A broken node that sends one twelfth of the traffic can meet the ratio and hop checks. The W4 schema permits empty `generated_ids` and `delivered_ids`, and `observations.generated` has only a zero minimum. Set equality is then true for two empty sets, while `relay_kill` and `link_loss` have no positive observation count at all. `mission_integrated` asks for only 100 observations despite two survey origins running for most of 240 seconds.
**Fix** derive expected counts from each scenario's active duration and rate, with an explicit tolerance, and require per-origin non-empty sets. Make the evaluator reject a zero denominator and require the relevant minimum count before calculating delivery or equality. Add a low-rate fixture and empty-set fixtures that must fail.

### Finding 5, significant: the 11 GB memory claim has no ceiling
**The plan says** four PX4 instances, Gazebo and the swarm nodes must fit in roughly 11 GB without swapping.
**The problem** W1.7 checks only `peak_rss_mib>0`, `swap_used_mib==0` and at least ten samples. The submission checker repeats the swap and sample checks but never compares the peak with 11 GB. A record reporting 20,000 MiB resident memory and zero swap passes both gates, so the stated capacity risk is never tested.
**Fix** choose an exact ceiling in MiB from the target limit, enforce `peak_rss_mib` below it in W1 and W5, and add a fixture that raises the peak above the ceiling. Keep the swap check as a separate failure.

### Finding 6, significant: the runtime seam oracle misses illegal side topics
**The plan says** the link layer and metrics collector sit outside the swarm seam, and no `SwarmPacket` may use a topic other than the per-node tx/rx pair.
**The problem** `seam_graph.check_outside` rejects only an outside process publishing or subscribing to the exact tx/rx topics. It allows any other topic. I took the clean three-vehicle fixture, added a `/swarm/sidechannel` publisher of `uavx_msgs/msg/SwarmPacket` to `/metrics_collector`, and the graph check returned 0. The same illegal side-channel subscriber on `/link_layer` also returned 0. The existing 33 fixtures do not cover either case, so the suite stays green on a broken seam.
**Fix** give each outside process a complete topic and type allowlist, reject all unlisted publishers and subscribers, and reject `SwarmPacket` on every non-tx/rx topic. Add both negative fixtures and run them through the same snapshot path used by W3 and W4.

### Finding 7, significant: section 1b still leaves incompatible implementations
**The plan says** the five messages and two script interfaces are frozen by `architecture.md` section 1b.
**The problem** `Hello` and `LinkState` omit types for most fields, `RoleAssignment` names enum labels but gives no numeric constants, and `SwarmPacket.kind` is a bare `uint8` with the same missing mapping. `RunMetrics` is defined as one field per schema top-level key even though that schema contains nested objects and arbitrary shapes, so it is not a usable ROS message contract. The runner section gives flags but no YAML schema, JSONL line shape or error codes. The schema stores `handback.prepared_path` as an array while the gate compares it with the string `uav_4>uav_2>uav_1>gcs`, and no grammar defines `--require` comparisons. Two agents can both follow section 1b and produce messages, records and gate parsing that disagree.
**Fix** add exact `.msg` definitions with field types and enum constants, decide whether `RunMetrics` is a typed message or a serialized record, and publish example YAML and JSONL. Specify runner exit codes and the `--require` grammar, then use one representation for `prepared_path` in the schema, evaluator and gate. Add contract fixtures that parse the examples.

### Finding 8, significant: queue-drain evidence is not tied to the 45 second outage
**The plan says** `queue_drain` holds the route down from 60 to 105 seconds, generates 450 observations and drains the backlog within 2.25 seconds.
**The problem** the record carries ID strings and aggregate counts but no generation timestamps or blackout-window counters. A writer can generate or preload 450 IDs outside the outage and still satisfy `generated>=450`, set equality and the peak-depth range. `backlog_drain_s` is described only as "route restored to backlog empty"; it is not clear whether the timer ends when a local queue empties or when the GCS receives the last observation. If it is end to end, the documented 2.25 seconds omits link and hop latency; if it is local, it does not prove delivery.
**Fix** record the fault start and end plus per-observation generation time, or emit trusted counters for IDs created during the blackout and delivered after restoration. Define the drain start and end events and include forwarding latency in the bound, or name the metric explicitly as local queue service. Add a fixture with 450 preloaded IDs and one with a delayed final hop; both should fail the intended claim.

### Finding 9, significant: the capacity arithmetic does not match the four-week plan
**The plan says** it runs from 30 August to 26 September at roughly 30 hours per week. Decisions D3 still budgets 32 days at 5 hours, about 160 hours, and gives W4 roles, two fault modes, two safety runs, the integrated run and packaging.
**The problem** the active dates are four weeks, 28 calendar days, or about 120 planned hours. W4 has five build days and two send days for eight chunks, several full simulations and the freeze/install/check sequence. The 160-hour figure and the old W5 references hide at least 40 hours of capacity that no longer exists. W4 is the first likely overrun, when there is no later week to recover it.
**Fix** replace D3's arithmetic with the four-week budget and put hours beside every W4 chunk, including failed reruns and packaging. Reserve the two send days first. Move a chunk into W2 or W3, or explicitly cut a lower-weight control or the integrated demo when the budget is exceeded, and state the rubric loss.

### Finding 10, moderate: the fallback and loop documents disagree with the active plan
**The plan says** the W4 fallback is to run `link_loss` with release disabled and claim routing recovery only.
**The problem** `stage-1/decisions.md` says the W4 fallback is a deterministic priority list fixed at startup instead of a live election. Those are different systems and support different claims. `.claude/weekly-loop.md` still says W3 carries the submission and uses the old W5 framing, although the active plan puts packaging in W4. `check_docs.py` passes because it does not inspect either contradiction. An autonomous tick can choose the wrong fallback or stop one week early.
**Fix** choose one fallback and copy it verbatim into the plan, decisions and loop config. Update the W3/W4 ownership and all W5 labels, then add `check_docs.py` assertions for the selected fallback and the four active dates.

## Verdict

NOT READY. The rubric table has an artifact behind every criterion, and the geometry, documentation and existing fixture checks are useful. The build still has two submission-level blockers: W1 cannot run its own gates from a clean checkout, and W5 can certify an installation and evidence set that are not the delivered artifacts. The traffic, memory, seam and queue checks then leave measurable marks open even after those blockers are fixed. The calendar arithmetic and fallback drift make an unattended run likely to choose the wrong work when W4 is already overloaded.

## For the next round

Start with a clean-checkout W1 run that includes the harness scenario and a checker available in that week. Then exercise the archive install through the actual delivered `INSTALL.md`, with a disposable target and a package fixture that rejects external evidence paths. Re-run the negative seam, low-rate traffic, empty observation, memory-limit and queue-window fixtures. Finally, show one reconciled W4 budget and one fallback in all three active documents.

Findings: 10
