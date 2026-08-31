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

import hashlib
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

# Round 6 finding 4: "five strings in steps_run" and "five [exit 0] lines" were
# the whole test, so a typed receipt beside a typed transcript passed. These
# are the exact labels rehearse_install.sh runs, read from the same file the
# wrapper reads, and every one of them has to appear in the transcript with a
# zero exit beside it.
STEPS = json.loads((REPO / "scripts" / "rehearsal-steps.json")
                   .read_text(encoding="utf-8"))["install"]

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path):
    if not path.is_file():
        fail(f"{path.relative_to(REPO).as_posix()} missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{path.name}: {exc}")
        return None


def submission_path(value, label: str):
    """Resolve a receipt path and keep it inside submission/."""
    if not isinstance(value, str) or not value:
        fail(f"the recording receipt names no {label}")
        return None
    rel = value.replace("\\", "/")
    if Path(rel).is_absolute() or rel.startswith("/") or ".." in Path(rel).parts:
        fail(f"the recording receipt's {label} path {value!r} escapes the "
             f"submission directory")
        return None
    path = (REPO / rel).resolve()
    if SUB.resolve() not in path.parents:
        fail(f"the recording receipt's {label} path {value!r} is not under "
             f"submission/")
        return None
    return path


def check_freshness(receipt: dict, label: str, current: str) -> bool:
    got = receipt.get("source_tree_sha256")
    if got != current:
        fail(f"the {label} rehearsal ran against source {str(got)[:12]}, the "
             f"tree is now {current[:12]}. A rehearsal of code that has since "
             f"changed rehearsed nothing.")
        return False
    return True


def check_recording_run(r: dict) -> bool:
    """Open the run the clip claims to come from.

    Round 6 finding 4: the receipt named a run id and a scenario, and nothing
    ever opened the record they pointed at. Two strings that are merely
    non-empty is what a hand-written receipt looks like.
    """
    rel = r.get("run_record")
    if not rel:
        fail("the recording receipt names no run record. A run id with no "
             "record behind it is a string.")
        return False
    path = submission_path(rel, "run record")
    if path is None:
        return False
    if not path.is_file():
        fail(f"the recording receipt points at {rel}, which is not there")
        return False
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"{rel}: {exc}")
        return False

    if rec.get("run_id") != r.get("run_id"):
        fail(f"the receipt is for run {r.get('run_id')} and the record it "
             f"names is run {rec.get('run_id')}. The clip and the record are "
             f"different runs.")
        return False
    want = f"scenarios/{Path(r['scenario']).stem}.yaml"
    if rec.get("scenario_path") not in (r.get("scenario"), want):
        fail(f"the receipt says the clip is of {r.get('scenario')} and the "
             f"record is a run of {rec.get('scenario_path')}")
        return False
    if rec.get("source_tree_sha256") != r.get("source_tree_sha256"):
        fail("the clip's receipt and the run record it names disagree about "
             "which source tree produced them")
        return False
    if rec.get("completion") != "complete":
        fail(f"the clip is of a run that ended {rec.get('completion')}. A "
             f"recording of a crashed scenario rehearses the capture path and "
             f"nothing else.")
        return False
    graph = submission_path(r.get("graph_snapshot"), "graph snapshot")
    if graph is None:
        return False
    if not graph.is_file():
        fail(f"the recording receipt points at {r.get('graph_snapshot')}, "
             f"which is not there")
        return False
    graph_sha = sha256_file(graph)
    if graph_sha != r.get("graph_snapshot_sha256"):
        fail("the kept graph snapshot does not hash to what the recording "
             "receipt says")
        return False
    if rec.get("graph_snapshot_sha256") != r.get("graph_snapshot_sha256"):
        fail("the receipt and the run record name different graph snapshots")
        return False
    # The overlay is what makes an unrelated clip impossible to substitute.
    if r.get("overlay_text") != r.get("run_id"):
        fail(f"the receipt says the frame carries {r.get('overlay_text')!r} "
             f"and the run is {r.get('run_id')!r}. The overlay is the only "
             f"thing tying the picture to the run.")
        return False
    return True


