# Codex prompt, round 8

Paste `_codex-context.md` first, then everything below the line. Run Codex in this repo.

Rounds 2 through 7 used a review-only brief: report findings, the author edits. Round 8 changes that. You explore options, choose, edit, and keep going until the repository is green and you are satisfied with the plan. Round number is the only thing that changes from here.

---

You are the second engineer on this project, not an outside reviewer. Rounds 1 through 7 raised 58 findings and every one is fixed. This round you fix things yourself.

Read `_codex-context.md` before this if it was not pasted above. It has the competition, the state of the repo, and the failure this project keeps repeating.

## What is different this round

Previous rounds stopped at "here is what is wrong". That was right while the plan was still moving. It is now 30 August, the build starts today, 28 days remain, and a round that costs a day and produces a list is worth less than one that costs a day and produces a repository that holds.

So: **find the problems, work out the options, pick one, make the change, and prove it.** The output is a working tree, not a report.

Three constraints on that freedom, and they are absolute.

1. **Never change a frozen value to make a gate pass.** Every parameter in `stage-1/architecture.md` and every threshold in `scripts/gate.sh` is fixed. Changing one so a check goes green is moving the goal. If a number is unreachable, that is a finding: say so with the arithmetic and leave it.
2. **Do not write implementation code.** No `uavx_ws` packages, no node stubs, no scenario YAML. Design, architecture, structure, gates, checkers and documents only. The weeks build the system; this round makes sure they can.
3. **Do not delete a check to make a suite pass.** If a fixture fails, either the fixture is wrong and you say why in the commit, or the thing it checks is wrong and you fix that.

## Read in this order

1. `context.md`, the published competition record. Ground truth. If the plan contradicts it, the plan is wrong.
2. `stage-1/plan.md`. Four weeks, 25 chunks.
3. `scripts/gate.sh`. The only acceptance contract. Read it as adversarially as the plan.
4. `stage-1/architecture.md`. The frozen design.
5. `stage-1/decisions.md` and `.claude/weekly-loop.md`.
6. The rest of `scripts/`, `scenarios/run-record.schema.json`, `submission/human-preflight.schema.json`.
7. `.claude/review-status.json`, then `_plan-review-round1.md` through `round7.md`.

## Step 1: find the problems

Two sources, and both count.

**Still open.** Read round 7's findings and confirm each fix actually holds. Four of this project's defects were introduced by the fix for an earlier round, so verify rather than trust.

**Your own.** Look hardest at design, architecture and structure, which is where the remaining risk is:

- **Design.** Does the routing, role election, handback and store-and-forward design do what the rubric asks? Is any of it underspecified enough that two agents would build different systems? Is any of it more complicated than the marks justify, given 28 days?
- **Architecture.** The tx/rx seam, the layering, the message set, the run record contract, the provenance chain. Where does an implementing agent have to guess?
- **Structure.** 25 chunks across four weeks. Are the boundaries in the right places? Is each chunk a piece of work somebody could finish and check in a sitting? Does the dependency order hold, meaning can chunk N actually be built and run when everything before it exists and nothing after it does? Round 7 finding 1 was exactly this and it was missed for a full round.

Also carry forward the standing question this project keeps failing: **which gates would report success on a broken system?** The Gazebo case is the template. Pay particular attention to anything added since round 6, including the round 7 fixes.

## Step 2: three or four options for every problem

This is the part that matters most, and it applies to every open problem and every problem you find yourself.

For each one, work out **at least three genuinely different approaches**, four where the problem is large. Different means different in mechanism, not three phrasings of the same idea. If two of your options differ only in a constant, you have one option and a parameter.

For each option, state:

- **How it works**, concretely enough to implement.
- **What it costs**, in hours against a 140 hour budget, and in complexity the weeks have to carry.
- **What it gives up**, including which rubric rows it weakens.
- **How it fails**, meaning the way this option breaks that the others do not.

Then pick one and say why, and say plainly what you are giving up by picking it. A choice with no cost is a choice you have not understood.

Where an option would need a number that nobody has measured, say so and choose the option that does not depend on it. Two of this project's worst bugs came from a threshold that looked reasonable and had never been derived.

**Write every option down, including the rejected ones**, in `_design-options-round8.md`. The rejected options are the valuable part of that file: when a week runs out of time and the chosen approach does not land, the fallback is already worked out. Use this structure:

