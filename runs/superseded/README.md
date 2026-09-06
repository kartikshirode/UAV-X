# Records kept as history, not offered as evidence

Runs that happened and are honest about what they measured, but that the
current checkers cannot verify from the file's own contents. They stay in the
repository because deleting a measurement because a later checker got stricter
is how a project loses the ability to say what it used to believe.

Nothing here is cited by the proposal or listed in the evidence manifest.
`scripts/check_submission.py` reads `runs/` and not this directory, and so does
the provenance suite that checks every committed record still holds.

## link_loss_20260905T201617Z.jsonl

Chunk 4.3, flown 5 September, and the run the handback claim was accepted on.
Reconnected in 24.9 s, drained in 1.30 s, handback owned by uav_4 over
`uav_4,uav_2,uav_1,gcs`, 7200 observations delivered once.

Superseded on 6 September for one reason. It reports 440 observations
generated during the outage and its own ledger rows place 436 of them inside
the window it names. Both numbers are right. The window opens at
121.10000000000001 and four observations were created at 121.1000000000001,
which the count read at full precision and the rows store rounded to 121.1,
below the start. Round 9 finding 4 added a checker that recomputes every scalar
in the observations block from the rows it was counted from, and this is the
first thing it found: a record has to be checkable from its own contents and
this one is not.

`uavx_gcs/ledger.py` now puts the window ends and the row timestamps on one
grid, so a fresh run of the same scenario carries a block that verifies. Chunk
4.3 has to be re-flown before the package is built, which round 9 finding 9
requires of every record anyway: all five accepted runs name a source tree hash
older than the current one, and `check_submission.py` requires the nine it
packages to match the frozen source.

## queue_drain_20260906T123639Z.jsonl

Chunk 4.4, the first run flown after the radio learned to gate itself on the
clock. It is here for one measurement and one fault.

The measurement: the radio gated at 60.0 and lifted at 105.0, both exact, where
every earlier run gated 1.8 s after the moment the scenario asked for. The
ledgers under `queue_drain_20260906T123639Z-ledgers/` are what that looks like.

The fault: it reports a 45 second outage as three seconds. `uav_3.json` and
`uav_4.json` each carry an episode at absolute 178.4 that was withdrawn at
178.5, which is scenario 63.0 to 63.1. uav_2's beacons timed out on one of them
before the other, so for one hop time a path existed through a neighbour that
was still advertising a vehicle the first had already given up on.
`recovery.lost_route` took that as the swarm reconnecting and closed the window
on it, so the block says 30 observations were generated inside the outage and
30 delivered after, while uav_3's own queue reached 468. The rule that skips a
route withdrawn before it was ever confirmed is in `recovery._held` now, with
those times in its tests.

## queue_drain_20260906T125232Z.jsonl

The same scenario with that rule in place, and the run the drain arithmetic is
quoted from. Twenty two of the twenty three requirements pass: the radio gates
at 60.0 and lifts at 105.0, 450 observations are generated inside the window,
uav_3 holds all 450 as custodian, all 450 reach the ground station after the
route returns, nothing is deferred, evicted or expired, the queue peaks at 465
inside its 512, and control never waits.

The one that fails is `backlog_drain_s <= 2.25`, which reads 2.30, and this
record is kept because that number cannot be improved. `/clock` runs at 10 Hz
and the queue is served at 200 packets a second, so 20 packets move per clock
step and there is no half step. The 450 the design requires is 22.5 steps,
which is 23, which is 2.3 s. `peak_queue_depth >= 450` and
`backlog_drain_s <= 2.25` cannot both hold on this stack. The 2.25 in
architecture.md is 450 over 200 in continuous time and the derivation left the
clock out.

Not offered as evidence, because a run that misses a gate requirement is not
evidence for the claim that requirement guards. It stays because deleting the
measurement that shows a bound is unreachable would leave the argument for
changing that bound resting on nothing.
