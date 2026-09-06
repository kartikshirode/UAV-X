# Plan review, round 9 (Codex)

Reviewed at HEAD `537f3dc3b630e81ac29c46a996f85eb93131f25f`. Read:
`context.md`, the plan, gate, architecture, decisions, weekly-loop config,
review status, round 8 review, W1 audit, W1 to W3 progress, all scenario files,
the submission scripts and the seven packages under `uavx_ws/src`, with the
router, queue, ledger, recovery, record and evaluator paths read in detail.

## Job 1: queue_drain

Keep both claims, but change the mechanism before another live run. First make
the scenario produce the workload it says it measures: only `uav_3` and
`uav_4` should mint the 450 queue-test observations. The current run minted 900
outage IDs because all four routers generate at 5 Hz. Then freeze those 450 IDs
as a recovery cohort when the route returns, serve that cohort at the full 200
packets per second ahead of newly created observations and measure its emptying
separately from the physical store. Send the first service allowance on the
route-return tick. At 10 Hz, 23 allowances clear 450 IDs at the 2.2 second tick,
so the 2.25 second requirement remains honest without pausing the mission or
raising the forward rate. Also trigger the promised immediate LSA when a direct
neighbour reappears. Problems 1 to 3 in `_design-options-round9.md` give the
implementation choices and the derived 2.7 second fallback if the cohort path
does not land.

## Job 2: findings

### Finding 1, critical: the queue evidence set is 900 IDs while the capacity proof is for 450
**The problem** The written proof says two cut off survey origins generate 450
observations. The implementation enables observation generation on all four
routers. The live record contains 225 outage IDs per vehicle, 900 total, while
the named custodian holds 447 IDs from `uav_3` and `uav_4`. The W4.4 gate only
asks for counts at least 450. The final checker asks the custodian set to equal
all outage IDs, so a count-only gate pass would still make the package fail.

**Evidence** `stage-1/architecture.md:300`, `stage-1/architecture.md:312`,
`uavx_ws/src/uavx_sim/uavx_sim/comms.py:309`, `scripts/gate.sh:705`,
`scripts/check_submission.py:826` and `runs/latest.jsonl`. The record reports
`generated_during_outage: 900`, `custodied: 447`, 225 `uav_3` IDs and 222
`uav_4` IDs in custody.

**Options** Problem 1 in `_design-options-round9.md`.

**Recommended** Add explicit survey observation origins to this scenario and
gate exactly 450 IDs. Roughly 4 to 6 hours plus a live rerun.

### Finding 2, significant: the current physical-empty drain has no path to 2.25 seconds
**The problem** New observations enter the FIFO while it drains. That makes the
net service rate 195 packets per second, and 450 / 195 is 2.3077 seconds before
clock quantisation. The 10 Hz service loop sends at most 20 per step, while the
run's actual peak was 472 because route restoration lagged radio restoration.
The recorded 2.30 seconds is the expected result of this mechanism, not noise.

**Evidence** `stage-1/architecture.md:375`,
`uavx_ws/src/uavx_comms/uavx_comms/router.py:963`,
`uavx_ws/src/uavx_comms/uavx_comms/router.py:995` and `runs/latest.jsonl`:
`drain_start_s: 108.8`, `drain_end_s: 111.1`, `peak_queue_depth: 472`.

**Options** Problem 2 in `_design-options-round9.md`.

**Recommended** Use a named outage cohort in a priority recovery lane and
record the later physical-empty time separately. Roughly 6 to 8 hours. If the
next live run still misses, use the derived 2.7 second physical-empty bound,
not the observed 2.30 as a picked threshold.

### Finding 3, moderate: neighbour restoration waits despite the immediate-LSA contract
**The problem** New-neighbour arrival is not treated as the topology change the
architecture promises. The neighbour table returns whether a HELLO is fresh,
but the router ignores it. Only expiry schedules an immediate LSA. That adds a
period-dependent delay and extra queue depth after the radio is already back.

