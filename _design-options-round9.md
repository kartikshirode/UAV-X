# Design options, round 9

## Problem 1: queue_drain counts four producers but sizes custody for two
**Where it bites** W4.4, the queue capacity claim and the communication row
**Found by** job 1

The scenario and architecture say that `uav_3` and `uav_4` are the two cut off
origins, so 45 seconds at 5 Hz means 450 IDs. `router_command()` passes
`observations:=true` to every router, though. The live record has 225 outage
IDs from each of `uav_1`, `uav_2`, `uav_3` and `uav_4`, 900 in all. Its custodian
has 225 `uav_3` IDs and 222 `uav_4` IDs, or 447. The final package checker later
compares `custodied_ids` with the complete outage set, so a record that merely
gets the gate count above 450 would still be rejected.

### Option A: make the queue scenario survey-origin only

Add an explicit `observation_origins` list to the scenario contract and pass
`observations:=true` only to those routers for `queue_drain`. Set it to
`uav_3,uav_4`, assert exactly 450 IDs in the gate and retain the current
single-custodian rule. Update the architecture wording from "per node" to
"per surveying origin" where this capacity calculation is used. Cost: 4 to 6
hours for the scenario field, launcher argument, schema, tests and a live
rerun. It gives up testing anchor and relay observations during this one queue
case, but does not weaken the relay, direct-only or integrated traffic claims.
It fails if another scenario silently gets the same producer filtering without
declaring it.

### Option B: keep all producers and name the outage cohort

Keep the 5 Hz router timer on all four vehicles. Add an `outage_ids` set and a
`disconnected_origins` field derived from route episodes, then define custody
and drain over the IDs from the disconnected survey origins only. Keep the
full 900-row ledger for delivery equality. The final checker must compare
`custodied_ids` to `outage_ids`, not to every row whose timestamp lies in the
commanded window. Cost: 6 to 8 hours across `uavx_gcs`, the record schema,
`uavx_eval`, submission checks and fixtures. It keeps the all-node traffic
model, but gives up the sentence that every observation minted during a
blackout was in the custodian. It fails when route episodes disagree on which
origin was actually cut off, so the record needs a rejection for an ambiguous
cohort.

### Option C: distribute the 900-ID backlog

Treat the relay and both surveyors as three origins, elect two custodians, and
prove the union of their custody sets. Derive a 4.5 second service bound for
900 IDs, or increase the queue capacity and the gate to fit that workload.
Cost: 10 to 14 hours and a larger evaluator. It gives up the one-custodian
argument and the 2.25 second communication headline. It fails the frozen 512
entry and 2.25 second requirements unless those values are changed, which
standing rule 2 forbids in this round.

### Option D: pause non-survey observation timers during the blackout

Have the fault handler stop minting on the anchor and relay while the
blackout is active, then resume them after restoration. The record would show
450 generated outage IDs without adding a new scenario field. Cost: 3 to 4
hours. It gives up continuous observation from those vehicles and can hide a
real loss in the pause path, weakening mission completion and innovation
claims. It fails if a delayed fault observation lets one of the paused routers
mint before the gate is applied.

### Recommended: A

This is the smallest change that makes the written 450-packet argument true in
the running scenario. The producer scope becomes explicit instead of being a
role-name convention buried in the launcher. It costs about half a day and a
fresh 4.4 run. If the author needs all-node traffic for a separate study, keep
that as a non-gated scenario rather than changing this proof.
**Fallback if it does not land:** B, if the author wants to preserve the
per-node observation timer and can finish the cohort evaluator before the next
live run.

## Problem 2: the physical queue drain cannot reach 2.25 seconds as written
**Where it bites** W4.4 and the 25 percent communication row
**Found by** job 1

The current service loop drains the same store that keeps receiving the
custodian's own observations. Its effective rate is 200 minus 5, or 195
packets per second. The 450-packet ideal is already 2.3077 seconds. With the
10 Hz clock the loop sends at most 20 per tick, so even a fixed 450 packet
backlog needs 23 service steps. The run recorded 472 at the peak and 2.30
seconds from route return to the whole store becoming empty. New observations
also share the FIFO with the backlog.

### Option A: snapshot the outage cohort and give it a priority lane

