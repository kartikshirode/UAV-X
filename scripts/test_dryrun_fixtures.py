#!/usr/bin/env python3
"""Prove check_dryruns.py rejects a rehearsal that did not happen.

Round 5 finding 6 made the two wrappers write the receipts. Round 6 finding 4
found what was still missing: the checker read the receipt and the transcript
and never read anything either of them pointed at. Five strings of any kind in
steps_run, five [exit 0] lines anywhere in a file, a target that need not
exist, and a run id with no run behind it all passed.

So this suite builds a rehearsal pair the checker accepts, then breaks one
thing at a time. Same discipline as the submission suite: a positive oracle
first, because a mutation from a broken baseline measures nothing.

    python3 scripts/test_dryrun_fixtures.py

Exit 0 if the baseline passes and every mutation is caught for its own reason.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_dryruns.py"
STEPS = json.loads((REPO / "scripts" / "rehearsal-steps.json")
                   .read_text(encoding="utf-8"))["install"]

CASES: list = []


def case(name: str, expect: str):
    def deco(fn):
        CASES.append((name, expect, fn))
        return fn
    return deco


def source_hash() -> str:
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "source_tree_hash.py")],
                         capture_output=True, text=True, cwd=str(REPO))
    return out.stdout.strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(sub: Path, target: Path, clip_src: Path) -> None:
    """A rehearsal pair the checker has no complaint about."""
    src = source_hash()
    target.mkdir(parents=True, exist_ok=True)

    transcript = sub / "dryrun-install-transcript.log"
    body = [f"target={target}", "started=2026-09-15T09:00:00Z",
            f"source_tree_sha256={src}", ""]
    for label in STEPS:
        body += ["", f"=== {label}", "$ some command", "output", "[exit 0]"]
    transcript.write_text("\n".join(body) + "\n", encoding="utf-8")

    (sub / "dryrun-install-receipt.json").write_text(json.dumps({
        "kind": "rebuild-rehearsal",
        "result": "pass",
        "target": str(target),
        "started": "2026-09-15T09:00:00Z",
        "done": "2026-09-15",
        "ended": "2026-09-15T09:40:00Z",
        "source_tree_sha256": src,
        "transcript": "submission/dryrun-install-transcript.log",
        "transcript_sha256": sha256_file(transcript),
        "steps_run": list(STEPS),
    }, indent=2), encoding="utf-8")

    clip = sub / "dryrun-recording.mp4"
    shutil.copyfile(clip_src, clip)
    run_id = "rehearsal_relay_required_20260915T091500Z"
    graph_sha = hashlib.sha256(b"graph").hexdigest()

    (sub / "dryrun-recording-run.jsonl").write_text(json.dumps({
        "run_id": run_id,
        "scenario_path": "scenarios/relay_required.yaml",
        "source_tree_sha256": src,
        "completion": "complete",
        "graph_snapshot_sha256": graph_sha,
    }), encoding="utf-8")

    (sub / "dryrun-recording-receipt.json").write_text(json.dumps({
        "kind": "recording-rehearsal",
        "result": "pass",
        "method": "headless gzserver capture via run_scenario.sh --record",
        "clip": "submission/dryrun-recording.mp4",
        "clip_sha256": sha256_file(clip),
        "duration_s": 60.0,
        "run_id": run_id,
        "run_record": "submission/dryrun-recording-run.jsonl",
        "graph_snapshot_sha256": graph_sha,
        "overlay_text": run_id,
        "scenario": "scenarios/relay_required.yaml",
        "source_tree_sha256": src,
        "done": "2026-09-15",
        "ended": "2026-09-15T09:16:00Z",
    }, indent=2), encoding="utf-8")


def load(sub: Path, name: str) -> dict:
    return json.loads((sub / name).read_text(encoding="utf-8"))


def save(sub: Path, name: str, data: dict) -> None:
    (sub / name).write_text(json.dumps(data, indent=2), encoding="utf-8")


@case("a transcript nobody produced, with the right shape",
      "no completed section")
def _typed_transcript(sub, target):
    t = sub / "dryrun-install-transcript.log"
    t.write_text("looks about right\n" + "[exit 0]\n" * 5, encoding="utf-8")
    r = load(sub, "dryrun-install-receipt.json")
    r["transcript_sha256"] = sha256_file(t)
    save(sub, "dryrun-install-receipt.json", r)


@case("five steps that are not the five the wrapper runs",
      "not what rehearse_install.sh runs")
def _wrong_steps(sub, target):
    r = load(sub, "dryrun-install-receipt.json")
    r["steps_run"] = ["one", "two", "three", "four", "five"]
    save(sub, "dryrun-install-receipt.json", r)


@case("a build target that is not there", "does not exist")
def _absent_target(sub, target):
    shutil.rmtree(target)


@case("a receipt naming a run record that does not exist", "is not there")
def _no_record(sub, target):
    (sub / "dryrun-recording-run.jsonl").unlink()


@case("a clip bound to one run and a record from another",
      "different runs")
def _mismatched_run(sub, target):
    rec = load(sub, "dryrun-recording-run.jsonl")
    rec["run_id"] = "some_other_run"
    save(sub, "dryrun-recording-run.jsonl", rec)


@case("a clip copied from a run of another scenario", "is a run of")
def _other_scenario(sub, target):
    rec = load(sub, "dryrun-recording-run.jsonl")
    rec["scenario_path"] = "scenarios/direct_only.yaml"
    save(sub, "dryrun-recording-run.jsonl", rec)


@case("a clip whose frames carry somebody else's run id",
      "only thing tying the picture to the run")
def _wrong_overlay(sub, target):
    r = load(sub, "dryrun-recording-receipt.json")
    r["overlay_text"] = "rehearsal_from_last_week"
    save(sub, "dryrun-recording-receipt.json", r)


@case("a recording of a run that crashed", "ended crashed")
def _crashed(sub, target):
    rec = load(sub, "dryrun-recording-run.jsonl")
    rec["completion"] = "crashed"
    save(sub, "dryrun-recording-run.jsonl", rec)


@case("a hand-written receipt with no wrapper behind it", "receipt kind is")
def _typed_receipt(sub, target):
    save(sub, "dryrun-recording-receipt.json", {
        "result": "pass", "clip": "submission/dryrun-recording.mp4",
        "run_id": "whatever", "scenario": "scenarios/relay_required.yaml"})


def run_checker(sub: Path) -> tuple:
    env = dict(os.environ)
    p = subprocess.run([sys.executable, str(CHECKER)], capture_output=True,
                       text=True, cwd=str(REPO), env=env)
    return p.returncode, p.stdout + p.stderr


def make_clip(dest: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc=size=320x240:rate=10:duration=60", "-pix_fmt", "yuv420p",
         str(dest)], capture_output=True, text=True, timeout=600)
    return r.returncode == 0 and dest.is_file()


def main() -> int:
    sub = REPO / "submission"
    existing = sub.is_dir()
    keep = None
    if existing:
        keep = Path(tempfile.mkdtemp(prefix="uavx-sub-")) / "submission"
        shutil.copytree(sub, keep)

    tmp = Path(tempfile.mkdtemp(prefix="uavx-dryrun-"))
    clip_src = tmp / "clip.mp4"
    if not make_clip(clip_src):
        print("  FAIL  ffmpeg is not available, so no valid baseline can be "
              "built. A suite that skips its own oracle is the bug this file "
              "exists to stop.")
        return 1

    failures = 0
    try:
        print("--- the baseline must be a rehearsal pair the checker accepts")
        sub.mkdir(exist_ok=True)
        for f in ("dryrun-install-receipt.json", "dryrun-install-transcript.log",
                  "dryrun-recording-receipt.json", "dryrun-recording-run.jsonl",
                  "dryrun-recording.mp4"):
            (sub / f).unlink(missing_ok=True)
        target = tmp / "prefix"
        build(sub, target, clip_src)
        rc, out = run_checker(sub)
        if rc == 0:
            print("  ok    a rehearsal that happened                      rc=0")
        else:
            failures += 1
            print(f"  FAIL  a rehearsal that happened                      rc={rc}")
            for line in out.strip().splitlines()[:10]:
                print(f"           {line}")
            print("\nFAILED: the baseline is not valid, so no case below can "
                  "distinguish\n        its own mutation from the broken "
                  "baseline.")
            return 1

        print("\n--- each mutation, from the passing baseline")
        for name, expect, mutate in CASES:
            for f in ("dryrun-install-receipt.json",
                      "dryrun-install-transcript.log",
                      "dryrun-recording-receipt.json",
                      "dryrun-recording-run.jsonl", "dryrun-recording.mp4"):
                (sub / f).unlink(missing_ok=True)
            if target.is_dir():
                shutil.rmtree(target)
            build(sub, target, clip_src)
            mutate(sub, target)
            rc, out = run_checker(sub)
            if rc == 0:
                failures += 1
                print(f"  FAIL  {name:<52} rc=0, accepted")
            elif expect not in out:
                failures += 1
                print(f"  FAIL  {name:<52} rc={rc}")
                print(f"           never said {expect!r}")
                for line in out.strip().splitlines()[:6]:
                    print(f"           {line}")
            else:
                print(f"  ok    {name:<52} rc={rc}")
    finally:
        shutil.rmtree(sub, ignore_errors=True)
        if keep is not None:
            shutil.copytree(keep, sub)
            shutil.rmtree(keep.parent, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED: {failures} of {len(CASES) + 1} rehearsal checks "
              f"behaved wrongly")
        return 1
    print(f"all {len(CASES) + 1} rehearsal checks behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
