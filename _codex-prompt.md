# Codex prompt, round 9

Paste `_codex-context.md` first, then everything below the line. Run Codex in this repo.

---

You are the second engineer on this project. Rounds 1 through 8 raised 77 findings and every one is fixed. Round 8 fixed things itself; this one does not.

Read `_codex-context.md` before this if it was not pasted above. It has the competition, the state of the repo, and the failure this project keeps repeating.

## What is different this round

There is an implementation now. Three weeks are built and accepted, week 4 is half done, and 20 days remain before the send on 26 September. The author is working in this tree today.

So round 9 is review only. **Do not edit anything except your own three output files.** Two agents editing the same working tree is how this project makes its next self-inflicted defect; four of the 77 findings were introduced by the fix for an earlier one. You find the problems and work the options, the author applies them.

Two jobs, and the first one blocks a chunk right now.

## Read in this order

1. `context.md`, the published competition record. Ground truth. If anything contradicts it, that thing is wrong.
2. `stage-1/plan.md`. Four weeks, 25 chunks.
3. `scripts/gate.sh`. The only acceptance contract, 113 `--require` expressions. Read it as adversarially as the plan.
4. `stage-1/architecture.md`. The frozen design, and section 8 for what each rubric row is earning.
5. `docs/progress/week-1.md` through `week-3.md`, and `docs/audits/week-1.md`.
6. `uavx_ws/src/`, seven packages. `uavx_comms` is where the mesh lives and where week 4 has been working.
7. `stage-1/decisions.md`, `.claude/weekly-loop.md`, `.claude/review-status.json`, then `_plan-review-round8.md`.

## Job 1: queue_drain asks for two things that cannot both happen

Chunk 4.4 flies `scenarios/queue_drain.yaml`, which gates uav_2's radio for 45 s with the election forbidden so nothing shortens the outage. uav_3 and uav_4 lose their route to the ground station and keep observing through it. The custody rule funnels the component's backlog into one member, uav_3, and 45 s at 5 Hz from 2 origins is 450 observations.

Four of the gate's 25 requirements are about that queue:

```
--require "observations.peak_queue_depth>=450"
--require "observations.peak_queue_depth<=512"
--require "observations.custodied>=450"
--require "observations.backlog_drain_s<=2.25"
```

`backlog_drain_s` is the moment the custodian's store first ran empty, minus the moment the last cut off member's route returned. The 2.25 comes from `architecture.md` section 3: 450 packets at the 200 per second forward rate. That derivation leaves out three things.

1. The custodian's own vehicle keeps observing into the same queue while it drains, so the queue empties at 200 minus 5, which is 195 per second. 450 / 195 = 2.3077 s.
2. `/clock` runs at 10 Hz on this stack, so the service loop moves at most 20 packets per clock step. Ceiling of 450 / 20 is 23 steps, which is 2.30 s with no inflow at all.
3. The queue holds more than 450 when the route returns. The route comes back about 2.5 s after the radio does, because the neighbour has to be heard again at the 1 s hello period and the link state has to travel at the 2 s LSA period, and both cut off origins mint through that. Measured peak was 472, and 472 / 195 = 2.42 s.

Live run `queue_drain_20260905T213058Z`, 5 September: 23 of the 25 requirements green, `peak_queue_depth` 472, `custodied` 447, `backlog_drain_s` 2.30. The radio gated itself at 61.2 and lifted at 106.2 against a commanded 60 and 105. uav_3 and uav_4 lost their routes at 63.9 and had them back at 108.7 and 108.8; uav_3's store ran empty at 111.1. That run is not committed, because committing a record that fails its own gate is what broke chunk 4.1 once. Reproduce it with `bash scripts/gate.sh 4.4` if you want the artifacts, and expect 10 minutes.

The author's position, which you should test rather than accept: **both claims are worth keeping.** A peak depth the design is sized for and a bound on clearing it are each doing real work for the 25% communication row, and dropping either one is worse than fixing the design. So look for a mechanism that makes both true before you conclude the contract is wrong.

Standing rule 2 applies to you as it does to the weeks. Never change a frozen value to make a gate pass. If the honest answer is that the bound is unreachable, that is a finding about the contract and the replacement number has to be derived from the frozen parameters rather than picked to fit the measurement.

Some directions exist and none of them is the answer yet: what the drain is measured over, how the service discipline treats new observations against a backlog, how long the mesh takes to notice a neighbour is back, and whether the clock rate the whole stack runs at is a frozen value or an accident. Find your own; those are named so you do not spend the round rediscovering them.

## Job 2: audit what has been built against the plan and the goal

Three weeks accepted, three chunks of the fourth done, and nobody has read the whole thing since it became an implementation. Six areas, and the first two are where I expect the findings.

**Do the accepted weeks still mean what their notes say?** Week 4 changed the meaning of several record fields after weeks 1 to 3 were accepted: the split between deferred and evicted packets, the drain bound scoped to the members an outage cut off, recovery numbers that no longer count the vehicle a fault was applied to, a router that keeps its route history rather than the latest value, a blackout window bounded by the command as well as measured, and a retry interval that changes how much traffic a recovery makes. Those records were read under the older meanings. Are the week 2 and week 3 claims still true of the records that back them, and does anything in `docs/progress/` now overstate what its run showed?

**Which gates would report success on a broken system?** This is the standing question and it has found something every round. The template is in the context file. Look hardest at anything added since week 1, meaning the whole of `uavx_eval`, `uavx_gcs`, `uavx_comms`, `uavx_roles` and the week 4 additions to `uavx_sim`.

