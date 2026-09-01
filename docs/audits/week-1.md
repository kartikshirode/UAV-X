# Week 1 audit

1 September 2026, over `c78ee24..HEAD`, which is the whole of week 1.

Run in two passes. A fresh session read the plan, the diff, the tests and the
documents, ran the suites itself and reported 13 findings. Then I re-derived
every one of them in my own shell, because a report is a claim and this repo has
now been bitten twice by checkers that confirmed what their author already
believed. Every verdict below is something I ran, not something I was told.

Thirteen findings came from that pass and a fourteenth turned up while closing
it. Ten are fixed and committed. Four are carried into week 2, and one of those
four needs a person rather than a commit.

AUDIT-COMPLETE

## The findings

| # | What | Verdict | Where it went |
| --- | --- | --- | --- |
| 1 | `run_smoke.sh` writes no valid run record | confirmed | week 2 |
| 2 | the gate's expression list is copied and unchecked | confirmed | fixed |
| 3 | the competition id fixtures cannot see their own defect | confirmed | fixed |
| 4 | the proposal's architecture section was not written | confirmed | week 2, needs a person |
| 5 | the week's own documents were outside the document checks | confirmed | fixed |
| 6 | two counts in the week notes are wrong | confirmed | fixed |
| 7 | numbers quoted from no run | confirmed | fixed |
| 8 | the four fix runs name a commit without the fix | confirmed | closed before the audit landed |
| 9 | the tilt check is untested and fails open | confirmed | fixed |
| 10 | poses are sampled at a quarter of the frozen rate | confirmed | week 2 |
| 11 | nothing records where each vehicle stands | confirmed | week 2 |
| 12 | a scenario may narrow what the seam pass demands | confirmed | fixed |
| 13 | carriage returns, and a printf broken across a line | confirmed, one half weaker than reported | fixed |
| 14 | the week notes carried the marker that says the week passed | found while closing | fixed |

## Fixed

**2. The gate's own list was written out by hand in three places.** The ten
expressions `w1_runner` asserts sit in `scripts/test_record_contract.py` and in
`uavx_ws/src/uavx_sim/test/test_run_record.py`, each under a comment saying
"copied from gate.sh", and the memory ceiling sits in three more. Nothing
compared any copy with the original. That is round 2 finding 2 again, moved out
of documents and into tests, where the rule against restating a threshold could
not reach it. A copy that drifts here does not fail loudly. It asserts last
week's contract and passes.

`check_docs.py` now pulls the `--require` list straight out of `w1_runner` and
compares both copies against it, and it fails on any bare use of the ceiling
outside its definition and the gate. The ceiling is imported now in the two
package tests and in the submission fixtures. Watched failing both ways: change
one expression in the package copy and the drift is named; add a stray copy of
the ceiling to any script and it is named with its line.

**3. The two competition id fixtures could not detect the defect they were named
for.** The placeholder id was twelve zeroes. Lowering it changed only the `UAVX`
prefix, so the case called "transcribed in lower case" proved the prefix was
case sensitive and said nothing at all about the twelve characters after it.
Proved by mutation: relax the character class to accept lower case hex and the
old case still passed.

The id has a letter in it now, the prefix and the hex are separate cases, and a
third covers an id one character short. Under the relaxed pattern the hex case
fails and the other two do not, which is exactly the separation that was
missing. This was my own defect, introduced the same morning.

**5. Every rule in `check_docs.py` applied to eight documents, and none of them
was the one the week produced.** `docs/progress/` and `docs/audits/` were
outside its list, so the file describing the work was checked by nothing while
the files describing the plan were checked by everything. Globbed now, so week 2
does not have to remember to add itself. Two mutations confirm it: a carriage
return injected into the week file is caught by name, and a gate threshold
appended to it is caught by name.

**6. Two counts in the week notes were wrong, and the checker's own comment
carried one of them.** Defect 8 said 27 requirements untyped and 25 remaining.
It was 25 and 23. `check_docs.py` prints the live number on every run, which is
the number to trust.

**7. Some numbers came from no file in the repository.** Chunk 1.6's row quoted
a root figure against a group figure, and neither appears anywhere: not in a run
record, not in the sampler, not in its tests, which carry different constants
for a different purpose. Removed. The diagnostic figures behind defects 11 to 14
are real readings off PX4 ULOGs and gazebo model poses, and none of them can be
re-derived from a checkout, so the week notes now carry a table saying exactly
which figures trace to a run record and which do not. Standing rule 3 is about
evidence. A post mortem is not evidence, and it should not be dressed as it.

**9. The one control this week added was the one thing this week did not test.**
The launcher asks gazebo where each model came to rest and refuses above two
degrees of tilt. Nothing exercised it. Worse, it failed open: awk reads a
non-numeric field as zero, so an error line, an empty answer or a changed output
format all produced a confident 0.00 degrees and the launcher would have
reported a vehicle level that it had never measured. A guard that fails open is
worse than no guard, because it also removes the reason to look.

Both pieces of arithmetic in the launcher are now named, delimited and pulled
out by `scripts/test_launcher_geometry.py`, which runs them directly and needs
no simulator. 19 checks: the line is centred for two through six vehicles, the
default formation clears the pavement edge by 2.5 m, a wide spacing still
reaches the lip so the negative case stays reachable, an even count keeps its
half spacing, pitch counts as much as roll, and seven malformed answers are each
refused rather than read as level. Extracted rather than restated, because a
copy of the formula in the test would agree with itself forever while the
launcher moved on.

It found something on its first run. **Five vehicles at the default spacing put
two of them on the lip at exactly the pavement edge.** The count is fixed at
four by D4 and by every coordinate in the frozen geometry, so nothing today
depends on it, and the tilt check would now stop it rather than let it fly.

