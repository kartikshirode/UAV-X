#!/usr/bin/env python3
"""Catch documents that contradict each other, or contradict the gate.

Round 3 finding 10: a week-agent was being handed files that told it both to use
and never to use the launcher that crashes the distro, and both that the
environment existed and that nothing was installed. Those statements sat in the
same file, the correct one first. An agent has no way to know which wins.

Round 4 found this file reporting agreement over three documents that were
plainly out of date, and doing it in the two ways a checker usually fails.

Finding 9: it looked for the exact sentences that were wrong last time. The
stale-state patterns knew about rounds 1 and 2, so round 3 walked past them, and
the allowed-distance list was maintained by hand, so any new derived number
reopened the door. Both are now derived: review state comes out of
.claude/review-status.json, and every distance out of check_geometry.py.

Finding 1: nothing compared what the plan promised with what the gate runs. The
plan called mission_integrated the proof of concept the whole submission rests
on, and no gate ran it. Prose and a shell script drifting apart is the same
failure as two documents drifting apart, and it costs more.

    python3 scripts/check_docs.py

Exit 0 if the documents agree with each other and with scripts/gate.sh.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_geometry                                       # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DOCS = ["handoff.md", "context.md", "stage-1/plan.md", "stage-1/decisions.md",
        "stage-1/architecture.md", ".claude/weekly-loop.md",
        "stage-1/setup/README.md", "stage-1/human-preflight.md"]

problems: list[str] = []


def read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def guard(before: int, msg: str) -> None:
    if len(problems) == before:
        ok(msg)


text = {d: read(d) for d in DOCS}

print("--- banned claims")

# The launcher. Using PX4's own script crashes the distro, so no document may
# recommend it except to say not to.
before = len(problems)
for doc, body in text.items():
    for m in re.finditer(r"^.*sitl_multiple_run\.sh.*$", body, re.M):
        line = m.group(0)
        if not re.search(r"do not use|never|ignores|instead of|rather than|crashes|not by", line, re.I):
            fail(f"{doc} mentions sitl_multiple_run.sh without saying not to use it: {line.strip()[:90]}")
guard(before, "no document recommends PX4's own multi-vehicle launcher")

# Thresholds live in gate.sh alone. Round 2 finding 2 was exactly this drift.
before = len(problems)
for doc, body in text.items():
    for m in re.finditer(r"(coverage_fraction|delivery_ratio\w*|time_to_reconnect_s|separation_violations|role_changes)\s*(>=|<=|==|<|>)\s*[0-9.]+", body):
        fail(f"{doc} restates a gate threshold: {m.group(0)}. Thresholds live only in scripts/gate.sh.")
guard(before, "no document restates a gate threshold")

# Superseded environment state.
before = len(problems)
STALE = [
    (r"[Nn]othing runs yet", "claims nothing runs, but the environment is up"),
    (r"[Nn]othing is installed", "claims nothing is installed"),
    (r"only distro is `?docker-desktop", "claims the only WSL distro is docker-desktop"),
    (r"WSLg has never produced a display", "claims WSLg gives no display, but DISPLAY is set"),
]
for doc, body in text.items():
    for rx, why in STALE:
        if re.search(rx, body):
            fail(f"{doc} {why}")
guard(before, "no document carries superseded environment state")

print("\n--- mutable state belongs in a file, not in prose")

# Round 4 finding 9. Describing what a past round FOUND is history and is fine.
# Claiming what is currently open, unrun or unfixed is state, and prose does not
# update itself. .claude/review-status.json is the one place it lives.
status_path = REPO / ".claude" / "review-status.json"
if not status_path.is_file():
    fail(".claude/review-status.json is missing, so nothing records the review state")
else:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    ok(f"review status: round {status['latest_round']}, {status['state']}")

    before = len(problems)
    CURRENT_STATE = re.compile(
        r"(has not (run|happened)|not yet run|has yet to run|still open|is open"
        r"|remains open|are open|not been read|outstanding)", re.I)
    for doc, body in text.items():
        for line in body.splitlines():
            if re.search(r"[Rr]ound \d", line) and CURRENT_STATE.search(line):
                fail(f"{doc} states review status in prose: {line.strip()[:88]}. "
                     f"That belongs in .claude/review-status.json.")
    guard(before, "no document states review status in prose")

# The human preflight items block the loop. Round 3 finding 4 made them
# blocking; round 4 finding 7 found two documents still filing them as backlog,
# which is the version a week-agent would believe.
before = len(problems)
HUMAN_ITEMS = re.compile(r"(techfest\.org|WhatsApp|organiser email|eligibilit|attachment)", re.I)
NOT_BLOCKING = re.compile(r"(not blocking|Open items|Still open|backlog|nobody has done)", re.I)
# Third rule in this file to need one of these, so it is worth naming the
# pattern: a checker that looks for a phrase and not for its negation fails on
# the sentence explaining that the phrase no longer applies. Round 3 caught it
# on the launcher rule, round 4 on the vehicle count.
UNDONE = re.compile(r"(used to|no longer|not backlog|never |do not|is not|are not"
                    r"|rather than|instead of|refuses)", re.I)
for doc, body in text.items():
    if doc == "stage-1/human-preflight.md":
        continue
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not NOT_BLOCKING.search(line) or UNDONE.search(line):
            continue
        window = "\n".join(lines[i:i + 8])
        if HUMAN_ITEMS.search(window):
            fail(f"{doc} files a human preflight item as optional: "
                 f"{line.strip()[:80]}. gate.sh preflight refuses to run "
                 f"without submission/human-preflight.json.")
guard(before, "the human preflight items are blocking everywhere they appear")

print("\n--- agreement across documents")

# Vehicle count is fixed at 4 and the recovery topology depends on it.
before = len(problems)
for doc, body in text.items():
    for m in re.finditer(r"^.*(drop|cut|reduce|fall back).{0,30}(to )?(2|two|3|three) vehicles.*$", body, re.I | re.M):
        line = m.group(0)
        # "do not drop to 2 vehicles" is the correct statement, not a violation.
        if re.search(r"\b(do not|don't|never|cannot|not)\b", line, re.I):
            continue
        fail(f"{doc} offers a reduced vehicle count, but D4 fixes it at 4: {line.strip()[:80]}")
    # Round 4 finding 9: "use 4 to 6" was sitting in handoff.md while D4 says
    # exactly 4, and every frozen coordinate assumes 4.
    for m in re.finditer(r"^.*\b\d\s*(to|or)\s*\d\s*(vehicles|drones)\b.*$", body, re.I | re.M):
        fail(f"{doc} states a vehicle range: {m.group(0).strip()[:80]}. D4 fixes it at exactly 4, and the frozen geometry has four positions.")
guard(before, "the vehicle count of 4 is not contradicted anywhere")

# Round 8 found active W5 labels after the five-week plan had been compressed
# to four. A week-agent could read them as work after W4, when no such tick
# exists. Historical review files are not in DOCS; active documents use 4.8.
before = len(problems)
for doc, body in text.items():
    if re.search(r"\bW5\b", body):
        fail(f"{doc} still labels active work as W5. The plan has four weeks; "
             f"the packaging and send chunk is 4.8.")
guard(before, "no active document invents a fifth week")

# Radio parameters must match check_geometry.py, which is what proves them.
arch = text["stage-1/architecture.md"]
for name, value in (("r_full", check_geometry.R_FULL), ("r_max", check_geometry.R_MAX)):
    v = int(value)
    if re.search(rf"`{name}`\s*\|\s*{v} m", arch):
        ok(f"{name} = {v} m agrees between architecture.md and check_geometry.py")
    else:
        fail(f"architecture.md does not state {name} = {v} m, which is what check_geometry.py enforces")

# Every distance quoted in prose must be one the geometry actually produces.
# Round 4 finding 9: the previous version carried a hand-maintained tuple of
# exceptions, so the stale-number class it exists to catch could return behind
# any new derived figure. The allowed set is now computed, not listed.
before = len(problems)
allowed = set(check_geometry.derived_distances().values())
for doc, body in text.items():
    for mm in re.finditer(r"(\d{3,4}\.\d) m", body):
        val = float(mm.group(1))
        if not any(abs(val - r) < 0.2 for r in allowed):
            fail(f"{doc} quotes {val} m, which check_geometry.py does not derive "
                 f"from the frozen geometry")
guard(before, f"every distance quoted in prose is one of the {len(allowed)} "
              f"check_geometry.py derives")

print("\n--- the plan and the gate")

# Round 4 finding 1. gate.sh is the only acceptance contract, so a scenario the
# plan calls essential and the gate never runs is a promise nothing keeps.
gate = read("scripts/gate.sh")
plan = text["stage-1/plan.md"]

# Round 6, the conclusion pass. Each week's gate is now a list of calls to
# chunk functions, so reading gate_wN's body alone would find no scenarios at
# all and every check below would pass on an empty string. Inline the chunks.
chunk_bodies = {m.group(1): m.group(2) for m in
                re.finditer(r"^(w\d_\w+)\(\)\s*\{(.*?)^\}", gate, re.M | re.S)}

# A scenario reached through a shell variable is still a scenario the gate
# runs. Without this the checks below read "${W1_SCENARIO}" and conclude the
# gate runs nothing, which is the wrong answer in the reassuring direction.
gate_vars = dict(re.findall(r'^(\w+)="([^"$]+)"$', gate, re.M))


def expand(text: str) -> str:
    for name, value in gate_vars.items():
        text = text.replace("${" + name + "}", value)
        text = re.sub(rf"\${name}\b", value, text)
    return text


chunk_bodies = {k: expand(v) for k, v in chunk_bodies.items()}

helpers = {m.group(1): m.group(2) for m in
           re.finditer(r"^(run_scenario|check_run|gate_test)\(\)\s*\{(.*?)^\}",
                       gate, re.M | re.S)}
for name, body in list(chunk_bodies.items()):
    # Helpers call helpers. Expand to a fixed point so a chunk that calls
    # prepare_w1_run also gains run_scenario's body for the contract checks.
    added = set()
    for _ in range(len(helpers)):
        before_body = chunk_bodies[name]
        for hname, hbody in helpers.items():
            if (re.search(r"(^|\s)" + hname + r"\s", chunk_bodies[name])
                    and hname not in added):
                chunk_bodies[name] += "\n" + expand(hbody)
                added.add(hname)
        if chunk_bodies[name] == before_body:
            break

gate_weeks = {}
for m in re.finditer(r"^gate_w(\d)\(\)\s*\{(.*?)^\}", gate, re.M | re.S):
    body = m.group(2)
    for name in re.findall(r"^\s*(w\d_\w+)\s*$", body, re.M):
        body += "\n" + chunk_bodies.get(name, "")
    gate_weeks[int(m.group(1))] = body

before = len(problems)
called = set()
for body in gate_weeks.values():
    called |= set(re.findall(r"^\s*(w\d_\w+)\s*$", body, re.M))
orphans = sorted(set(chunk_bodies) - called)
for c in orphans:
    fail(f"{c} is defined in gate.sh and no week gate calls it, so the work it "
         f"checks is optional however the plan describes it")
guard(before, f"all {len(chunk_bodies)} gate chunks are reached by a week gate")

# Every chunk must also be reachable on its own, or "run one chunk" is a
# promise the dispatch does not keep.
before = len(problems)
dispatched = set(re.findall(r"^\s*\d+\.\d+\)\s*echo\s+(w\d_\w+)", gate, re.M))
for c in sorted(set(chunk_bodies) - dispatched):
    fail(f"{c} has no chunk id in gate.sh, so it cannot be run on its own")
guard(before, f"all {len(chunk_bodies)} chunks have an id you can run alone")

# Round 8 found three standalone W1 chunks reading latest files made by an
# earlier chunk. They passed as part of a week and failed when invoked by id,
# which broke the plan's unit-of-work promise. Any chunk reading a latest
# artifact must launch a scenario itself or call a preparation helper.
before = len(problems)
for name, body in sorted(chunk_bodies.items()):
    if "latest." not in body and "latest-graph" not in body:
        continue
    if not any(call in body for call in
               ("run_scenario ", "run_scenario.sh", "prepare_w1_run")):
        fail(f"{name} reads a latest run artifact and launches no scenario, so "
             f"its chunk id depends on a previous chunk's leftover files")
guard(before, "every chunk that reads a latest artifact can produce it alone")
gate_scenarios = {w: set(re.findall(r"run_scenario\s+\"?scenarios/(\w+)\.yaml", body))
                  for w, body in gate_weeks.items()}

plan_weeks = {}
for m in re.finditer(r"^### W(\d),(.*?)(?=^### W\d,|^## )", plan, re.M | re.S):
    plan_weeks[int(m.group(1))] = m.group(2)

before = len(problems)
for week, body in sorted(plan_weeks.items()):
    promised = set(re.findall(r"scenarios/(\w+)\.yaml", body))
    run_here = gate_scenarios.get(week, set())
    run_earlier = set().union(*(gate_scenarios.get(w, set()) for w in range(1, week))) if week > 1 else set()
    for s in sorted(promised - run_here - run_earlier):
        fail(f"plan.md week {week} names scenarios/{s}.yaml, and gate.sh week "
             f"{week} never runs it. The gate is the contract; a scenario "
             f"outside it is optional however the plan describes it.")
guard(before, "every scenario the plan promises is run by the gate that owns it")

# And nothing frozen in the architecture is left unrun by any gate.
before = len(problems)
all_run = set().union(*gate_scenarios.values()) if gate_scenarios else set()
frozen = set(re.findall(r"^### `(\w+)\.yaml`", arch, re.M))
# harness_check proves the harness and is never evidence for a rubric row, so
# it is deliberately outside both the frozen geometry and final run list.
sys.path.insert(0, str(REPO / "scripts"))
from check_submission_const import HARNESS_RUNS               # noqa: E402
all_run -= set(HARNESS_RUNS)
for s in sorted(frozen - all_run):
    fail(f"architecture.md freezes scenarios/{s}.yaml and no gate ever runs it")
guard(before, f"all {len(frozen)} frozen scenarios are run by a gate")

# And the other direction, which is the one that was open. A gate can run a
# scenario nobody has written down, and then the only description of what it
# has to contain is a list of --require flags. queue_drain arrived in gate.sh
# and in REQUIRED_RUNS before it had geometry, and nothing here would have
# noticed.
before = len(problems)
for s in sorted(all_run - frozen):
    fail(f"gate.sh runs scenarios/{s}.yaml and architecture.md has no "
         f"### `{s}.yaml` section. A scenario with acceptance flags and no "
         f"frozen geometry cannot be implemented twice the same way.")
guard(before, f"all {len(all_run)} scenarios the gate runs have frozen geometry")

# The submission's required run list and the gate's have to be the same set,
# or the package asks for evidence no week produces, or accepts a week that produced
# less than it ran.
before = len(problems)
sys.path.insert(0, str(REPO / "scripts"))
from check_submission_const import REQUIRED_RUNS               # noqa: E402
missing = set(REQUIRED_RUNS) - all_run
extra = all_run - set(REQUIRED_RUNS)
for s in sorted(missing):
    fail(f"check_submission_const.REQUIRED_RUNS names {s} and no gate runs it")
for s in sorted(extra):
    fail(f"gate.sh runs {s} and the final package never asks for its record, so the run is "
         f"evidence for nothing")
guard(before, f"the {len(REQUIRED_RUNS)} runs the final package requires are exactly the ones "
              f"the gates produce")

# Round 6, the conclusion pass, and the check that would have caught W1 six
# rounds ago. The plan-to-gate check above compares scenario files only, so a
# deliverable that is not a scenario was invisible to it. W1 promises seven
# things and produces no scenario at all, which put the whole week outside
# every check in this file.
#
# The plan states its deliverables as chunk tables now, one row per chunk:
#
#     | `1.3` | the run record writer, and `scripts/validate_record.py` | ... |
#
# Every row must name a chunk the gate dispatches, and every module or script
# in that row must appear in that chunk's function. A row nothing enforces is
# a promise, and the gate is the only contract in this project.
before = len(problems)
rows = re.findall(r"^\|\s*`(\d\.\d)`\s*\|([^|]*)\|", plan, re.M)
if len(rows) < 20:
    fail(f"plan.md lists {len(rows)} chunk rows and gate.sh dispatches "
         f"{len(chunk_bodies)}. If the plan stopped stating deliverables as "
         f"chunk rows, this check silently stopped checking anything.")
dispatch = dict(re.findall(r"^\s*(\d+\.\d+)\)\s*echo\s+(w\d_\w+)", gate, re.M))
for chunk_id, produces in rows:
    fn = dispatch.get(chunk_id)
    if not fn:
        fail(f"plan.md names chunk {chunk_id} and gate.sh dispatches no such "
             f"chunk, so nothing runs it")
        continue
    body = chunk_bodies.get(fn, "")
    names = [n for n in re.findall(r"`([A-Za-z0-9_./]+)`", produces)
             if "/" in n or n.endswith((".sh", ".py", ".yaml", ".json"))
             or n.startswith("uavx_")]
    for name in names:
        leaf = name.split("/")[-1]
        stem = leaf.removesuffix(".yaml")
        # A package name covers the node inside it: uavx_sim/scenario_runner is
        # gated by building and testing uavx_sim.
        pkg = name.split("/")[0]
        if not any(x in body for x in (leaf, stem, name, pkg)):
            fail(f"plan.md chunk {chunk_id} promises {name} and {fn}() in "
                 f"gate.sh never mentions it")
for chunk_id in sorted(dispatch):
    if chunk_id not in {r[0] for r in rows}:
        fail(f"gate.sh dispatches chunk {chunk_id} and plan.md has no row for "
             f"it, so the work it checks is undocumented")
guard(before, f"all {len(rows)} chunk rows in plan.md match a gate chunk that "
              f"names the same artifacts")

# Round 8 found chunk 1.3 invoking run_scenario.sh before chunk 1.4 produced
# it. The early call also needed graph and resource fields assigned to still
# later chunks. Hold the executable itself to the dependency order so this
# exact cycle cannot return under a different function name.
before = len(problems)
runner_rows = [chunk_id for chunk_id, produces in rows
               if "scripts/run_scenario.sh" in produces]
if len(runner_rows) != 1:
    fail(f"plan.md must assign scripts/run_scenario.sh to exactly one chunk, "
         f"found {runner_rows}")
else:
    owner = tuple(int(part) for part in runner_rows[0].split("."))
    for chunk_id, fn in dispatch.items():
        current = tuple(int(part) for part in chunk_id.split("."))
        if current < owner and "run_scenario.sh" in chunk_bodies.get(fn, ""):
            fail(f"chunk {chunk_id} calls scripts/run_scenario.sh before chunk "
                 f"{runner_rows[0]} produces it")
guard(before, "no chunk calls the scenario runner before its producing chunk")

# Round 8 resolved the W1 contract tests against ${UAVX_WS_SRC} while every
# other consumer, gate_build, check_seam.sh and standing rule 9, uses
# ${UAVX_WS_SRC}/src. Four chunks could not have found a correct
# implementation. One source root, or the gate lies about where code lives.
before = len(problems)
for m in re.finditer(r"\$\{UAVX_WS_SRC\}/(\S+)", gate):
    if not m.group(1).startswith("src"):
        fail(f"gate.sh resolves {m.group(0)} directly under the workspace. Our "
             f"packages live in ${{UAVX_WS_SRC}}/src, so this path is one "
             f"directory too high; use ${{UAVX_PKG_ROOT}}.")
guard(before, "every package path in the gate goes through the one source root")

# Round 7 finding 10: plan.md said the W4 fallback is link_loss with the
# release rule disabled and decisions.md said a deterministic priority list
# fixed at startup. Those are different systems supporting different claims,
# and the week-agent cannot ask which one is meant. One sentence, three files.
before = len(problems)
FALLBACK = "run `link_loss` with the release rule disabled"
loop = (REPO / ".claude" / "weekly-loop.md").read_text(encoding="utf-8")
decisions = (REPO / "stage-1" / "decisions.md").read_text(encoding="utf-8")
for name, body in (("stage-1/plan.md", plan),
                   ("stage-1/decisions.md", decisions),
                   (".claude/weekly-loop.md", loop)):
    if FALLBACK.lower() not in body.lower():
        fail(f"{name} does not state the W4 fallback. All three carry it "
             f"verbatim, because an unattended tick implements whichever one "
             f"it reads.")
if "deterministic priority list" in decisions.lower():
    fail("stage-1/decisions.md still names the old fallback, a deterministic "
         "priority list. Two fallbacks is the same as none.")
guard(before, "the W4 fallback is stated once, in all three documents")

# And the dates, which round 7 finding 9 found stale in decisions.md.
before = len(problems)
for want in ("5 Sep", "12 Sep", "19 Sep", "26 Sep"):
    if want not in decisions:
        fail(f"stage-1/decisions.md has no gate dated {want}. The four week "
             f"dates live in plan.md and the gate table has to match them.")
guard(before, "the decision gate dates match the four active weeks")

# Artifacts the plan promises must be checked by something the gate calls.
before = len(problems)
gate_reach = gate
for rel in sorted(set(re.findall(r"scripts/([\w.-]+\.(?:py|sh))", gate))):
    gate_reach += read(f"scripts/{rel}")
for m in sorted(set(re.findall(r"`?submission/([\w.-]+)`?", plan))):
    if m not in gate_reach:
        fail(f"plan.md promises submission/{m}, and nothing gate.sh runs ever "
             f"looks for it")
guard(before, "every submission artifact the plan names is checked by the gate")

print()
if problems:
    print(f"FAILED: {len(problems)} inconsistency(ies)")
    sys.exit(1)
print("documents agree with each other and with the gate")
sys.exit(0)