**Evidence** `stage-1/architecture.md:295`,
`uavx_ws/src/uavx_comms/uavx_comms/routing.py:66`,
`uavx_ws/src/uavx_comms/uavx_comms/router.py:279` and
`uavx_ws/src/uavx_comms/uavx_comms/router.py:557`. The live record says the
radio returned at 106.2 seconds and the last counted route at 108.8.

**Options** Problem 3 in `_design-options-round9.md`.

**Recommended** Schedule an immediate LSA on a fresh direct HELLO and test the
route update, not just packet emission. Roughly 1 to 2 hours.

### Finding 4, critical: uavx_eval accepts observation summaries that contradict the ledger
**The problem** The week gate's checker does not run the strong observation,
safety and recovery semantics used by the writer and final package checker.
It trusts scalar summaries after only schema and basic time checks. This is the
project's recurring green-on-broken-system failure. The final package catches
some contradictions, but only after the chunk and week have already reported
success.

**Evidence** `uavx_ws/src/uavx_eval/uavx_eval/check.py:244` calls
`scripts/validate_record.py:198`. The latter has no custody, ledger, safety or
recovery recomputation. In a scratch in-memory mutation I set custody to 450
using arbitrary generated IDs and changed the drain to 2.2. `schema_errors`
and all 22 W4.4 requirement checks both returned empty, although the ledger
still held 900 outage IDs.

**Options** Problem 4 in `_design-options-round9.md`.

**Recommended** Extract one dependency-free cross-field verifier and call it
from both the week and package checkers. Roughly 6 to 8 hours with mutation
fixtures.

### Finding 5, moderate: relay_kill can report clean safety with no monitor samples
**The problem** The relay-kill gate reads zero contacts and zero violations but
does not require any contact-monitor samples. The schema allows zero. Since
the stricter writer semantics are not called by `uavx_eval.check`, a missing
monitor can satisfy the safety lines.

**Evidence** `scripts/gate.sh:625` to `scripts/gate.sh:638`,
`scenarios/run-record.schema.json:223` and
`uavx_ws/src/uavx_sim/uavx_sim/run_record.py:506`. The accepted live record did
have 3092 samples, but that fact is not part of the gate.

**Options** Problem 5 in `_design-options-round9.md`.

**Recommended** Reject zero samples in the shared verifier and add the explicit
relay-kill requirement. Roughly 2 hours beside finding 4.

### Finding 6, moderate: direct_only can pass when all data delivery is broken
**The problem** The control checks only that `uav_4` sent enough and delivered
nothing. A forwarding flag that accidentally disables every data path would
pass. The progress note says the anchor kept delivering, which is the positive
witness needed to isolate forwarding, but the gate never reads it.

**Evidence** `scripts/gate.sh:570` to `scripts/gate.sh:574` and
`docs/progress/week-3.md:27`. A scratch mutation set the accepted direct-only
record's `uav_1` delivered count and ratio to zero; its schema and both control
requirements still passed.

**Options** Problem 6 in `_design-options-round9.md`.

**Recommended** Gate a positive anchor delivery ratio and delivered count.
Roughly 1 to 2 hours with a rejection fixture.

### Finding 7, moderate: the no-yield control need not finish either track
**The problem** The safe encounter requires both vehicles complete and a pose
sample floor. Its negative control asks only for one violating frame and one
monitor sample. A partial course can therefore pass without exercising the
same 240 metre crossing that makes the comparison causal.

**Evidence** `scripts/gate.sh:734` to `scripts/gate.sh:754` and
`scenarios/encounter_noyield.yaml`. The two completion requirements are present
only in `w4_encounter`.

**Options** Problem 7 in `_design-options-round9.md`.

**Recommended** Mirror `vehicles_completed==2` and the pose sample floor into
the control. Roughly 1 hour.