**12. Any scenario could have narrowed what the seam pass demanded of it.** The
per-scenario override went in for the week 1 harness, which runs before any
swarm node exists and so cannot show the radio or the collector. Nothing
constrained it. A week 3 scenario could have dropped the radio from its own
list, and the graph pass would then have reported a clean seam for a run with no
radio in it, which is the single thing that checker exists to notice. The
allowance is week 1's alone now, and a later scenario that tries it dies naming
itself. Watched failing.

**13. Carriage returns, and one printf broken across a line.** The printf was
already repaired earlier in the day. On the carriage returns the report was
right about the symptom and one step out on the consequence: `stage-1/setup/README.md`
did carry 94 CRLF pairs, but only in the working tree. `.gitattributes` forces
LF on checkout and git had normalised the committed blob, so a fresh clone was
always clean. The risk was local, and it is the local copy that a gate runs
against, which is the whole point: a shell script with a CR dies on line 1 while
the caller reports success. `check_docs.py` now scans every tracked text file by
suffix, 124 of them, rather than only the shell scripts.

**8. The four runs cited for the launcher fix name a commit that predates it.**
Confirmed, and already closed. They were made with the fix in a dirty worktree,
so their `source_tree_sha256` binds to the code that produced them and nothing
about them is false, but a reader following `commit_sha` lands on a tree without
the fix. I committed first and ran once more against a clean tree, and that
run's tree digest is the digest of its own commit exactly. The week notes now
cite that run.

**14. The sentence saying this week was not accepted was itself the receipt
saying it was.** Found while running the closing checks, not by the audit pass.
The week notes explained the done marker by quoting it, and the loop greps for
that literal string rather than reading the sentence around it. So the
supervisor would have read week 1 as accepted, on the strength of a paragraph
whose entire point was that it had not been.

The notes no longer spell the marker out, and `check_docs.py` fails any week
file carrying its own done marker while `submission/human-preflight.json` is
absent. That condition is exact rather than a judgment about prose: every week
gate runs the preflight first and the preflight refuses without that file, so a
marker written before it exists cannot have been earned. Watched failing.

This one is worth more than its size. Every other finding here is about a
checker that might miss something. This is a document that could have told the
automation the opposite of what it said to a human, and the two readers would
never have compared notes.

## Carried into week 2

**1. `run_smoke.sh` does not write a run record, and `architecture.md` says it
does.** The four smoke files carry `"kind": "smoke-placeholder"` and a note
admitting they do not validate, and the validator lists 19 missing required
fields. The note also says the script will be rewritten to call the real writer,
which is a reasonable deferral, made when the writer did not exist. What is not
reasonable is that the week notes cite those runs as chunk 1.2's proof while
listing three outstanding items, none of them this. A deviation from a frozen
file was being carried with no ticket, no gate and no mention. It is written
down now.

**10. Poses are sampled at a quarter of the rate the design freezes.** The
runner samples at 5 Hz, hardcoded as a module constant, while `architecture.md`
names 20 Hz as the coverage source and again for the separation monitor. Week 2
computes coverage off sampled poses, so this is not cosmetic: a survey scored at
a quarter of the intended resolution is a different measurement, and the rate is
not read from the scenario file, so no scenario can correct it. **This is the
first thing to fix in week 2.**

**11. Nothing records where each vehicle stands, and this week moved them.** The
spawn line went from running out from the origin to being centred on it, which
shifted every vehicle. Position comes from PX4's local frame, whose origin is
each vehicle's own spawn point, while the design fixes all geometry in one frame
with the ground station at the origin. The record carries a home altitude per
vehicle and no home x or y. Any later week converting sampled poses into the
frozen frame needs those offsets, and the only place they are written down is an
awk expression in a shell script. The launcher prints them; the runner should
record them.

**4. The plan's non-chunk deliverable for week 1 was not produced.** The plan
says "Also this week: the proposal's architecture section", and no proposal file
is tracked. `check_docs.py` matches the plan's 25 chunk rows against the gate
and does not look at the "Also this week" lines at all, so the same hole is open
in every week, and three more of these sections are due. This one needs writing
rather than fixing, and the proposal is a deliverable the submission cannot go
without.

## What checks out

Every headline count in the week notes is right except the two in finding 6, and
those are corrected. Run in my own shell after all the changes above: 179
package tests, 48 seam fixtures, 53 submission checks, 20 rehearsal checks, 8
preflight decisions, 19 launcher geometry checks, all 113 gate expressions
parsed, 21 shell scripts, static seam pass clean, `check_docs.py` clean.

The altitude and preparation tables match `max_altitude_m` and
`preparation_seconds_wall` in all six run records, including the outlier from
before the launcher fix. All ten gate expressions and the graph pass hold on the
closing run. An absent scenario still exits 10 in both flag orderings and leaves
`latest` byte for byte unchanged.

On test quality the injector and snapshot suites are behavioural rather than
mirrors. The injector's fake world deliberately leaves its apply and visibility
hooks unconnected, so a test would fail an implementation that stamped an event
at request time instead of at observation, which is precisely the question the
plan asks of that chunk. The snapshot suite asserts atomic publish, byte
identical rewrites, no leftover temporary file, and survival of the previous
snapshot when a rename fails. The resource sampler is good on the question it
was built for and its last test was the third home of the memory ceiling, which
is finding 2.

`.claude/codemap.md` does not exist in this repo, so the codemap item was
skipped rather than answered with invented findings.

Cannot certify: approach correctness, statistical validity of results, anything requiring the blocked or external resources.
Findings: 14