At route restoration, freeze the named outage IDs in a recovery lane. Serve
that lane before new observations at the existing 200 packets per second, keep
new observations in a live lane and define `backlog_drain_s` as the time until
the recovery lane is empty. Run the first service allowance in the same
callback that installs the route. With 450 IDs, 23 ten-Hz allowances finish at
the 2.2 second tick when the first batch is sent at the start. Record both
cohort completion and physical queue empty time so the distinction is visible.
Cost: 6 to 8 hours for queue state, record fields and fixtures. It keeps the
frozen rate, capacity and 2.25 bound. It gives up the claim that the entire
physical store is empty in that time. It fails if the cohort is larger than
450 or if route restoration is observed only after the first service pass.

### Option B: stop minting until the store is empty

Hold the observation timer on the custodian and its senders from route return
until the existing store is empty. The current FIFO and physical-empty
measurement then get the full 200 packets per second. Cost: 2 to 3 hours. It
gives up continuous mission sampling during recovery and changes the
integrated mission's traffic pattern. It fails when an executor ignores the
pause or when another origin keeps filling the store.

### Option C: allow a recovery burst above 200 packets per second

Keep one store and the current measurement, but grant a temporary service
credit large enough to clear 472 packets in 2.25 seconds. Cost: 3 to 5 hours.
It gives up the frozen per-node forward-rate claim and makes radio capacity
look better than the design says. It fails on a real link or a judge that
checks the advertised rate against the trace.

### Option D: retain physical-empty semantics and derive a reachable bound

Keep the implementation and replace the unreachable headline with a bound
derived from the frozen capacity and the custodian's own 5 Hz traffic:
512 / (200 - 5) = 2.6256 seconds, rounded up to the 10 Hz clock as 2.7
seconds. Cost: 1 to 2 hours for the gate, architecture and proposal. It gives
up the sharper 2.25 second result and the current plan text. It fails if more
than one live origin continues to feed the custodian after restoration, so the
bound must name its traffic assumptions.

### Recommended: A

The queue proof is about the outage backlog, not unrelated observations that
arrive after the route is back. A named recovery lane preserves that meaning
without pausing the mission or inventing a faster radio. It costs most of a
working day and needs one deterministic fixture for the same-tick first send.
**Fallback if it does not land:** D after one failed live rerun, with the
proposal saying that the bound covers physical emptying under the derived
traffic load.

## Problem 3: a new neighbour does not trigger the immediate LSA promised by the design
**Where it bites** route restoration in W4.2 to W4.4 and the reconnect budget
**Found by** job 1

`NeighbourTable.hello()` returns `fresh=True` for an unseen direct neighbour,
but `Router._on_hello()` ignores that return value. `Router.tick()` schedules
an immediate LSA only for expired neighbours. `architecture.md` says the LSA
is immediate on any neighbour change. The 5 September run restored the radio
at 106.2 seconds and did not return the survey routes until about 108.8, which
adds queue depth and recovery uncertainty.

### Option A: schedule an LSA when a direct HELLO is fresh

Capture the boolean from `neighbours.hello()` and set `_next_lsa_at = now` on a
new neighbour, mirroring the expiry path. Add a unit test that a fresh HELLO
queues an LSA on the next tick, while a renewal does not. Cost: 1 to 2 hours.
It keeps all frozen periods and the radio model. It fails if the route
computation timer still waits two seconds after the LSA, so the test must also
check the route update path rather than only the outgoing packet.

### Option B: make topology changes request an immediate route computation

Have the link-state database expose a change notification and let the router
run Dijkstra immediately when a new LSA changes its graph. Keep the LSA
periodic and use the existing hysteresis for installation. Cost: 3 to 5 hours.
It gives up the simple one-computation-per-period timing assumption in tests
and increases CPU work during floods. It fails if a burst of LSAs causes
re-entrant computations or bypasses the two-win route hysteresis.

### Option C: add an explicit radio-restore notification

The link layer would publish a restore event to each router, and the router
would send HELLO and LSA immediately instead of waiting for discovery. Cost:
4 to 6 hours plus a new control endpoint and seam allowlist entry. It gives up
the claim that the swarm notices restoration only from radio traffic. It fails
if the notification crosses the same broken route it is meant to repair, or
if the link layer becomes an unmeasured recovery oracle.

### Recommended: A