### Finding 8, significant: deferred packets hide a real queue overflow
**The problem** The week 4 split is useful for loss accounting, but it changes
what zero eviction means. A locally retained packet removed at capacity is
counted as deferred, not evicted. No W4 gate requires deferred to be zero. The
accepted link-loss record reports 703 deferred packets, zero evictions and set
equality. End-to-end retention worked, but the 512-entry queue did not absorb
the workload without overflow.

**Evidence** `uavx_ws/src/uavx_comms/uavx_comms/routing.py:182` to
`uavx_ws/src/uavx_comms/uavx_comms/routing.py:246`, `scripts/gate.sh:657` and
`runs/link_loss_20260905T201617Z.jsonl`: `deferred: 703`, `evicted: 0`,
`observations_set_equal: true`.

**Options** Problem 8 in `_design-options-round9.md`.

**Recommended** Require zero deferred in scenarios used to claim queue
capacity. Roughly 1 to 2 hours. If integrated traffic needs origin outbox
recovery, rename and bound that mechanism instead of calling it queue headroom.

### Finding 9, significant: accepted records cannot be final evidence for the current source
**The problem** The accepted W2, W3 and early W4 records remain honest history,
but every one names an older source-tree hash. The final checker requires all
nine records to match the frozen source. Both W3 rehearsal receipts are stale
for the same reason. All five existing evidence scenarios therefore need fresh
runs after the implementation settles, in addition to the four records not yet
made.

**Evidence** Current source hash `010d781c...`; survey record `c3d20d21...`;
relay-required and direct-only `aaf710d5...`; relay-kill and link-loss
`1125f944...`. `scripts/check_submission.py:897` enforces equality.
`python scripts/check_dryruns.py` exited 1 and named both rehearsal hashes as
stale.

**Options** Problem 9 in `_design-options-round9.md`.

**Recommended** Reserve one to two days to rerun all nine scenarios and both
rehearsals against the final frozen source, before 4.8 package validation.

### Finding 10, significant: 4.8 is validation work but the remaining tail includes authorship
**The problem** The plan says the package tail is automated. Its script only
freezes source, runs a clean install and checks files that must already exist.
There is no production path that writes the proposal PDF, final video,
evidence manifest or attachment manifest. With two owed proposal sections, a
W4 section, a final video cut and the reruns in finding 9, leaving authorship to
the two send days does not fit the stated tail.

**Evidence** `stage-1/plan.md:183` and `stage-1/plan.md:197` to
`stage-1/plan.md:205` assign the outputs. `scripts/gate.sh:810` to
`scripts/gate.sh:838` only freezes, installs and checks. `docs/proposal/` has
one section and `submission/` has only the rehearsal video.

**Options** Problem 10 in `_design-options-round9.md`.

**Recommended** Use the present calendar slack to write W1 and W2 now, draft
W4 as its runs land, and cut the demo during 4.7. Roughly 10 to 14 authoring
hours before 4.8.

### Finding 11, minor: the mutable status sources disagree
**The problem** Review status still says W1 is unaccepted and describes the
preflight as missing. The W1 progress file begins with the same old statement,
then ends by saying the gate passed on 4 September. Context and the W1 marker
say W1 through W3 are accepted. An autonomous supervisor can repeat or stop on
the wrong state.

**Evidence** `.claude/review-status.json:46`,
`docs/progress/week-1.md:6`, `docs/progress/week-1.md:271` and the current
context file.

**Options** Problem 11 in `_design-options-round9.md`.

**Recommended** Correct the current status, remove the stale W1 opening and
make the supervisor cross-check the marker and gate result before dispatch.
Roughly 2 to 4 hours.

## What still holds

The round 8 seam, provenance, scenario, install and package hardening still
has value. Static and captured-graph seam checks remain tied to source hashes,
run records carry exact scenario and source provenance, and final delivery
binds validated evidence to delivered bytes. The final package checker is
strict enough to catch the queue custody mismatch that the week checker misses.

