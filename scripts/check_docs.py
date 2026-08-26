#!/usr/bin/env python3
"""Catch documents that contradict each other.

Round 3 finding 10: a week-agent was being handed files that told it both to use
and never to use the launcher that crashes the distro, and both that the
environment existed and that nothing was installed. Those statements sat in the
same file, the correct one first. An agent has no way to know which wins.

Prose drifts because nothing checks it. This does.

    python3 scripts/check_docs.py

Exit 0 if the documents agree.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS = ["handoff.md", "context.md", "stage-1/plan.md", "stage-1/decisions.md",
        "stage-1/architecture.md", ".claude/weekly-loop.md",
        "stage-1/setup/README.md"]

problems: list[str] = []


def read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


text = {d: read(d) for d in DOCS}
allmd = "\n".join(text.values())

print("--- banned claims")

# The launcher. Using PX4's own script crashes the distro, so no document may
# recommend it except to say not to.
for doc, body in text.items():
    for m in re.finditer(r"^.*sitl_multiple_run\.sh.*$", body, re.M):
        line = m.group(0)
        if not re.search(r"do not use|never|ignores|instead of|rather than|crashes|not by", line, re.I):
            fail(f"{doc} mentions sitl_multiple_run.sh without saying not to use it: {line.strip()[:90]}")
if not problems:
    ok("no document recommends PX4's own multi-vehicle launcher")

# Thresholds live in gate.sh alone. Round 2 finding 2 was exactly this drift.
before = len(problems)
for doc, body in text.items():
    for m in re.finditer(r"(coverage_fraction|delivery_ratio\w*|time_to_reconnect_s|separation_violations|role_changes)\s*(>=|<=|==|<|>)\s*[0-9.]+", body):
        fail(f"{doc} restates a gate threshold: {m.group(0)}. Thresholds live only in scripts/gate.sh.")
if len(problems) == before:
    ok("no document restates a gate threshold")

# Superseded state.
before = len(problems)
STALE = [
    (r"[Nn]othing runs yet", "claims nothing runs, but the environment is up"),
    (r"[Nn]othing is installed", "claims nothing is installed"),
    (r"only distro is `?docker-desktop", "claims the only WSL distro is docker-desktop"),
    (r"WSLg has never produced a display", "claims WSLg gives no display, but DISPLAY is set"),
    (r"[Rr]ound 2 .{0,40}has not (run|happened)", "says round 2 has not run"),
    (r"[Rr]ound 1 .{0,40}has not (run|happened)", "says round 1 has not run"),
]
for doc, body in text.items():
    if doc.startswith("_plan-review"):
        continue
    for rx, why in STALE:
        if re.search(rx, body):
            fail(f"{doc} {why}")
if len(problems) == before:
    ok("no document carries superseded state")

print("\n--- agreement across documents")

# Vehicle count is fixed at 4 and the recovery topology depends on it.
before = len(problems)
for doc, body in text.items():
    for m in re.finditer(r"^.*(drop|cut|reduce|fall back).{0,30}(to )?(2|two|3|three) vehicles.*$", body, re.I | re.M):
        line = m.group(0)
        # "do not drop to 2 vehicles" is the correct statement, not a violation.
        # Checking for the phrase without checking for its negation is the same
        # mistake the launcher rule above already had to account for.
        if re.search(r"\b(do not|don't|never|cannot|not)\b", line, re.I):
            continue
        fail(f"{doc} offers a reduced vehicle count, but D4 fixes it at 4 and fewer cannot hold anchor, relay and two surveyors: {line.strip()[:80]}")
if len(problems) == before:
    ok("vehicle count of 4 is not contradicted anywhere")

# W5 is four days, 23 to 26 September.
before = len(problems)
for doc, body in text.items():
    if re.search(r"(5|five) days.{0,40}(proposal|video|W5)", body, re.I):
        fail(f"{doc} says W5 has five days; it has four, 23 to 26 September")
if len(problems) == before:
    ok("W5 is described as four days everywhere")

# The radio parameters in architecture.md must match check_geometry.py, which is
# what actually proves the topology.
geo = read("scripts/check_geometry.py")
arch = text["stage-1/architecture.md"]
for name, pat_py in (("r_full", r"R_FULL\s*=\s*([0-9.]+)"), ("r_max", r"R_MAX\s*=\s*([0-9.]+)")):
    m = re.search(pat_py, geo)
    if not m:
        fail(f"check_geometry.py has no {name}")
        continue
    val = int(float(m.group(1)))
    if re.search(rf"`{name}`\s*\|\s*{val} m", arch):
        ok(f"{name} = {val} m agrees between architecture.md and check_geometry.py")
    else:
        fail(f"architecture.md does not state {name} = {val} m, which is what check_geometry.py enforces")

# Any distance quoted in prose must match the frozen positions. Round 3 finding
# 1 survived partly because stale metres sat unchallenged in three documents.
before = len(problems)
import ast as _ast
m = re.search(r"START\s*=\s*\{(.+?)\n\}", geo, re.S)
if m:
    pos = _ast.literal_eval("{" + m.group(1) + "}")
    import math as _math
    real = set()
    for a in pos:
        for b in pos:
            if a < b:
                real.add(round(_math.dist(pos[a], pos[b]), 1))
    for doc, body in text.items():
        for mm in re.finditer(r"(\d{3,4}\.\d) m", body):
            val = float(mm.group(1))
            if val not in real and not any(abs(val - r) < 0.2 for r in real):
                # Derived figures that are legitimately not pair distances:
                # the relay slot hops, the mover's flight, and the integrated
                # mission's worst-corner margins.
                if val not in (160.2, 191.6, 262.7):
                    fail(f"{doc} quotes {val} m, which is not a distance in the frozen topology")
if len(problems) == before:
    ok("every distance quoted in prose exists in the frozen topology")

print()
if problems:
    print(f"FAILED: {len(problems)} inconsistency(ies)")
    sys.exit(1)
print("documents agree")
sys.exit(0)