The direct neighbour table already knows the exact change. Using its return
value fixes the documented behaviour with almost no new state and leaves the
route hysteresis intact. It costs a short implementation and test pass. This
does not by itself solve the 450 versus 900 scope or the drain math, so it is a
supporting fix for those queue changes.
**Fallback if it does not land:** B if a live trace shows that the remaining
delay is route computation rather than LSA propagation.

## Problem 4: the week gates accept summaries that contradict their ledgers
**Where it bites** W4.2 to W4.7 and the standing failure mode of a green check
**Found by** this round

`uavx_eval.check.schema_errors()` calls `scripts/validate_record.py`, whose
semantic checks cover duration, wall time, duplicate vehicle IDs and event
times. It does not call the stronger observation, safety or recovery checks in
`uavx_sim.run_record`. A scratch in-memory mutation of the live queue record
set `custodied` to 450, replaced its 447 IDs with arbitrary generated IDs and
set the drain clock to 2.2. The schema errors and all 22 queue gate
requirements were empty. `check_submission.py` would reject the same bytes,
but that is the tail, after the week gate has already reported success.

### Option A: share one pure record verifier

Move the cross-field checks for ledgers, outage cohorts, safety samples and
recovery blocks into a dependency-free module under `uavx_eval` or `scripts`,
then make both `uavx_eval.check` and the final package checker call it. Keep
ROS and simulator imports out of it so W1 remains runnable. Cost: 6 to 8 hours
plus mutation fixtures. It gives up the current duplicate implementations and
requires a careful compatibility pass over old W1 records. It fails when a new
field is added to only one caller's required list.

### Option B: put every cross-field assertion in gate expressions

Add requirements for custody set size, ledger count and each safety sample
floor, and teach the requirement grammar a set comparison. Cost: 4 to 6 hours.
It gives up reusable semantic checks and makes the shell gate carry long,
scenario-specific expressions. It fails when a malformed ledger still has
counts that satisfy the new scalar expressions.

### Option C: have each week gate invoke the final package checker on its run

Expose a single-record mode in `check_submission.py` and call it from
`check_run` after the normal provenance check. Cost: 5 to 7 hours. It keeps one
strict oracle, but couples W4 to submission paths, media tools and package
state. It fails when an otherwise valid chunk cannot run because the final
package has not been assembled yet.

### Option D: sign the writer output and trust only signed records

Have the runner sign each record and verify the signature before reading a
metric. Cost: 12 to 18 hours for key handling and clean-target distribution.
It gives up inspectable unsigned fixtures and still does not prove that the
signed writer calculated custody correctly. It fails if the key is unavailable
on the clean install or if the writer itself has the bug.

### Recommended: A

This is the direct repair for the exact failure pattern the project has been
finding for eight rounds. One JSON-only verifier gives the week gate and the
submission gate the same meaning and keeps the checks testable without Gazebo.
It costs one working day, mostly in moving code and rebuilding tamper fixtures.
**Fallback if it does not land:** C for the final week gate, with the shared
verifier still scheduled before the send.

## Problem 5: relay_kill can claim clean safety with no contact samples
**Where it bites** W4.2 and the safety row
**Found by** this round

The schema permits `contact_monitor_samples: 0`. `w4_relay_kill` requires zero
contacts, zero separation violations and a minimum separation, but it never
requires `contact_monitor_samples>0`. The stronger safety semantic check lives
in `uavx_sim.run_record` and is not called by `uavx_eval.check`. A record with
no monitor can therefore satisfy the relay-kill gate's clean-safety fields.

### Option A: add a sample floor to the relay-kill gate

Add `contact_monitor_samples>0` beside the existing safety requirements and
add a fixture with zero samples. Cost: under 1 hour. It gives up no rubric
claim and fails loudly if the collector is absent. It fails if another
safety-bearing gate repeats the same omission.

### Option B: enforce the sample floor in the shared semantic verifier

Make every record carrying safety fields reject zero samples before any gate
requirement is read. Add the explicit requirement to relay-kill as a second
line of defence. Cost: 2 to 3 hours once Problem 4's verifier exists. It gives
up accepting historical synthetic records with empty safety blocks. It fails
only if a caller bypasses the verifier entirely.

### Option C: remove safety assertions from relay_kill

Leave safety to `encounter` and `mission_integrated`, and make relay-kill a
pure fault-recovery test. Cost: 1 to 2 hours. It gives up a safety cross-check
on the fault scenario and makes the 10 percent row rest on fewer runs. It fails
if either remaining safety run is not completed before the package freeze.