def main() -> int:
    current = digest(from_worktree())
    print(f"  source tree {current[:12]}")

    print("\n--- rebuild rehearsal")
    r = load(SUB / "dryrun-install-receipt.json")
    if r is not None:
        # Round 5 finding 6: this used to accept any JSON claiming result=pass
        # with five strings in steps_run. It never ran an installer, never read
        # an exit code, never checked a target existed. The receipt is now an
        # artifact of scripts/rehearse_install.sh, which cannot write one unless
        # every step exited 0, and the transcript it kept is rehashed here.
        if r.get("result") != "pass":
            fail(f"rehearsal recorded result={r.get('result')}")
        elif r.get("kind") != "rebuild-rehearsal":
            fail(f"receipt kind is {r.get('kind')!r}. Only "
                 f"scripts/rehearse_install.sh writes this file, and it stamps "
                 f"what the rehearsal actually was. Calling a receipt-only "
                 f"check a fresh install is how W5 inherits the risk anyway.")
        elif check_freshness(r, "rebuild", current):
            tpath = REPO / (r.get("transcript") or "")
            steps = r.get("steps_run") or []
            if not r.get("transcript") or not tpath.is_file():
                fail(f"the receipt names transcript {r.get('transcript')!r}, "
                     f"which is not there. The transcript is the evidence; the "
                     f"receipt is only the claim.")
            elif sha256_file(tpath) != r.get("transcript_sha256"):
                fail("the transcript does not hash to what the receipt says. "
                     "One of them has been edited since the rehearsal ran.")
            elif not r.get("target"):
                fail("the receipt records no build target, so nothing says "
                     "where this was rebuilt")
            elif not Path(r["target"]).is_dir():
                # Round 6 finding 4. A receipt naming a prefix that is not
                # there describes a rebuild whose output nobody can look at.
                fail(f"the receipt says it rebuilt into {r['target']}, which "
                     f"does not exist. Either the rehearsal never ran or its "
                     f"output is gone, and neither is a rehearsal W3 can be "
                     f"accepted on.")
            elif steps != STEPS:
                fail(f"steps_run is not what rehearse_install.sh runs. Wanted "
                     f"{STEPS}, got {steps}. Five strings of any kind used to "
                     f"be enough.")
            else:
                body = tpath.read_text(encoding="utf-8", errors="replace")
                bad = [ln for ln in body.splitlines()
                       if ln.startswith("[exit ") and ln.strip() != "[exit 0]"]
                # Each label has to appear as its own transcript section, with
                # a zero exit after it and before the next section starts.
                missing = []
                for label in STEPS:
                    head = f"=== {label}"
                    at = body.find(head)
                    if at < 0:
                        missing.append(label)
                        continue
                    rest = body[at + len(head):]
                    nxt = rest.find("\n=== ")
                    section = rest if nxt < 0 else rest[:nxt]
                    if "[exit 0]" not in section:
                        missing.append(label)
                if bad:
                    fail(f"the transcript records {len(bad)} non-zero exit(s) "
                         f"while the receipt says pass: {bad[:3]}")
                elif missing:
                    fail(f"the transcript has no completed section for "
                         f"{missing}. The receipt claims these ran; the "
                         f"transcript is where they would have said so.")
                else:
                    ok(f"rebuilt {r.get('done')} into {r.get('target')}, "
                       f"all {len(STEPS)} steps present in the transcript")

    print("\n--- recording rehearsal")
    r = load(SUB / "dryrun-recording-receipt.json")
    if r is not None:
        if r.get("kind") != "recording-rehearsal":
            fail(f"receipt kind is {r.get('kind')!r}. Only "
                 f"scripts/rehearse_recording.sh writes this file.")
            r = None
        elif r.get("result") != "pass":
            fail(f"recording rehearsal recorded result={r.get('result')}")
            r = None
    if r is not None:
        if not check_freshness(r, "recording", current):
            pass
        else:
            clip = REPO / (r.get("clip") or "")
            if not r.get("clip") or not clip.is_file():
                fail(f"recording rehearsal names clip {r.get('clip')!r}, which "
                     f"is not there. The receipt is the claim; the file is the "
                     f"evidence.")
            elif sha256_file(clip) != r.get("clip_sha256"):
                fail("the clip does not hash to what the receipt says. Any "
                     "decodable video satisfied the old check; this one has to "
                     "be the video the rehearsal recorded.")
            elif not r.get("run_id") or not r.get("scenario"):
                fail("the recording receipt names no run id and scenario, so "
                     "the clip is not bound to anything that ran")
            elif not check_recording_run(r):
                pass
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
