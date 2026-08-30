# Codex prompt, round 7

Paste `_codex-context.md` first, then everything below the line. Run Codex in this repo.

This replaces `_codex-review-prompt.md`, which was written for the five week plan and for a repo that had no fixture suites. Rounds 2 to 6 used it. Round number is the only thing that changes from here.

---

You are reviewing a competition build plan and the harness that accepts it. You are the second reviewer, and the first six rounds raised 54 findings that are all now fixed. Your job is to find what breaks it, not to admire it.

Read `_codex-context.md` before this if it was not pasted above. It has the competition, the state of the repo, and the failure this project keeps repeating.

## Read in this order

1. `context.md`, the published competition record. Ground truth. If the plan contradicts it, the plan is wrong.
2. `stage-1/plan.md`. Four weeks, 25 chunks. **This was rewritten on 29 August.** Rounds 1 to 6 reviewed a five week version that no longer exists, so do not carry forward a finding about week numbering or week boundaries without rechecking it.
3. `scripts/gate.sh`. The only acceptance contract. Read it as adversarially as the plan; several checkers in this repo have been caught passing on broken systems.
4. `stage-1/architecture.md`. The frozen design. Section 1b, the messages and the script interfaces, is new and has never been reviewed.
5. `stage-1/decisions.md` and `.claude/weekly-loop.md`.
6. The rest of `scripts/`, `scenarios/run-record.schema.json`, `submission/human-preflight.schema.json`.
7. `.claude/review-status.json`, then `_plan-review-round1.md` through `round6.md`. Do not re-report a finding an earlier round made unless it is still unfixed. If it is, say so and name the round.

## Run these, do not read them

```
bash scripts/gate.sh chunks
python3 scripts/check_docs.py
python3 scripts/check_geometry.py
python3 scripts/test_seam_fixtures.py
python3 scripts/test_submission_fixtures.py
python3 scripts/test_dryrun_fixtures.py
python3 scripts/test_gate_preflight.py
bash    scripts/check_shell.sh
```

A fixture suite that passes proves less than one you have watched fail for the right reason. **Break something and confirm the suite notices.** Three times in six rounds the bug was in the checker rather than in the thing checked, and twice a fix introduced the next round's finding.

Note that `bash -n` exits 0 on a file that does not parse under bash 5.1, which is what the target runs. Do not use it as a pass or fail signal.

## What to check

**1. The new plan shape.** Four weeks, 30 August to 26 September, with the packaging distributed into the weeks instead of pooled in a tail. Does it fit 30 hours a week? Which week overruns first, and what should move when it does? W4 holds roles, fault recovery, safety and the send in seven days. Is that survivable, and if not, what is the honest cut?

**2. Chunk integrity.** 25 chunks, each with its own gate. For each: does it fail when its work is not done? Can it pass while the thing is broken? Can it be satisfied by writing a file? Are the chunk boundaries in the right places, meaning is each one a piece of work somebody could finish and check in a sitting? Name any chunk that is decoration.

**3. Week 1 specifically.** It was the least reviewed week in six rounds, because every reviewer started at the interesting parts, and its gate ran one command against seven deliverables until 29 August. Everything later writes into what W1 produces. The five messages and the two script interfaces in `architecture.md` section 1b were written this week and nobody has read them. Are they specified tightly enough that two implementations would agree?

**4. Thresholds.** Every number in a gate. Is it achievable by a correct implementation, and strict enough that a broken one fails? Show the arithmetic. The reconnect budget, the coverage fraction, the delivery ratios, the drain bound, the separation floor, the memory ceiling.

**5. The tx/rx seam.** 25% of the rubric rests on it. Is it enforceable? What are the ways an implementing agent breaks it without noticing, and does the plan close them?

**6. What the rubric asks for that the plan does not build.** Criterion by criterion through the table in `_codex-context.md`. Point at the specific artifact earning each row. Name any criterion with nothing behind it.

**7. Which gates would report success on a broken system.** This is the question this project keeps failing. The Gazebo case is the template: a check asserted on metadata rather than on the artifact, green on a machine that could not run a simulation. Find the others. Pay particular attention to anything added since round 5, because two of those additions were themselves findings.

**8. Contradictions.** `check_docs.py` enforces agreement between the plan, the gate, the architecture and the submission checker in several directions. Find a contradiction it cannot see. That is a finding about `check_docs.py` as much as about the documents.

**9. Risk the plan does not name.** Given the environment record in the context file, what else bites, and when?

## What not to do

- Do not write implementation code. No package skeletons, no node stubs.
- Do not rewrite the plan. Report findings; the author edits.
- Do not soften a finding because the plan explains itself well. A confident explanation of a wrong choice is worse than an uncertain one.
- Do not pad. A finding that costs nothing to ignore is noise. Four real findings is a better round than nine padded ones, and the last three rounds all returned exactly nine, which looks more like a budget than a measurement.
- Do not report the human preflight as a finding. It is known, it blocks every gate by design, and it is the author's to do.

## Output

Write to `_plan-review-round7.md`. Use exactly this structure:

```
# Plan review, round 7 (Codex)

Reviewed: <files, with the commit sha of HEAD>

## Still open from earlier rounds
<finding id, which round raised it, why it is still open. "None" if none.>

## Findings

### Finding <n>, <critical|significant|moderate|minor>: <one line>
**The plan says** <quote or precise reference>
**The problem** <what breaks, with reasoning and numbers where numbers apply>
**Fix** <the specific change, concrete enough to action without another round>

## Verdict
One of: READY TO EXECUTE / EXECUTE WITH THE FIXES ABOVE / NOT READY, and one paragraph on why.

## For the next round
<what round 8 should check, given what changed>
```

Order findings by damage done if left alone. **critical** costs a week or the submission. **significant** costs days or measurable marks. **moderate** is a real defect with a cheap fix. **minor** is worth knowing.

Say plainly if the plan is ready to execute. Six rounds have returned NOT READY and the calendar has 29 days left in it; a seventh NOT READY over minor findings costs more than it is worth, so reserve the verdict for something that genuinely stops the build.

End with the literal line `Findings: <count>`.