### Recommended: B

The shared check prevents the same zero-monitor shape from returning in queue
or integrated records. The direct gate expression is cheap insurance. This is
about two hours when done beside Problem 4.
**Fallback if it does not land:** A immediately, before running relay-kill
again.

## Problem 6: the direct_only control has no positive link-layer sanity check
**Where it bites** W3.5 and the communication resilience row
**Found by** this round

`w3_control` requires only `uav_4` delivery ratio equal to zero and at least
1080 packets sent. Mutating an accepted direct-only record in memory to set the
anchor's delivery count and ratio to zero still leaves both gate requirements
passing. A forwarding implementation that accidentally disables all data
transmission when the flag is false would pass this control, even though it
does not isolate the forwarding feature. The progress note says the anchor
kept delivering, but the gate does not enforce it.

### Option A: require anchor traffic to arrive

Add an anchor delivery ratio and delivered-count floor, plus a positive
link-layer delivery count if the record exposes one. Cost: 1 to 2 hours for
gate expressions and a mutation fixture. It keeps the control's purpose and
does not weaken the communication row. It fails if the anchor's own route is
legitimately degraded in a future control topology, so the scenario geometry
must stay frozen.

### Option B: validate the relay/control pair together

Add a pair checker that loads both named records, proves equal seed, duration,
stations and roles, confirms forwarding is the only changed flag, and requires
the anchor to remain positive in both. Cost: 4 to 6 hours. It gives up running
the control as a standalone gate and adds an ordering dependency on the
measurement record. It fails when one record is rerun after a source change
and the other is not, which is at least an honest failure.

### Option C: use a radio-disabled control instead

Replace direct_only with a run that disables the link layer and assert that no
vehicle delivers, then compare it with relay_required. Cost: 3 to 5 hours. It
gives up the specific claim that forwarding is the switch that enables the
second hop and weakens the communication row. It fails to distinguish a bad
radio from a bad router, which is exactly the ambiguity the current pair was
meant to remove.

### Recommended: A

The anchor floor is the missing one-line witness. It costs little and keeps the
pair independently runnable while making an all-radio failure visible.
**Fallback if it does not land:** B if the author wants pair-level provenance
before final evidence is frozen.

## Problem 7: encounter_noyield can pass without completing the two tracks
**Where it bites** W4.6 and the safety control
**Found by** this round

The positive encounter gate requires two completed vehicles and a pose sample
floor. The no-yield control requires only one separation violation and a
positive contact-monitor sample count. Provenance requires a complete timed
record, not that either track reached its endpoint. A partial flight can
therefore produce one bad frame and pass the control while failing to exercise
the same converging course.

### Option A: mirror the completion floors

Require `vehicles_completed==2`, `pose_sample_count>=1000` and the existing
violation and monitor requirements in `w4_encounter_control`. Cost: about 1
hour. It keeps the control honest and costs no rubric credit. It fails when a
vehicle genuinely completes late, which should be a failed control rather
than a weaker result.

### Option B: add a pair-level trajectory comparison

Have a checker compare the two encounter records' track metadata, sample span,
crossing time and completion flags, then require the control's violation. Cost:
4 to 6 hours. It gives up standalone control execution and adds a second
cross-record tool. It fails when the source hashes differ, forcing a rerun that
the current scalar gate can overlook.

### Option C: remove the no-yield run and claim monitor coverage only

Use the positive encounter and integrated run for the safety row, with no
negative control. Cost: 1 hour. It gives up the causal evidence that the yield
rule caused the safe result and weakens the 10 percent safety argument. It
fails in review if both vehicles simply happened to miss each other.

### Recommended: A

The control should meet the same basic flight contract as the measurement.
This is a small gate and fixture change, not a new subsystem.
**Fallback if it does not land:** B if the author is already building a pair
checker for direct-only.

## Problem 8: `evicted==0` no longer proves the queue never overflowed
**Where it bites** W4.2 to W4.7 capacity and resilience claims
**Found by** this round

`PacketQueue.push()` increments `deferred` when it removes an item that the
origin still retains, and increments `evicted` only when the removed item was
not locally retained. The gates assert zero `evicted` but never assert zero
`deferred`. A queue can reach capacity, push out many packets and later recover
them from origin storage while still advertising a clean zero-eviction result.
That may be a sound end-to-end retention design, but it is not the same as a
512-entry queue holding the outage backlog without overflow.