```
# Design options, round 8

## Problem <n>: <one line>
**Where it bites** <the chunk, week or rubric row>
**Found by** <round 7 finding N | this round>

### Option A: <name>
How it works. Cost. What it gives up. How it fails.

### Option B: <name>
...

### Option C: <name>
...

### Chosen: <A|B|C|D>
Why, and what this costs us.
**Fallback if it does not land:** <which other option, and the trigger to switch>
```

## Step 3: make the change

Edit the plan, the architecture, the decisions, the gate and the checkers to match what you chose. Add fixtures for anything you changed the behaviour of.

House rules, which are the author's and not negotiable:

- Prose in files uses no em dashes and no en dashes. A comma, a semicolon, "and", or a new sentence. Plain hyphen for ranges.
- Commit on `main`. Never push, amend, force, branch, tag or rewrite history.
- Commit messages: imperative, sentence case, no prefix, roughly 50 to 72 characters in the subject, body wrapped near 72 explaining why rather than what. No emoji. No `Co-Authored-By`, no AI or model attribution, no "generated by" line.
- Split substantial work into several commits, by concern, in dependency order.
- Every checker in this repo carries a comment saying which round found the defect it exists for and what the defect was. Keep that habit; it is why the repository is auditable.

## Step 4: run it until you are happy

After every change, and again after your last change:

```
python3 scripts/check_geometry.py
python3 scripts/check_docs.py
python3 scripts/test_seam_fixtures.py
python3 scripts/test_submission_fixtures.py
python3 scripts/test_dryrun_fixtures.py
python3 scripts/test_gate_preflight.py
python3 scripts/test_require_grammar.py
bash    scripts/check_shell.sh
python3 scripts/check_competition_spec.py
bash    scripts/gate.sh chunks
```

All must exit 0. `check_dryruns.py` exits 1 until W3 has run, which is correct and expected.

Green is not the finish line. **A suite that passes proves less than one you have watched fail for the right reason.** For every check you touched, break the thing it guards, confirm it fails with a diagnostic that names the real cause, then put it back. Three times in seven rounds the bug was in the checker rather than in the thing checked, and a checker that passes for the wrong reason is worse than none.

Two traps specific to this machine:

- `bash -n` exits 0 on a file that does not parse under bash 5.1, which is what the target runs. Empty stderr is the only reliable signal. `scripts/check_shell.sh` already does this.
- A `.sh` saved with CRLF dies on line 1 and the script above it reports success. Run `check_shell.sh` after any shell edit.

Iterate until every command above is green and you have no finding you are choosing to ignore. If you run out of ideas before you run out of problems, say so rather than declaring victory.

## Step 5: write it up

Write `_plan-review-round8.md`:

```
# Plan review, round 8 (Codex)

Reviewed and edited: <files, with the commit sha of HEAD at start and at end>

## Still open from earlier rounds
<finding id, which round raised it, whether the round 7 fix holds. "None" if none.>

## What I changed
<one line per commit, with the sha>

## Problems and choices
<one line per problem, naming the chosen option. The reasoning is in _design-options-round8.md.>

## Findings I did not fix
### Finding <n>, <critical|significant|moderate|minor>: <one line>
**The problem** <what breaks, with reasoning and numbers where numbers apply>
**Why I left it** <needs a decision that is not mine, needs a measurement nobody has taken, or costs more than it saves>
**What I would do** <the option you would pick, and from which section of the options file>

## Verification
<the ten commands, their exit codes, and what you broke on purpose to prove each suite works>

## Verdict
One of: READY TO EXECUTE / EXECUTE WITH THE FIXES ABOVE / NOT READY, and one paragraph on why.

## For the next round
<what round 9 should check, given what changed>
```

End with the literal line `Findings: <count>`, counting only the ones you did not fix.

## On the verdict

Seven rounds have returned NOT READY. The build starts today with 28 days left, and the human preflight blocks every gate regardless of what you do here.

So reserve NOT READY for something that genuinely stops the build: a chunk that cannot run in its own week, a gate that certifies work nobody did, a design that cannot produce what the rubric asks for. If what remains is a list of moderate defects with cheap fixes, say EXECUTE WITH THE FIXES ABOVE and let the weeks start. An eighth NOT READY over refinements costs more than it is worth, and saying so is part of your job.

Do not report the human preflight as a finding. It is known, it blocks every gate by design, and it is the author's to do.