The accepted W2 and W3 claims are true of their named records. The survey
record reports 1.0 coverage from pose samples. `relay_required` reports 0.9968
delivery for `uav_4` over two forwarders. `direct_only` reports 0 for `uav_4`
while `uav_1` reports 0.9992. Week 4's accepted fault records also show the
core role work: relay-kill reconnects in 20.8 seconds with `uav_3` taking the
role, and link-loss reconnects in 24.9 seconds, completes handback and restores
the mover. These are historical measurements until finding 9's reruns.

Excluding the fault target from `time_to_reconnect_s` is defensible and does
not need reversal. That number measures what the surviving swarm did; a gated
target returns because the scenario timer releases it. The record still names
the target and `route_restored_after_blackout` checks eventual restoration, so
the omission is visible. The proposal should call the metric survivor
reconnection time rather than full-fleet recovery time.

The email budget is not the blocker. The latest selected five run records total
about 1.9 MB. Even the conservative case of nine 760 KB records is about 6.8
MB, and the uncompressed source paths are about 5.3 MB before archive
compression. The recorded delivery route can link the source and video if the
video pushes the attachments over 25 MB. Every evidence record still has to be
listed as an attachment or a tested link because the source archive excludes
`runs/`.

## Verification

- `python scripts/check_geometry.py`, exit 0.
- `python scripts/check_docs.py`, exit 0.
- `python scripts/check_submission.py`, exit 1 with the eight known missing
  package items plus one network failure reaching the live competition record.
- `python scripts/test_seam_fixtures.py`, exit 0, 51 cases.
- `python scripts/test_submission_fixtures.py`, exit 1 because `ffmpeg` is not
  available on this Windows host. The suite correctly refused to skip its
  oracle.
- `python scripts/test_gate_preflight.py`, exit 0.
- `python scripts/test_require_grammar.py`, exit 0, all 113 expressions parsed.
- `python scripts/test_record_contract.py`, exit 0.
- Scenario, install-guide and message-contract fixture scripts exited 0.
- Direct package runs passed 68 `uavx_eval`, 101 `uavx_mission`, 183
  `uavx_comms`, 67 `uavx_gcs` and 67 `uavx_roles` tests, with one skip in
  mission and one in comms. `uavx_msgs` needs the generated ROS overlay.
  `uavx_sim` reached 530 passes and one skip; its one failure came from placing
  pytest's temporary base inside the repository, so a test expecting a path
  outside git correctly saw the parent repository instead.
- `python scripts/check_dryruns.py`, exit 1, correctly rejecting both W3
  rehearsal receipts against the current source hash.
- A scratch in-memory queue record with a false custody set and typed drain
  passed `uavx_eval.check` schema semantics and all 22 W4.4 requirements. This
  is finding 4. A second scratch mutation zeroed anchor delivery and still
  passed both direct-only requirements. This is finding 6. No repository file
  was changed for either probe.
- The Windows `python3` app alias and WSL `bash` launcher were unavailable in
  this session, so `check_shell.sh` and `gate.sh chunks` could not be rerun.
  The Python gate-preflight suite still checked selector dispatch and all 113
  requirements were parsed.

## Verdict

ON TRACK WITH THE FIXES ABOVE. The core survey, mesh and role recovery exist,
and the live fault records support those claims. The present 4.4 contract and
the week-level evidence checker can still report the wrong thing, so findings
1 through 4 need to land before more evidence is accepted. Twenty days is
enough for that work and the remaining scenarios, but only if proposal writing
and the complete rerun set move out of the final two-day tail now.

## For the author, in order

1. Fix the queue producer scope, outage cohort and priority drain together.
2. Put the cross-field record semantics in the week checker before accepting
   the next W4 record.
3. Trigger the immediate neighbour-add LSA and rerun 4.4 once.
4. Close the direct-only, relay-kill and no-yield gate holes before final reruns.
5. Decide whether queue capacity means zero deferred. The current link-loss
   record says it overflowed 703 times.
6. Draft the owed proposal sections now and protect 4.8 as a validation tail.
7. After source settles, rerun all nine evidence scenarios and both rehearsals.
8. Correct the mutable W1 status and add the supervisor drift check.

Findings: 11