### Option A: require zero deferred where capacity is part of the claim

Add `observations.deferred==0` to queue-drain, link-loss and integrated gates,
and print it beside peak depth. Cost: 1 to 2 hours. It keeps the simple
capacity story and catches a full queue even when the origin can repair it. It
fails on a legitimate high-rate recovery that relies on origin retention, so
the run would need the explicit fallback rather than silently passing.

### Option B: make deferred the stated retention mechanism

Keep the split and change the architecture claim to a bounded forwarding queue
backed by an origin outbox. Gate `deferred` against a measured outbox bound and
prove that every deferred ID is later resent. Cost: 4 to 6 hours. It gives up
the claim that 512 entries absorb the whole outage at every node. It fails if
the outbox is not bounded or its retry ledger is incomplete.

### Option C: use separate capacity fields

Record `queue_peak`, `origin_outbox_peak` and `forwarding_evictions`, then make
the proposal say which one the 512 entries describe. Cost: 5 to 7 hours. It
keeps both mechanisms visible but adds schema and evidence burden. It fails
when a judge reads the old `peak_queue_depth` field without the new scope.

### Option D: fold deferred back into evicted

Treat every pop at capacity as an eviction and let the origin retry as a
separate event. Cost: 2 to 3 hours. It gives up the useful distinction between
recoverable origin retention and lost foreign data, which weakens the fault
recovery explanation. It fails by making a recoverable queue look lossy.

### Recommended: A

The current architecture sells a queue sized for the outage, so a full queue
should fail that scenario even if another store saves the packet. The gate
change is cheap and leaves the deferred metric available for diagnosis.
**Fallback if it does not land:** B, if a live run shows deferred packets are
needed for the integrated mission and the proposal is rewritten before freeze.

## Problem 9: accepted evidence is historical but cannot be used at the final freeze
**Where it bites** W3 evidence, W4.8 and every quoted communication result
**Found by** job 2

The current source-tree hash is `010d781c...`. The accepted survey record has
`c3d20d21...`, both relay-required and direct-only have `aaf710d5...`, and the
accepted W4 relay records have `1125f944...`. `check_submission.py` requires
every named run's hash to equal the frozen source hash. `check_dryruns.py` also
currently rejects both W3 rehearsal receipts because they were made against
`aaf710d5...`. The W2 and W3 claims remain true of their named historical
records, but those bytes cannot be the final evidence after the W4 source is
frozen.

### Option A: rerun every named scenario after the final source freeze

Finish implementation, freeze the source, then rerun all nine required
scenarios and both rehearsals against that exact tree. Copy stable record and
graph names into the evidence manifest and run the final checker. Cost: about
one to two days of simulator time and operator attention, plus the package
checks. It preserves independent W2 and W3 evidence and catches semantic
changes from the new queue and role code. It fails if a late source edit or
scenario edit forces another complete cycle.

### Option B: package the old source snapshot with each old record

Keep the accepted records and carry their source trees as separate evidence
archives. The proposal would cite which snapshot produced each number. Cost:
8 to 12 hours for multiple archives and a more complex delivery manifest. It
gives up one coherent source package and may confuse a judge about which code
is the submission. It fails if the old snapshot cannot be rebuilt on the clean
target.

### Option C: narrow the source hash to exclude W4 changes

Change the hash policy so W4 role, routing and record changes do not invalidate
W3 records. Cost: 2 to 4 hours. It gives up the provenance guarantee exactly
where later work can change the measured result. It fails a review of the
source manifest because the submitted code is wider than the evidence hash.

### Option D: cite only newly produced integrated evidence

Drop separate W2 and W3 runs from the final evidence and let
`mission_integrated` carry the proposal numbers. Cost: 3 to 5 hours in the
proposal and manifest. It gives up the clean relay versus direct-only causal
pair and the standalone survey result, weakening mission and communication
rows. It fails if the integrated run does not meet every independent threshold.

### Recommended: A

The checker is right to bind bytes to the code being sent. The reruns are the
price of changing the implementation, not a reason to weaken provenance. Book
them before 4.8 and keep the old records as development history only.
**Fallback if it does not land:** D, but only if a human accepts the rubric
loss before the final proposal is written.

