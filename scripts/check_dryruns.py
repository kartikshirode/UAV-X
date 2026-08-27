#!/usr/bin/env python3
"""The two W3 rehearsals, checked rather than promised.

stage-1/plan.md says a fresh-install dry run and a 60 second recording dry run
both happen in W3, "so the week cannot be accepted without them and hand the
bill to W5". Round 4 finding 1: `gate_w3` contained neither, and since gate.sh
is the only acceptance contract, W3 could go green having done neither and
handed both to the four days that have no room for them.

The recording one is the sharper risk. The `gazebo` GUI binary takes this WSL
distro down, and it has done it three times. If the video path turns out not to
work, that is a week's problem in W3 and a submission's problem in W5.

Both receipts are tied to the current source tree, so a rehearsal from three
weeks and two rewrites ago does not count as a rehearsal of this code.

    python3 scripts/check_dryruns.py

Exit 0 if both rehearsals happened against this source.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_tree_hash import digest, from_worktree          # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SUB = REPO / "submission"
MIN_RECORDING_S = 55.0

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def load(path: Path):
    if not path.is_file():
        fail(f"{path.relative_to(REPO).as_posix()} missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path.name}: {exc}")
        return None


def check_freshness(receipt: dict, label: str, current: str) -> bool:
    got = receipt.get("source_tree_sha256")
    if got != current:
        fail(f"the {label} rehearsal ran against source {str(got)[:12]}, the "
             f"tree is now {current[:12]}. A rehearsal of code that has since "
             f"changed rehearsed nothing.")
        return False
    return True


def main() -> int:
    current = digest(from_worktree())
    print(f"  source tree {current[:12]}")

    print("\n--- fresh install rehearsal")
    r = load(SUB / "dryrun-install-receipt.json")
    if r is not None:
        if r.get("result") != "pass":
            fail(f"install rehearsal recorded result={r.get('result')}")
        elif check_freshness(r, "install", current):
            steps = r.get("steps_run") or []
            if len(steps) < 5:
                fail(f"install rehearsal ran {len(steps)} setup steps; there "
                     f"are five and a partial run proves less than none")
            else:
                ok(f"fresh install rehearsed {r.get('done')}, {len(steps)} steps, "
                   f"{r.get('minutes', '?')} minutes")

    print("\n--- recording rehearsal")
    r = load(SUB / "dryrun-recording-receipt.json")
    if r is not None:
        if not check_freshness(r, "recording", current):
            pass
        else:
            clip = REPO / (r.get("clip") or "")
            if not r.get("clip") or not clip.is_file():
                fail(f"recording rehearsal names clip {r.get('clip')!r}, which "
                     f"is not there. The receipt is the claim; the file is the "
                     f"evidence.")
            elif not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
                fail("ffmpeg is not installed, so the clip cannot be verified. "
                     "apt install ffmpeg")
            else:
                try:
                    dur = float(subprocess.run(
                        ["ffprobe", "-v", "error", "-show_entries",
                         "format=duration", "-of", "default=nw=1:nk=1", str(clip)],
                        capture_output=True, text=True, timeout=120).stdout.strip())
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    dur = -1.0
                    fail(f"ffprobe could not read the rehearsal clip: {exc}")

                if dur >= 0:
                    if dur < MIN_RECORDING_S:
                        fail(f"rehearsal clip is {dur:.0f}s; the plan asks for 60, "
                             f"and a short clip does not prove a long capture holds up")
                    else:
                        proc = subprocess.run(
                            ["ffmpeg", "-v", "error", "-i", str(clip), "-f",
                             "null", "-"], capture_output=True, text=True,
                            timeout=600)
                        if proc.returncode != 0 or proc.stderr.strip():
                            fail(f"rehearsal clip does not decode: "
                                 f"{proc.stderr.strip()[:160]}")
                        else:
                            ok(f"{dur:.0f}s clip captured by {r.get('method', '?')}, "
                               f"decodes end to end")

    print()
    if problems:
        print(f"FAILED: {len(problems)} rehearsal problem(s)")
        print("\nBoth of these belong to W3 on purpose. W5 is four days and has")
        print("no room to discover that the install or the screen capture does")
        print("not work. See stage-1/plan.md, week 3.")
        return 1
    print("both W3 rehearsals happened against the current source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
