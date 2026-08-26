# Codex review prompt

Copy everything below the line into Codex, running in this repo. Reuse it unchanged for every round. Round number is the only thing that changes, and Codex reads the previous round's findings file to avoid repeating itself.

Rounds land in `_plan-review-round<N>.md`. Round 1 exists already and was written by Claude, so Codex starts at round 2.

---

You are reviewing a competition build plan before any implementation code is written. You are the second reviewer. Your job is to find what breaks it, not to admire it.

## Context you must read first, in this order

1. `context.md` - the official competition facts, pulled from the Techfest API. This is ground truth. If the plan contradicts it, the plan is wrong.
2. `stage-1/plan.md` - the week-by-week build plan you are reviewing.
3. `stage-1/decisions.md` - the locked decisions and the go/no-go gate per week.
4. `.claude/weekly-loop.md` - the automation config. The gate commands here must match the plan exactly.
5. `stage-1/setup/` - the provisioning scripts, already run on the target machine.
6. `_plan-review-round1.md`, and every later round file that exists. Do not re-report a finding an earlier round already made unless it is still unfixed, and if it is, say so and say which round raised it.

## What you are reviewing

A solo entrant, 4 to 6 hours a day, has until 27 September 2026 to submit five things by email: a 6 to 8 page technical proposal, a working proof of concept simulation, source code, installation instructions, and a demo video.

The plan will be executed by an autonomous agent loop. One tick runs one plan week. A week is accepted only when its gate commands exit 0 in a supervisor's shell. The agent cannot ask questions mid-week; it either follows the plan, takes a stated fallback, or halts. **This is why the plan has to be rigid.** Anything the plan leaves ambiguous becomes a coin flip made by an agent with no context, and a wrong flip costs a week out of five.

Judge everything by that standard: not "is this a reasonable plan for a human" but "can an agent execute this without guessing".

## Check these specifically

**1. Gate integrity.** Every gate must be a command that exits non-zero when the work is not done. For each gate, ask: does it fail when it should? Can it pass while the underlying thing is broken? Can it be satisfied by writing a file rather than doing the work? Name any gate that is decoration.

**2. Thresholds.** Every number in a gate was chosen by the plan's author and most were not derived from anything. `coverage_fraction>=0.95`, `delivery_ratio>=0.95`, `time_to_reconnect_s<=30`, `separation_violations==0`, `mean_hop_count>1.0`, the `delivery_ratio_by_node.uav_4<=0.05` control. For each: is it achievable by a correct implementation, and is it strict enough that a broken one fails? Show your reasoning with numbers. The reconnect budget in particular has to cover neighbour timeout plus election plus flight time to the new position, and the plan admits it has not done that arithmetic.

**3. The tx/rx seam.** The whole communication-resilience claim, 25% of the rubric, rests on swarm nodes only ever talking through `/uavx/<id>/tx` and `/uavx/<id>/rx`. Is that enforceable by a test? Is the test described specific enough to write? What are the ways an implementing agent breaks the seam without noticing, and does the plan close them?

**4. Week sizing.** Five weeks, one person, 4 to 6 hours a day, roughly 30 hours a week. Take each week's deliverables and say whether they fit. W3 builds a link model, a link layer, a link-state router, two scenarios and a test suite. W5 has 4 days for a proposal, a video, a packaging pass and a fresh-machine install test. Which week is the one that overruns, and what should move?

**5. Technical soundness of the design.** The architecture section specifies the link model, the routing protocol, the role election and the safety layers. Look for: things that will not work as described, things underspecified enough that two implementations would differ, and missing pieces the rubric asks for. Note that safety and collision avoidance carries 10% and is currently three bullet points.

**6. Contradictions between files.** The gate commands in `.claude/weekly-loop.md` must match `stage-1/plan.md` exactly. The gate dates in `stage-1/decisions.md` must match the week boundaries in the plan. The version pins must match what `stage-1/setup/` actually installs. Any drift here means the loop enforces something different from what the plan says.

**7. What the rubric asks for that the plan does not build.** Go criterion by criterion through the judging table in `context.md`. For each, point at the specific plan artifact that earns those marks. Name any criterion with nothing behind it.

**8. Risk the plan does not name.** The environment is WSL2 on Windows with Docker Desktop installed. Getting the stack up on 26 August took seven failed runs, and the failures are worth reading as a pattern rather than a list, because the plan assumes this environment is now understood:

- DNS dropped when Docker Desktop reconfigured the WSL NAT.
- A long build running off the `/mnt/c` 9P mount lost its connection and died.
- WSL terminated the distro whenever no client was attached, killing detached builds.
- `gazebo --version` crashes the entire WSL distribution through the dxg GPU shim.
- The osrfoundation apt repo serves Gazebo Garden on jammy, and `gz-tools2` conflicts with `gazebo`, so apt installed the Gazebo Classic libraries and silently skipped the binaries. `verify.sh` passed for hours on a machine with no `gzserver`.
- The Micro XRCE-DDS Agent version the PX4 docs name pins a Fast DDS branch that no longer exists upstream.

Four of those seven presented as something other than their cause. Two passed a green check while broken. Four PX4 instances plus gzserver plus our nodes then have to fit in roughly 11 GB of WSL memory, which nobody has measured.

Given that record, the question for you is not just "what else will bite". It is: **which gates in this plan would report success on a broken system?** The Gazebo case is the template. A check asserted on package metadata rather than on the artifact, and it passed on a machine that could not have run a simulation. Find the others.

## What not to do

- Do not write implementation code. No package skeletons, no node stubs. The plan is being reviewed, not built.
- Do not rewrite the plan. Report findings; the author edits.
- Do not soften a finding because the plan explains itself well. A confident explanation of a wrong choice is worse than an uncertain one.
- Do not pad the list. A finding that costs nothing to ignore is noise. If you have four real findings, report four.

## Output

Write to `_plan-review-round<N>.md` where N is this round's number. Use exactly this structure:

```
# Plan review, round <N> (Codex)

Reviewed: <files, with the commit sha of HEAD>

## Still open from earlier rounds
<finding id, which round raised it, and why it is still open. "None" if none.>

## Findings

### Finding <n>, <critical|significant|moderate|minor>: <one line>
**The plan says** <quote or precise reference>
**The problem** <what breaks, with reasoning and numbers where numbers apply>
**Fix** <the specific change, concrete enough to action without another round>

## Verdict
One of: READY TO EXECUTE / EXECUTE WITH THE FIXES ABOVE / NOT READY, and one paragraph on why.

## For the next round
<what round N+1 should check, given what changed>
```

Order findings by damage done if left alone. Severity means: **critical** costs a week or the submission; **significant** costs days or measurable marks; **moderate** is a real defect with a cheap fix; **minor** is worth knowing.

End with the literal line `Findings: <count>`.