## Problem 10: the submission tail has no scheduled authoring time
**Where it bites** W4.5 to W4.8 and the 45 percent of the rubric outside comms
**Found by** job 2

The plan gives W4 five build days and two send days. Its 4.8 script freezes the
source, installs it and checks a package, but it does not write a proposal,
render a PDF, cut a final video or create the evidence and attachment
manifests. The plan says those outputs are automated even though only the
source, install and checker scripts exist. Week 3 has one drafted section;
weeks 1 and 2 are still owed, and the only video is a rehearsal clip.

### Option A: author while each final artifact is fresh

Use the current calendar slack to write the W1 and W2 sections now. Draft the
W4 fault and safety section as 4.2 to 4.6 land, and cut the demo from
`mission_integrated` during 4.7. Leave 4.8 as freeze, clean install,
manifest, final read and send preparation. Cost: 10 to 14 hours spread across
the next week. It keeps all four proposal sections and gives up no rubric row.
It fails if the author postpones the writing again and reaches 4.8 with a
blank PDF, so a page-count checkpoint belongs before 4.7.

### Option B: reserve a protected 4.8 authoring day

Move one of the two send days or one low-weight control day into a fixed PDF
and video authoring block before the freeze. Cost: 8 to 12 hours of schedule
movement. It keeps a visible deadline but compresses reruns and package checks.
It fails when any live scenario slips, because the protected day is the only
remaining writing window.

### Option C: generate a proposal draft from run records

Build a small renderer that turns the architecture and evidence manifests into
four cited sections, then have a human edit the result into six to eight pages.
Cost: 12 to 20 hours. It gives up some control over the narrative and still
needs a final read. It fails when the renderer prints stale or unexplained
numbers from a record that is later rerun.

### Option D: submit the rehearsal clip and a shortened proposal

Use the existing 60 second rehearsal and fill only the communication section,
then rely on the source archive for the rest. Cost: under 4 hours. It gives up
mission, fault and safety explanation and risks failing the proposal page and
demo expectations. It fails at review even if the package checker is made to
accept the files.

### Recommended: A

The plan's own standing rule says each week writes its section while the work
is fresh. The tail can still fit if that rule starts today, but 4.8 cannot be
treated as an authoring week. This costs roughly two afternoons and protects
the final two days for checks and the human send.
**Fallback if it does not land:** B, with the no-yield control taking the
documented sacrifice before any time is taken from the integrated run.

## Problem 11: review status disagrees with accepted progress
**Where it bites** the weekly loop supervisor and handoff decisions
**Found by** job 2

`docs/progress/week-1.md` begins by saying the week is not accepted, then says
the gate exited 0 on 4 September and carries `WEEK-1-DONE` at the end. The
context says weeks 1 through 3 are accepted. The current
`.claude/review-status.json` still says `build_state.accepted: false` because
it was written before the human preflight receipt existed. The same file also
reports the older registration count. A supervisor reading either stale line
can stop or repeat work that the final marker says is complete.

### Option A: update the JSON by hand after each accepted gate

Change the accepted flag, week, evidence and current source facts whenever the
supervisor finishes a week, and remove the stale opening from the W1 note.
Cost: under 1 hour now. It keeps the existing file shape but fails whenever a
gate is run outside the supervisor or a human edits the progress file without
the matching status update.

### Option B: make the supervisor cross-check status and markers

Before dispatch, read the JSON and the progress marker, and stop with a named
drift error when they disagree. On a successful gate, write the status from the
same result. Cost: 2 to 4 hours. It keeps the JSON as the compact source of
truth and makes stale state visible instead of silently acting on it. It fails
if a marker is copied into a progress note without a real gate, so the write
must include the gate result or run id.

### Option C: remove mutable build state from review-status

Keep only review-round history in the JSON and derive accepted weeks from gate
receipts and progress markers. Cost: 2 to 3 hours. It avoids one stale field,
but gives up a convenient handoff summary and adds filesystem scans to every
loop tick. It fails when a receipt is deleted or a progress file is moved.

### Recommended: B

The contradiction is cheap to prevent and the supervisor is the one place that
already sees both the gate exit and the marker. This costs a few hours and
stops a stale handoff from changing execution. The round 9 status file should
record the corrected W1 state now while the implementation fix is scheduled.
**Fallback if it does not land:** A for this handoff, with a checklist entry
for every future accepted week.