**Is the remaining plan executable in the days left?** 4.5 through 4.8 against 20 days, with 4 of the 9 required runs still unmade and 8 package items still missing. Check the dependency order holds and that no chunk needs something a later chunk produces.

**The submission tail.** `docs/proposal/` has 1 of 4 sections. Standing rule 5 says each week drafts its own while the work is fresh, and weeks 1 and 2 did not, so the architecture and mission sections are owed and 4.8 was never sized to write them. The only video in `submission/` is the dry run rehearsal. Say plainly whether the tail as planned fits.

**Does the evidence fit the email?** A week 4 run record is roughly 760 KB, up from 68 KB, because the id lists and per delivery rows are what proves delivered once. Nine of those plus the archive, the proposal and the video against a 25 MB attachment cap, with the organiser's answer on the delivery route still outstanding.

**Do the week 4 design changes weaken any claim?** Two are judgement calls the author flagged and neither has been reviewed. Splitting deferred from evicted changes what `observations.evicted==0` measures. Excluding the fault target from the recovery arithmetic changes what "recovered" means. Both are reported in full in the record, and the question is whether a judge reading the record could disagree with either.

## Options, for every problem in both jobs

For each problem, work out **at least three genuinely different approaches**, four where the problem is large. Different means different in mechanism. If two of your options differ only in a constant, you have one option and a parameter.

For each option: how it works, concretely enough to implement; what it costs, in hours against the days that are left and in complexity the remaining chunks have to carry; what it gives up, naming the rubric rows it weakens; and how it fails, meaning the way this option breaks that the others do not.

Then pick one and say why, and say what picking it costs. A choice with no cost is a choice you have not understood. Where an option needs a number nobody has measured, say so and prefer the option that does not need it.

Write every option down, rejected ones included, in `_design-options-round9.md`:

```
# Design options, round 9

## Problem <n>: <one line>
**Where it bites** <the chunk, week or rubric row>
**Found by** <job 1 | job 2 | this round>

### Option A: <name>
How it works. Cost. What it gives up. How it fails.

### Option B: <name>
### Option C: <name>

### Recommended: <A|B|C|D>
Why, and what it costs.
**Fallback if it does not land:** <which other option, and the trigger to switch>
```

The rejected options are the valuable part of that file. When a chunk runs out of time and the chosen approach does not land, the fallback is already worked out.

## What you may and may not touch

You may write `_plan-review-round9.md`, `_design-options-round9.md` and `.claude/review-status.json`, and commit those three. Nothing else in the tree, including tests, fixtures, the gate and the documents. If a fix is one line, still do not make it; write it down.

You may run anything read only. These all pass on the current tree and are worth running before you trust a claim about them:

```
python3 scripts/check_geometry.py
python3 scripts/check_docs.py
python3 scripts/check_submission.py
python3 scripts/test_seam_fixtures.py
python3 scripts/test_submission_fixtures.py
python3 scripts/test_gate_preflight.py
python3 scripts/test_require_grammar.py
python3 scripts/test_record_contract.py
bash    scripts/check_shell.sh
bash    scripts/gate.sh chunks
```

Package tests run one package at a time, because two `test/conftest.py` files in one pytest run collide:

```
python3 -m pytest -q uavx_ws/src/<package>/test/
```

`check_submission.py` reports NOT READY with 8 problems and that is the current true state, not a finding.

Do not run a live scenario or a week gate. They need the WSL simulator stack, take about 10 minutes each, and they write into `runs/`.

House rules if you commit your three files: prose carries no em dashes and no en dashes, use a comma or a semicolon or "and" or a new sentence, plain hyphen for ranges. Commit on `main`. Never push, amend, force, branch, tag or rewrite history. Subject imperative and sentence case, no prefix, roughly 50 to 72 characters, body wrapped near 72 saying why. No emoji, no `Co-Authored-By`, no AI or model attribution.

## Write it up

`_plan-review-round9.md`:

```
# Plan review, round 9 (Codex)

Reviewed at HEAD <sha>. Read: <files and packages>.

## Job 1: queue_drain
<the mechanism you recommend, one paragraph, pointing at the options file.
If no mechanism satisfies both requirements, say so with the arithmetic and
give the derived correction.>

## Job 2: findings
### Finding <n>, <critical|significant|moderate|minor>: <one line>
**The problem** <what breaks, with numbers where numbers apply>
**Evidence** <the file and line, the run record field, or the command you ran>
**Options** <which section of _design-options-round9.md>
**Recommended** <the option, and roughly what it costs to apply>

## What still holds
<the round 8 fixes and the week 1 to 3 claims you checked and found sound.
Name them; a finding list with no negative space is not an audit.>

## Verification
<the commands you ran, their exit codes, and anything you broke on purpose to
prove a checker works. You may break a checker in a scratch copy, never in the
tree.>

## Verdict
One of: ON TRACK / ON TRACK WITH THE FIXES ABOVE / OFF TRACK, and a paragraph.

## For the author, in order
<the findings ranked by what to fix first, given 20 days and the chunks left.>
```

End with the literal line `Findings: <count>`.

## On the verdict

Reserve OFF TRACK for something that genuinely threatens the submission: a claim the evidence does not support, a chunk that cannot be built in the days remaining, a gate that certifies work nobody did. Three weeks are accepted and the tail is the risk, so if what you find is a list of moderate defects with cheap fixes, say so and rank them.

Do not report the missing package items, the outstanding organiser reply, or the unwritten proposal sections as findings on their own. They are known and they are in the context file. Whether the plan still has room for them is a finding, and that is a different question.
