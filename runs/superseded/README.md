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
