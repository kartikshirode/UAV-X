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

# W5 is four days, 23 to 26 September.
before = len(problems)
for doc, body in text.items():
    if re.search(r"(5|five) days.{0,40}(proposal|video|W5)", body, re.I):
        fail(f"{doc} says W5 has five days; it has four, 23 to 26 September")
guard(before, "W5 is described as four days everywhere")

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

gate_weeks = {}
for m in re.finditer(r"^gate_w(\d)\(\)\s*\{(.*?)^\}", gate, re.M | re.S):
    gate_weeks[int(m.group(1))] = m.group(2)
gate_scenarios = {w: set(re.findall(r"run_scenario\s+scenarios/(\w+)\.yaml", body))
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
for s in sorted(frozen - all_run):
    fail(f"architecture.md freezes scenarios/{s}.yaml and no gate ever runs it")
guard(before, f"all {len(frozen)} frozen scenarios are run by a gate")

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
