#!/usr/bin/env python3
"""Verify the Stage 1 submission package is real, not just present.

Round 2 finding 3: the planned checker tested existence, a page count, a video
duration and a checklist that claimed the files were there. A blank seven-page
PDF, a corrupt three-second MP4, an archive missing half the source and a
hand-written checklist all satisfied that contract.

Round 4 came back for the two places that were still taking a claim at face
value.

Finding 5: the archive was never hashed. `hashlib` was imported and unused. The
checker read `archive_sha256` out of the manifest and confirmed the install
receipt repeated the same string, so two invented matching strings passed while
`uavx-source.zip` held anything at all. Now the file on disk is hashed, and
every entry in it is checked against the frozen commit's own tree, so the
archive cannot contain source that commit never had.

Finding 6: run evidence was filename matching. Five substrings against
`runs/*.jsonl`. An empty file, a crashed run, a run of a different scenario or a
run off different source all counted, and the integrated mission and the safety
control were not on the list at all. Now the final package revalidates every run against the
schema and against `uavx_eval.check`, binds each to the archived source, and
requires the proposal to cite the run ids it is quoting numbers from.

    python3 scripts/check_submission.py

Exit 0 means the package is ready AND the send has been recorded. Exit 2 means
the package is good but the email has not gone yet, which is the normal state
the moment before a human sends it. Exit 1 means something is actually wrong.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonschema_mini import validate                      # noqa: E402
from source_tree_hash import blob_id                      # noqa: E402
import check_human_preflight                              # noqa: E402
# One definition, shared with scripts/test_submission_fixtures.py. Round 5
# finding 2: these lists were copied into the fixture by hand and drifted.
from check_submission_const import (PEAK_RSS_CEILING_MIB, REQUIRED_RUNS, REQUIRED_SECTIONS,
                                   REQUIRED_COMPLIANCE)  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# Overridable so scripts/test_submission_fixtures.py can point the whole checker
# at a tampered copy and prove it says no. A checker nobody has watched reject
# something is not evidence, and this repo has shipped two of those.
SUB = Path(os.environ.get("UAVX_SUBMISSION_DIR") or (REPO / "submission"))
# Where run records live. Overridable only so the fixture suite can build a
# package in a temp tree; chunk 4.8 uses the repository. Round 7 finding 3: evidence
# paths are resolved under this root and may not escape it.
RUNS_ROOT = Path(os.environ.get("UAVX_RUNS_ROOT") or REPO).resolve()
RUNS = Path(os.environ.get("UAVX_RUNS_DIR") or (REPO / "runs"))

# Techfest Stage 1, from context.md: a 6-8 page technical proposal with software
# architecture, a working proof-of-concept simulation, source code, installation
# instructions, and a demo video.
MIN_PAGES, MAX_PAGES = 6, 8
MAX_VIDEO_S = 180          # our assumption, not theirs; see organiser-email.md
MIN_VIDEO_S = 30
ORGANISER_EMAIL = "pushpak_gc2026@aero.iitb.ac.in"


# Every scenario whose numbers the proposal is allowed to quote. The integrated
# mission and the safety control are on it because round 4 finding 6 found the
# one run that proves the swarm, and the one run that gives the safety result
# its meaning, were both missing from the final run list.

# What the archive is allowed to hold, matching scripts/freeze_source.sh.
ARCHIVE_PATHS = ("uavx_ws/", "scenarios/", "scripts/", "stage-1/",
                 "INSTALL.md", "LICENSE", "THIRD-PARTY.md")
ARCHIVE_PREFIX = "uavx-source/"

FORBIDDEN_IN_ARCHIVE = [
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"(^|/)\.git/"),
    re.compile(r"\.(pem|key)$"),
    re.compile(r"(^|/)(build|install|log)/"),
    re.compile(r"__pycache__/"),
]

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def section(title: str) -> None:
    print(f"\n--- {title}")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"{path.name}: {exc}")
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------- human preflight
# The final package needs the delivery budget, and if the receipt is not valid there is no
# budget to check the package against.
section("human preflight")
human = None
try:
    check_human_preflight.RECEIPT = SUB / "human-preflight.json"
    human = check_human_preflight.load()
    errs = validate(human, load_json(SUB / "human-preflight.schema.json") or {})
    if errs:
        fail(f"human-preflight.json fails its schema: {errs[0]}")
        human = None
    else:
        ok(f"delivery budget {human['delivery']['attachment_limit_mb']} MB, "
           f"route: {human['delivery']['route']}")
        # Round 5 finding 8, third layer: an API diff does not read the channels
        # the rules say changes are announced on. Round 5 finding 9: a person
        # signs the legal analysis, because a substring checker cannot.
        today = date.today()
        for path, label, limit in (
                (("clarification_channel", "last_checked"), "the WhatsApp group", 14),
                (("organiser_email", "last_checked"), "the organiser reply inbox", 14),
                (("compliance_review", "done"), "the compliance sign-off", 60)):
            raw = human.get(path[0], {}).get(path[1])
            try:
                age = (today - date.fromisoformat(raw)).days
            except (TypeError, ValueError):
                fail(f"{label} has no usable date in human-preflight.json")
                continue
            if age > limit:
                fail(f"{label} was last read {age} days ago, over the {limit} "
                     f"day limit. The rules say changes are announced through "
                     f"official channels, so not reading them is not a defence.")
            else:
                ok(f"{label} read {age} day(s) ago")
        cr = human.get("compliance_review", {})
        if cr.get("by"):
            ok(f"compliance signed off by {cr['by']} on {cr.get('done')}")
except SystemExit:
    fail("submission/human-preflight.json missing or unreadable. "
         "See stage-1/human-preflight.md.")

# ---------------------------------------------------------------- proposal
section("proposal")
pdf = SUB / "proposal.pdf"
proposal_text = ""
if not pdf.is_file():
    fail("submission/proposal.pdf missing")
elif not have("pdftotext"):
    fail("pdftotext not installed, cannot read the PDF. apt install poppler-utils")
else:
    try:
        proposal_proc = subprocess.run(
            ["pdftotext", str(pdf), "-"], capture_output=True, text=True,
            timeout=120)
        proposal_text = proposal_proc.stdout
        if proposal_proc.returncode != 0:
            fail(f"pdftotext rejected proposal.pdf: "
                 f"{proposal_proc.stderr.strip()[:200] or 'no diagnostic'}")
    except (OSError, subprocess.SubprocessError) as exc:
        fail(f"pdftotext failed: {exc}")

    pages = proposal_text.count("\f")
    if MIN_PAGES <= pages <= MAX_PAGES:
        ok(f"page count {pages}")
    else:
        fail(f"page count {pages}, must be {MIN_PAGES} to {MAX_PAGES}")

    words = len(proposal_text.split())
    if words < 1500:
        fail(f"proposal has only {words} words. A 6 to 8 page document is not "
             f"that short; is it mostly images or empty?")
    else:
        ok(f"word count {words}")

    low = proposal_text.lower()
    missing = [s for s in REQUIRED_SECTIONS if s not in low]
    if missing:
        fail(f"proposal never mentions: {', '.join(missing)}")
    else:
        ok("covers architecture, communication, relay, fault, safety, results")

    # Round 5 finding 9: "regulat" appearing anywhere satisfied this, and a
    # sentence saying the system is unregulated contains it.
    absent = [(t, why) for t, why in REQUIRED_COMPLIANCE if t.lower() not in low]
    if absent:
        for token, why in absent:
            fail(f"the regulatory section never gives {token!r}, {why}")
    else:
        ok(f"regulatory section cites the rules, the amendments, an official "
           f"source and the authority, and says simulation only")

# ------------------------------------------------------------------- video
# Round 6 finding 3: this took the first of demo.mp4 and demo.mkv that existed
# and decoded it, while delivery asked only for one listed name beginning
# "demo.". Two ways through. A valid demo.mp4 on disk with demo.txt in the
# manifest, and a good MP4 decoded here while a corrupt MKV was the file
# actually sent. Both reach the submitted state with no checked video among the
# delivered bytes, which is round 5 finding 3 one file along.
#
# So: collect every candidate, require exactly one, decode that one, and later
# require that exact name, size and hash in the delivery manifest.
section("video")
# The one file that was decoded. Delivery has to send this exact file, and
# nothing else named demo.*.
checked_video = None
DEMO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v")
candidates = sorted(q for q in SUB.glob("demo.*") if q.is_file())
video_candidates = [q for q in candidates if q.suffix.lower() in DEMO_SUFFIXES]
other_demos = [q for q in candidates if q not in video_candidates]

video = None
if other_demos:
    fail(f"submission/ holds {', '.join(q.name for q in other_demos)} beside "
         f"the demo video. Anything called demo.* can be picked up by the "
         f"delivery manifest, and a demo.txt in the email is not a video.")
elif len(video_candidates) > 1:
    fail(f"submission/ holds {len(video_candidates)} demo videos "
         f"({', '.join(q.name for q in video_candidates)}). One is checked and "
         f"the other is not, and nothing says which one gets sent.")
elif not video_candidates:
    fail("submission/demo.mp4 missing")
else:
    video = video_candidates[0]

if video is None:
    pass
elif not have("ffprobe") or not have("ffmpeg"):
    fail("ffmpeg not installed, cannot verify the video decodes. apt install ffmpeg")
else:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)],
            capture_output=True, text=True, timeout=120)
        if probe.returncode != 0:
            raise ValueError(probe.stderr.strip() or "ffprobe returned non-zero")
        dur = float(probe.stdout.strip())
        if not math.isfinite(dur):
            raise ValueError(f"non-finite duration {probe.stdout.strip()!r}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        dur = -1.0
        fail(f"ffprobe could not read a duration: {exc}")

    if dur >= 0:
        if MIN_VIDEO_S <= dur <= MAX_VIDEO_S:
            ok(f"duration {dur:.0f}s")
        else:
            fail(f"duration {dur:.0f}s, expected {MIN_VIDEO_S} to {MAX_VIDEO_S}")

    # Decode the whole thing. A file with a valid header and a corrupt body
    # reports a duration perfectly well.
    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or proc.stderr.strip():
        fail(f"video does not decode cleanly: {proc.stderr.strip()[:200]}")
    else:
        ok(f"{video.name} decodes end to end with no errors")
        checked_video = video

# ------------------------------------------------------------------ install
section("installation instructions")
install = SUB / "INSTALL.md"
if not install.is_file():
    fail("submission/INSTALL.md missing")
else:
    body = install.read_text(encoding="utf-8", errors="replace")
    if len(body.split()) < 200:
        fail("INSTALL.md is too short to be instructions")
    else:
        ok(f"INSTALL.md, {len(body.split())} words")

# ------------------------------------------------------------- source freeze
section("source freeze")
manifest = load_json(SUB / "source-manifest.json") if (SUB / "source-manifest.json").is_file() else None
frozen_sha = archive_sha256 = source_tree_sha = None
archive = None

if manifest is None:
    fail("submission/source-manifest.json missing. Run scripts/freeze_source.sh.")
elif not isinstance(manifest, dict):
    # Round 8 found package-controlled JSON containers reaching .get calls
    # before their shape was checked. A malformed package must be rejected,
    # not terminate the checker with a traceback.
    fail("source-manifest.json must contain one JSON object")
else:
    frozen_sha = manifest.get("commit_sha")
    archive_sha256 = manifest.get("archive_sha256")
    source_tree_sha = manifest.get("source_tree_sha256")
    named = manifest.get("archive")

    if not frozen_sha or not re.fullmatch(r"[0-9a-f]{40}", str(frozen_sha)):
        fail("source-manifest.json has no valid commit_sha")
    elif subprocess.run(["git", "-C", str(REPO), "cat-file", "-e",
                         f"{frozen_sha}^{{commit}}"], capture_output=True).returncode:
        fail(f"frozen commit {str(frozen_sha)[:12]} is not in this repository")
    else:
        ok(f"source frozen at {frozen_sha[:12]}")

    if not archive_sha256 or not re.fullmatch(r"[0-9a-f]{64}", str(archive_sha256)):
        fail("source-manifest.json has no valid archive_sha256")
    if not source_tree_sha or not re.fullmatch(r"[0-9a-f]{64}", str(source_tree_sha)):
        fail("source-manifest.json has no valid source_tree_sha256, so no run "
             "record can be tied to the submitted source")

    # Round 4 finding 5: the manifest has to name the archive. Taking the first
    # thing a glob returned made two archives in the directory a coin toss over
    # which one the receipts described.
    if not named:
        fail("source-manifest.json does not name the archive it describes")
    else:
        candidates = [p for p in SUB.glob("uavx-source.*")
                      if p.suffix in {".zip", ".gz", ".tgz"}]
        if len(candidates) != 1:
            fail(f"expected exactly one source archive in submission/, found "
                 f"{len(candidates)}: {[p.name for p in candidates]}")
        elif candidates[0].name != named:
            fail(f"manifest names {named}, the directory holds "
                 f"{candidates[0].name}")
        else:
            archive = candidates[0]

# ------------------------------------------------------------------ archive
section("source archive")
if archive is None:
    fail("no archive to check")
else:
    actual = sha256_file(archive)
    if actual != archive_sha256:
        fail(f"{archive.name} hashes to {actual[:16]}, the manifest claims "
             f"{str(archive_sha256)[:16]}. The file on disk is not the one that "
             f"was frozen.")
    else:
        ok(f"{archive.name} matches its manifest hash, {actual[:16]}")

    entries = {}
    duplicate_entries = []
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                names = [i.filename for i in infos]
                duplicate_entries = sorted({name for name in names
                                            if names.count(name) > 1})
                entries = {i.filename: zf.read(i) for i in infos}
        else:
            with tarfile.open(archive) as tf:
                members = [m for m in tf.getmembers() if m.isfile()]
                names = [m.name for m in members]
                duplicate_entries = sorted({name for name in names
                                            if names.count(name) > 1})
                for m in members:
                    fh = tf.extractfile(m)
                    entries[m.name] = fh.read() if fh else b""
    except (OSError, EOFError, tarfile.TarError, zipfile.BadZipFile,
            RuntimeError) as exc:
        fail(f"{archive.name} cannot be read as a source archive: {exc}")

    ok(f"{len(entries)} files in the archive")
    archive_names = set(entries)
    if duplicate_entries:
        fail(f"the source archive repeats path entries: "
             f"{duplicate_entries[:4]}")

    leaked = [n for n in entries if any(rx.search(n) for rx in FORBIDDEN_IN_ARCHIVE)]
    if leaked:
        fail(f"archive contains build products or secrets: {leaked[:5]}")
    else:
        ok("no build products, git metadata or key material")

    # The part that was missing entirely: prove the bytes came from commit C.
    want = {}
    if frozen_sha:
        tree = subprocess.run(
            ["git", "-C", str(REPO), "ls-tree", "-r", "-z", frozen_sha],
            capture_output=True, text=True).stdout
        want = {}
        for entry in tree.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            parts = meta.split()
            if len(parts) >= 3 and parts[1] == "blob" and path.startswith(ARCHIVE_PATHS):
                want[path] = parts[2]

        mismatched, unexpected = [], []
        seen = set()
        for name, data in entries.items():
            rel = name[len(ARCHIVE_PREFIX):] if name.startswith(ARCHIVE_PREFIX) else name
            if rel not in want:
                unexpected.append(rel)
                continue
            seen.add(rel)
            if blob_id(data) != want[rel]:
                mismatched.append(rel)

        absent = sorted(set(want) - seen)
        if mismatched:
            fail(f"{len(mismatched)} archive file(s) differ from commit "
                 f"{frozen_sha[:12]}: {mismatched[:4]}")
        if unexpected:
            fail(f"{len(unexpected)} archive file(s) are not in commit "
                 f"{frozen_sha[:12]} at all: {unexpected[:4]}")
        if absent:
            fail(f"{len(absent)} tracked source file(s) never made it into the "
                 f"archive: {absent[:4]}")
        if not (mismatched or unexpected or absent):
            ok(f"every file in the archive is byte-identical to commit "
               f"{frozen_sha[:12]}, and none is missing")

    # Round 8 found fresh_install.sh executing the root guide from the archive
    # while the attachment manifest sent submission/INSTALL.md. Both could be
    # valid files and still contain different commands. Once INSTALL.md exists
    # in the frozen commit, the installed and delivered instructions are one
    # artifact and must match byte for byte.
    if "INSTALL.md" in want:
        archived_install = entries.get(ARCHIVE_PREFIX + "INSTALL.md")
        if archived_install is None:
            fail(f"the frozen commit contains INSTALL.md and the archive does "
                 f"not")
        elif install.is_file() and install.read_bytes() != archived_install:
            fail("submission/INSTALL.md differs from the INSTALL.md that the "
                 "fresh-install rehearsal executed inside the archive")
        else:
            ok("the delivered INSTALL.md is the guide executed from the archive")

# --------------------------------------------------------------- licensing
# From the rules: "Participants retain ownership of their intellectual property
# ... submissions must not infringe on third-party IP." This submission ships
# our code on top of PX4, Gazebo and ROS 2, so it has to say what is ours, under
# what terms, and what it is standing on.
section("licensing")
if archive is not None:
    # Round 5 finding 3: the previous check accepted an empty file at any depth
    # whose name ended the right way. That proves neither ownership terms nor
    # third-party notices.
    for name in ("LICENSE", "THIRD-PARTY.md"):
        want = ARCHIVE_PREFIX + name
        if want not in entries:
            fail(f"the archive has no {want}. The rules make the entrant "
                 f"responsible for third-party IP, and this is built on PX4, "
                 f"Gazebo and ROS 2.")
            continue
        body = entries[want].decode("utf-8", "replace")
        if len(body.split()) < 60:
            fail(f"{want} is {len(body.split())} words. An empty licence file is "
                 f"worse than none: it looks like the question was answered.")
        else:
            ok(f"{name} present, {len(body.split())} words")

    tp = entries.get(ARCHIVE_PREFIX + "THIRD-PARTY.md", b"").decode("utf-8", "replace")
    if tp:
        # Every enforced pin in versions.lock has to appear by name, with a
        # licence identifier and an upstream URL beside it. One guessed label
        # for the whole stack is not an inventory.
        lock = (REPO / "stage-1" / "setup" / "versions.lock").read_text(encoding="utf-8")
        pins = [m for m in re.findall(r"^([a-z0-9_]+)=", lock, re.M)
                if not m.startswith("observed_")]
        missing_pin = [k for k in pins if k not in tp]
        if missing_pin:
            fail(f"THIRD-PARTY.md never names these pinned components: "
                 f"{', '.join(missing_pin)}. A dependency nobody listed is a "
                 f"dependency nobody checked the licence of.")
        else:
            ok(f"THIRD-PARTY.md accounts for all {len(pins)} pinned components")
        for token, why in (("BSD 3-Clause", "the PX4 family's terms"),
                           ("Apache 2.0", "the ROS and Gazebo terms"),
                           ("http", "an upstream to check against")):
            if token not in tp:
                fail(f"THIRD-PARTY.md never mentions {token}, so it does not "
                     f"record {why}")

# ------------------------------------------------------- fresh install receipt
# Round 6 finding 1: this compared archive_sha256, commit_sha and the string
# "pass", and no script in the repository produced any of them. A hand-written
# JSON file with three values in it certified the one install that decides
# whether a judge can run this at all. scripts/fresh_install.sh is that script
# now, and everything below reads what it left behind rather than what it
# claimed.
section("fresh install receipt")
receipt_path = SUB / "fresh-install-receipt.json"
FRESH_STEPS = json.loads((REPO / "scripts" / "rehearsal-steps.json")
                         .read_text(encoding="utf-8"))["archive_install"]
if not receipt_path.is_file():
    fail("submission/fresh-install-receipt.json missing. Run "
         "scripts/fresh_install.sh against the frozen archive.")
else:
    loaded_receipt = load_json(receipt_path)
    if not isinstance(loaded_receipt, dict):
        fail("fresh-install-receipt.json must contain one JSON object")
        r = {}
    else:
        r = loaded_receipt
    target = r.get("target") or {}
    if not isinstance(target, dict):
        fail("the fresh install receipt target must be a JSON object")
        target = {}
    # Resolved inside the submission directory, not against the repo root.
    # The wrapper always writes the transcript beside the receipt, and taking
    # the basename means a receipt cannot point the checker at some other file
    # on the machine either.
    transcript = r.get("transcript")
    tname = Path(transcript).name if isinstance(transcript, str) else ""
    tpath = (SUB / tname) if tname else SUB

    if r.get("kind") != "archive-install":
        fail(f"the receipt kind is {r.get('kind')!r}. Only "
             f"scripts/fresh_install.sh writes this file, and it stamps what "
             f"the install actually was. A rebuild of the working tree is a "
             f"different claim from installing the archive on a clean target.")
    elif r.get("result") != "pass":
        fail(f"receipt records result={r.get('result')}")
    elif archive_sha256 and r.get("archive_sha256") != archive_sha256:
        fail(f"the fresh install tested archive "
             f"{str(r.get('archive_sha256'))[:16]} and the one being submitted "
             f"is {archive_sha256[:16]}")
    elif frozen_sha and r.get("commit_sha") != frozen_sha:
        fail(f"receipt is for {str(r.get('commit_sha'))[:12]}, frozen source is "
             f"{str(frozen_sha)[:12]}")
    elif target.get("kind") not in ("wsl-distro", "clean-prefix"):
        fail(f"the receipt names no clean target. target={target!r}. Without "
             f"one, nothing says where the archive was installed, and 'it "
             f"worked on the machine that built it' is not the claim.")
    elif not isinstance(target.get("name"), str) or not target["name"]:
        fail("the receipt records a target kind and no target name")
    elif not isinstance(target.get("path"), str) or not target["path"]:
        fail("the receipt records no install path inside its target")
    elif target.get("kind") == "clean-prefix" and not target.get("isolated_home"):
        fail("the install receipt records a clean prefix with no isolated "
             "home. Round 7 finding 2: a prefix on this machine still sees "
             "~/.uavx-setup, so setup-all.sh skips every dependency step and "
             "the fresh install reuses a machine that was already provisioned.")
    elif (target.get("kind") == "clean-prefix"
          and not Path(target["path"]).is_dir()):
        fail(f"the clean install target {target['path']} does not exist. A "
             f"receipt for an output that is gone cannot prove what was "
             f"installed there.")
    elif (target.get("kind") == "wsl-distro"
          and (not shutil.which("wsl.exe") or subprocess.run(
              ["wsl.exe", "-d", target["name"], "--", "test", "-d",
               target["path"]], capture_output=True).returncode != 0)):
        fail(f"the WSL install target {target['name']}:{target['path']} cannot "
             f"be inspected. Keep the disposable target until the package is "
             f"sent.")
    elif not r.get("smoke_run_id"):
        fail("the receipt records no smoke run id. An install that builds and "
             "never flies is not an install a judge can use.")
    elif r.get("steps_run") != FRESH_STEPS:
        fail(f"steps_run is not what scripts/fresh_install.sh runs. Wanted "
             f"{FRESH_STEPS}, got {r.get('steps_run')}.")
    elif not r.get("transcript") or not tpath.is_file():
        fail(f"the receipt names transcript {r.get('transcript')!r}, which is "
             f"not there. The transcript is the evidence; the receipt is only "
             f"the claim.")
    elif sha256_file(tpath) != r.get("transcript_sha256"):
        fail("the fresh install transcript does not hash to what the receipt "
             "says. One of them has been edited since the install ran.")
    else:
        body = tpath.read_text(encoding="utf-8", errors="replace")
        bad = [ln for ln in body.splitlines()
               if ln.startswith("[exit ") and ln.strip() != "[exit 0]"]
        missing = []
        for label in FRESH_STEPS:
            head = f"=== {label}"
            at = body.find(head)
            if at < 0:
                missing.append(label)
                continue
            rest = body[at + len(head):]
            nxt = rest.find("\n=== ")
            if "[exit 0]" not in (rest if nxt < 0 else rest[:nxt]):
                missing.append(label)
        if bad:
            fail(f"the fresh install transcript records {len(bad)} non-zero "
                 f"exit(s) while the receipt says pass: {bad[:3]}")
        elif missing:
            fail(f"the transcript has no completed section for {missing}")
        elif r.get("smoke_run_id") not in body:
            fail(f"the receipt names smoke run {r.get('smoke_run_id')} and the "
                 f"transcript never mentions it")
        else:
            ok(f"the frozen archive installed and flew on "
               f"{target['kind']} {target['name']}, all "
               f"{len(FRESH_STEPS)} steps in the transcript")

# ----------------------------------------------------------- run evidence
section("run evidence")
evidence = load_json(SUB / "evidence-manifest.json") if (SUB / "evidence-manifest.json").is_file() else None
schema = load_json(REPO / "scenarios" / "run-record.schema.json")
validated_run_hashes = {}
named_runs = {}

if evidence is None:
    fail("submission/evidence-manifest.json missing. It names the exact run "
         "record behind each scenario; a glob over runs/ counts whatever "
         "happens to be lying there.")
elif not isinstance(evidence, dict):
    fail("evidence-manifest.json must contain one JSON object")
else:
    named_runs = evidence.get("runs", {})
    if not isinstance(named_runs, dict):
        fail("evidence-manifest.json runs must be a JSON object")
        named_runs = {}
    for scenario in REQUIRED_RUNS:
        rel = named_runs.get(scenario)
        if not rel:
            fail(f"evidence manifest names no run for {scenario}")
            continue
        # Round 7 finding 3: this was REPO / rel, and Path("/repo") / "C:/x"
        # is "C:/x". An absolute path escaped the repository entirely and a
        # ../ walked out of it, so a record prepared anywhere on the machine
        # could satisfy the package while the judge received no copy of it.
        rel = str(rel).replace("\\", "/")
        if Path(rel).is_absolute() or rel.startswith("/") or ".." in Path(rel).parts:
            fail(f"{scenario}: the evidence manifest names {rel!r}. Run records "
                 f"are named by a path inside runs/, because an absolute path "
                 f"or a .. points at a file that is not in the package.")
            continue
        if not rel.startswith("runs/"):
            fail(f"{scenario}: {rel} is not under runs/. Evidence lives in one "
                 f"place so that what is checked and what is sent are the same "
                 f"files.")
            continue
        path = (RUNS_ROOT / rel).resolve()
        if RUNS_ROOT not in path.parents:
            fail(f"{scenario}: {rel} resolves outside {RUNS_ROOT}")
            continue
        if not path.is_file():
            fail(f"{scenario}: {rel} does not exist")
            continue

        record = load_json(path)
        if record is None:
            continue

        errs = validate(record, schema or {})
        if errs:
            fail(f"{scenario}: {path.name} fails the run-record schema: {errs[0]}")
            continue

        # Round 6 finding 5. The record carries both id sets so equality can be
        # recomputed rather than believed, and this is where it gets
        # recomputed. Doing it only in uavx_eval would leave the last gate
        # before the send trusting a summary line written by the thing it is
        # checking: generated 450, unique_delivered 450, and 450 of the wrong
        # packets delivered.
        obs = record.get("observations")
        if isinstance(obs, dict):
            obs_bad = False
            gen_list = obs.get("generated_ids") or []
            got_list = obs.get("delivered_ids") or []
            gen = set(gen_list)
            got = set(got_list)
            if len(gen) != len(gen_list) or len(got) != len(got_list):
                fail(f"{scenario}: observation id lists contain duplicates. "
                     f"Set equality cannot prove delivered once when the "
                     f"source lists already hide repeated ids.")
                obs_bad = True
            missing = sorted(gen - got)
            unexpected = sorted(got - gen)
            if missing or unexpected:
                fail(f"{scenario}: generated_ids and delivered_ids are not the "
                     f"same set. {len(missing)} generated and never delivered "
                     f"{missing[:3]}, {len(unexpected)} delivered and never "
                     f"generated {unexpected[:3]}. Delivered once is a claim "
                     f"about which observations arrived, not how many.")
                obs_bad = True
            for field, size in (("generated", len(gen)),
                                ("unique_delivered", len(got))):
                if obs.get(field) != size:
                    fail(f"{scenario}: observations.{field} says "
                         f"{obs.get(field)} and its own id set holds {size}. A "
                         f"record that disagrees with itself cannot be read "
                         f"either way.")
                    obs_bad = True

            # Round 8. The earlier timestamps were aggregate claims. They did
            # not connect any observation id to the outage or its later GCS
            # delivery. Rebuild every total and clock from the ledger here.
            ledger = obs.get("ledger") or []
            ledger_by_id = {}
            finite_rows = True
            for index, row in enumerate(ledger):
                ident = row.get("id") if isinstance(row, dict) else None
                if ident in ledger_by_id:
                    fail(f"{scenario}: observations.ledger repeats id {ident!r}")
                    obs_bad = True
                elif ident is not None:
                    ledger_by_id[ident] = row
                if not isinstance(row, dict):
                    finite_rows = False
                    continue
                created = row.get("created_at_s")
                delivered = row.get("delivered_at_s")
                if (not isinstance(created, (int, float))
                        or isinstance(created, bool)
                        or not math.isfinite(created)):
                    fail(f"{scenario}: observations.ledger[{index}] has a "
                         f"non-finite creation time")
                    finite_rows = False
                    obs_bad = True
                if (delivered is not None
                        and (not isinstance(delivered, (int, float))
                             or isinstance(delivered, bool)
                             or not math.isfinite(delivered))):
                    fail(f"{scenario}: observations.ledger[{index}] has a "
                         f"non-finite delivery time")
                    finite_rows = False
                    obs_bad = True
                if (isinstance(created, (int, float))
                        and isinstance(delivered, (int, float))
                        and math.isfinite(created) and math.isfinite(delivered)
                        and delivered < created):
                    fail(f"{scenario}: {ident} was delivered before it was "
                         f"created")
                    obs_bad = True

            ledger_ids = set(ledger_by_id)
            if ledger_ids != gen:
                absent = sorted(gen - ledger_ids)
                extra = sorted(ledger_ids - gen)
                fail(f"{scenario}: observations.ledger ids do not match "
                     f"generated_ids. Missing {absent[:3]}, extra {extra[:3]}")
                obs_bad = True

            ledger_delivered = {
                ident for ident, row in ledger_by_id.items()
                if isinstance(row, dict) and row.get("delivered_at_s") is not None
            }
            if ledger_delivered != got:
                fail(f"{scenario}: delivered_ids do not match the ids with a "
                     f"delivery timestamp in observations.ledger")
                obs_bad = True

            clock_names = ("outage_start_s", "outage_end_s", "drain_start_s",
                           "drain_end_s", "delivery_complete_s",
                           "backlog_drain_s")
            clocks = {name: obs.get(name) for name in clock_names}
            clocks_ok = all(isinstance(value, (int, float))
                            and not isinstance(value, bool)
                            and math.isfinite(value)
                            for value in clocks.values())
            if not clocks_ok:
                fail(f"{scenario}: observation drain clocks must all be finite")
                obs_bad = True
            elif not (clocks["outage_start_s"] < clocks["outage_end_s"]
                      <= clocks["drain_start_s"] <= clocks["drain_end_s"]
                      <= clocks["delivery_complete_s"]
                      <= record.get("elapsed_sim_s", -1)):
                fail(f"{scenario}: observation clocks are out of order. Need "
                     f"outage start < outage end <= drain start <= local drain "
                     f"end <= GCS delivery complete <= elapsed simulation.")
                obs_bad = True

            if clocks_ok:
                measured = clocks["drain_end_s"] - clocks["drain_start_s"]
                if not math.isclose(obs["backlog_drain_s"], measured,
                                    abs_tol=1e-6):
                    fail(f"{scenario}: observations.backlog_drain_s says "
                         f"{obs['backlog_drain_s']} but drain_end_s minus "
                         f"drain_start_s is {measured}")
                    obs_bad = True
                if obs["backlog_drain_s"] > 2.25 + 1e-6:
                    fail(f"{scenario}: the local backlog took "
                         f"{obs['backlog_drain_s']}s to drain, past the 2.25 s "
                         f"the queue size was derived from")
                    obs_bad = True

            if finite_rows and clocks_ok:
                during = {
                    ident for ident, row in ledger_by_id.items()
                    if clocks["outage_start_s"] <= row["created_at_s"]
                    < clocks["outage_end_s"]
                }
                after = {
                    ident for ident in during
                    if ledger_by_id[ident]["delivered_at_s"] is not None
                    and ledger_by_id[ident]["delivered_at_s"]
                    >= clocks["drain_start_s"]
                }
                if obs.get("generated_during_outage") != len(during):
                    fail(f"{scenario}: observations.generated_during_outage "
                         f"says {obs.get('generated_during_outage')} but the "
                         f"ledger places {len(during)} ids in that window")
                    obs_bad = True
                if obs.get("delivered_after_restore") != len(after):
                    fail(f"{scenario}: observations.delivered_after_restore "
                         f"says {obs.get('delivered_after_restore')} but the "
                         f"ledger proves {len(after)}")
                    obs_bad = True
                if scenario == "queue_drain":
                    custodied_list = obs.get("custodied_ids") or []
                    custodied = set(custodied_list)
                    if len(custodied) != len(custodied_list):
                        fail(f"{scenario}: observations.custodied_ids contains "
                             f"duplicates")
                        obs_bad = True
                    if custodied != during:
                        absent = sorted(during - custodied)
                        extra = sorted(custodied - during)
                        fail(f"{scenario}: custodied_ids are not the outage "
                             f"set. Missing {absent[:3]}, extra {extra[:3]}")
                        obs_bad = True
                    if obs.get("custodied") != len(custodied):
                        fail(f"{scenario}: observations.custodied says "
                             f"{obs.get('custodied')} but custodied_ids holds "
                             f"{len(custodied)} ids")
                        obs_bad = True
                if during and len(after) == len(during):
                    last_delivery = max(
                        ledger_by_id[ident]["delivered_at_s"] for ident in during)
                    if not math.isclose(clocks["delivery_complete_s"],
                                        last_delivery, abs_tol=1e-6):
                        fail(f"{scenario}: observations.delivery_complete_s "
                             f"says {clocks['delivery_complete_s']} but the last "
                             f"outage id reached the GCS at {last_delivery}")
                        obs_bad = True

            if obs_bad:
                continue

        # A run that swapped is a run whose timings mean nothing, and
        # time_to_reconnect_s is graded. The schema can require the field; only
        # this can say what value is acceptable.
        res = record.get("resources") or {}
        # Round 7 finding 5: the peak was required to exist and never
        # compared with anything, so a record reporting 20 GB resident and no
        # swap passed. The stated capacity risk was the one thing never tested.
        if res.get("peak_rss_mib", 0) > PEAK_RSS_CEILING_MIB:
            fail(f"{scenario}: peak resident memory was "
                 f"{res['peak_rss_mib']:.0f} MiB against a "
                 f"{PEAK_RSS_CEILING_MIB:.0f} MiB ceiling. The run fitted on "
                 f"the machine that produced it and will not fit on the "
                 f"target.")
            continue
        if res.get("swap_used_mib", 0) > 0:
            fail(f"{scenario}: the machine swapped {res['swap_used_mib']} MiB "
                 f"during this run, so every timing in it is a measurement of "
                 f"the swap file")
            continue
        if res.get("samples", 0) < 10:
            fail(f"{scenario}: resources.samples is {res.get('samples')}. Too "
                 f"few samples and a healthy peak reads the same as one nobody "
                 f"watched.")
            continue

        want_scenario = f"scenarios/{scenario}.yaml"
        if record.get("scenario_path") != want_scenario:
            fail(f"{scenario}: {path.name} records a run of "
                 f"{record.get('scenario_path')}")
            continue
        if record.get("completion") != "complete":
            fail(f"{scenario}: run completion is {record.get('completion')}")
            continue

        elapsed = record.get("elapsed_sim_s", 0)
        wanted = record.get("requested_duration_s", 0)
        if wanted and elapsed < 0.95 * wanted:
            fail(f"{scenario}: ran {elapsed:.0f}s of a requested {wanted:.0f}s")
            continue

        unfired = [e for e in record.get("injected_events", [])
                   if e.get("observed_t") is None]
        if unfired:
            fail(f"{scenario}: {len(unfired)} injected event(s) never fired, so "
                 f"the run did not test what it claims")
            continue

        if source_tree_sha and record.get("source_tree_sha256") != source_tree_sha:
            fail(f"{scenario}: produced from source "
                 f"{str(record.get('source_tree_sha256'))[:12]}, the submission "
                 f"freezes {source_tree_sha[:12]}. The evidence and the code do "
                 f"not match.")
            continue

        # And the real checker, not this file's opinion of it.
        proc = subprocess.run(
            [sys.executable, "-m", "uavx_eval.check", str(path),
             "--expect-scenario", want_scenario],
            capture_output=True, text=True, cwd=str(REPO))
        if proc.returncode != 0:
            head = (proc.stdout + proc.stderr).strip().splitlines()
            fail(f"{scenario}: uavx_eval.check rejected {path.name}: "
                 f"{head[-1] if head else 'no output'}")
            continue

        rid = record.get("run_id", "")
        if proposal_text and rid and rid not in proposal_text:
            fail(f"{scenario}: the proposal never cites run id {rid}, so its "
                 f"numbers point at nothing a reader can check")
            continue

        validated_run_hashes[rel] = sha256_file(path)
        ok(f"{scenario}: {rid} validated, {elapsed:.0f}s, source matches")

# ----------------------------------------------------------- attachments
section("attachments and delivery")
att_path = SUB / "attachment-manifest.json"
attachment_sha = None
if not att_path.is_file():
    fail("submission/attachment-manifest.json missing. It is the list of what "
         "actually goes in the email, and what the send receipt binds to.")
else:
    att = load_json(att_path) or {}
    if not isinstance(att, dict):
        fail("attachment-manifest.json must contain one JSON object")
        att = {}
    attachments = att.get("attachments", [])
    links = att.get("delivered_by_link", [])
    if not isinstance(attachments, list):
        fail("attachment-manifest.json attachments must be a list")
        attachments = []
    if not isinstance(links, list):
        fail("attachment-manifest.json delivered_by_link must be a list")
        links = []
    if any(not isinstance(item, dict) for item in attachments + links):
        fail("every delivery manifest entry must be a JSON object")
    attachments = [item for item in attachments if isinstance(item, dict)]
    links = [item for item in links if isinstance(item, dict)]

    delivery_names = [str(item.get("name") or "").replace("\\", "/")
                      for item in attachments + links]
    duplicates = sorted({name for name in delivery_names
                         if name and delivery_names.count(name) > 1})
    if duplicates:
        fail(f"the delivery manifest names the same file more than once: "
             f"{duplicates[:4]}")

    attachment_sha = hashlib.sha256(att_path.read_bytes()).hexdigest()
    total = 0
    for item in attachments:
        name = str(item.get("name") or "").replace("\\", "/")
        if (not name or Path(name).is_absolute() or name.startswith("/")
                or ".." in Path(name).parts):
            fail(f"attachment manifest path {name!r} must stay inside "
                 f"submission/")
            continue
        p = (SUB / name).resolve()
        if SUB.resolve() not in p.parents:
            fail(f"attachment manifest path {name!r} resolves outside "
                 f"submission/")
            continue
        if not p.is_file():
            fail(f"attachment manifest lists {item.get('name')}, which is absent")
            continue
        size = p.stat().st_size
        digest = sha256_file(p)
        if item.get("sha256") != digest:
            fail(f"{p.name} does not match the hash in the attachment manifest")
        elif item.get("bytes") != size:
            fail(f"{p.name} is {size} bytes, the manifest says {item.get('bytes')}")
        else:
            total += size

    # Round 5 finding 3: only the proposal and INSTALL.md were required here,
    # so a receipt for an email carrying two of the five deliverables reached
    # the submitted state. The archive and the video had to exist on disk and
    # never had to be sent.
    listed = set(delivery_names)

    # Round 7 finding 3, the other half. The checker revalidated every named
    # run record and nothing required those records to be in the email, so the
    # evidence behind every number in the proposal could stay on this machine.
    if evidence is not None and archive is not None:
        named = [str(v).replace("\\", "/") for v in
                 named_runs.values()]
        inside = archive_names if "archive_names" in dir() else set()
        missing_ev = [r for r in named
                      if r not in listed
                      and f"uavx-source/{r}" not in inside
                      and r not in inside]
        if missing_ev:
            fail(f"{len(missing_ev)} run record(s) are named as evidence and "
                 f"are in neither the archive nor the delivery manifest, so "
                 f"the judge gets the numbers and not the runs behind them: "
                 f"{missing_ev[:3]}")

        # Round 8 found the evidence checker reading REPO/runs while delivery
        # read SUB/runs. Matching names did not bind those two files. A valid
        # local record and a different same-named delivered copy both passed.
        for rel, validated_hash in sorted(validated_run_hashes.items()):
            if rel in listed:
                delivered = (SUB / rel).resolve()
                if delivered.is_file() and sha256_file(delivered) != validated_hash:
                    fail(f"the delivered copy of {rel} differs from the run "
                         f"that was validated. A matching path is not matching "
                         f"evidence unless the bytes are the same.")
            else:
                archived_name = (f"{ARCHIVE_PREFIX}{rel}"
                                 if f"{ARCHIVE_PREFIX}{rel}" in inside else rel)
                if archived_name in inside:
                    delivered_hash = hashlib.sha256(entries[archived_name]).hexdigest()
                    if delivered_hash != validated_hash:
                        fail(f"the archived copy of {rel} differs from the run "
                             f"that was validated")
    needed = ["proposal.pdf", "INSTALL.md"]
    if archive is not None:
        needed.append(archive.name)
    demos = [n for n in listed if str(n).startswith("demo.")]
    for name in needed:
        if name not in listed:
            fail(f"the delivery manifest does not include {name}, so the "
                 f"submission can be recorded as sent without it")
    if len(demos) != 1:
        fail(f"the delivery manifest names {len(demos)} demo files; there is "
             f"exactly one video deliverable")

    # Round 6 finding 3. The count above is satisfied by demo.txt. What has to
    # be sent is the file that decoded, by name, and then by bytes, because a
    # manifest entry naming demo.mp4 while carrying another file's hash is the
    # same omission wearing the right label.
    if checked_video is None:
        fail("no demo video was checked, so nothing can confirm the delivered "
             "one is the one that decodes")
    elif checked_video.name not in demos:
        fail(f"the delivery manifest sends {demos[0] if demos else 'no demo'} "
             f"and the video that was decoded is {checked_video.name}. The "
             f"checked video is not the one going in the email.")
    else:
        want = sha256_file(checked_video)
        entry = next((i for i in attachments + links
                      if i.get("name") == checked_video.name), None)
        if entry is None:
            fail(f"{checked_video.name} is listed and has no manifest entry")
        elif entry.get("sha256") != want:
            fail(f"the manifest sends a {checked_video.name} whose hash is not "
                 f"the file that decoded. {str(entry.get('sha256'))[:16]} "
                 f"against {want[:16]}.")
        elif entry.get("bytes") != checked_video.stat().st_size:
            fail(f"the manifest records {entry.get('bytes')} bytes for "
                 f"{checked_video.name}, the checked file is "
                 f"{checked_video.stat().st_size}")
        else:
            ok(f"the delivered demo is {checked_video.name}, the file that was "
               f"decoded, by hash")

    # A file sent as a link is still a deliverable and still has to be checked.
    for item in links:
        name = str(item.get("name") or "").replace("\\", "/")
        for field in ("route", "url", "bytes", "sha256", "access_tested"):
            if not item.get(field):
                fail(f"{name} is delivered by link and its manifest "
                     f"entry has no {field}. A link nobody opened is not a "
                     f"delivery.")
        if (not name or Path(name).is_absolute() or name.startswith("/")
                or ".." in Path(name).parts):
            fail(f"linked delivery path {name!r} must stay inside submission/")
            continue
        p = (SUB / name).resolve()
        if SUB.resolve() not in p.parents:
            fail(f"linked delivery path {name!r} resolves outside submission/")
            continue
        if not p.is_file():
            fail(f"linked delivery {name} has no local copy to hash and size")
            continue
        digest = sha256_file(p)
        size = p.stat().st_size
        if item.get("sha256") != digest:
            fail(f"{name} on disk does not match the hash published at "
                 f"{item.get('url')}")
        if item.get("bytes") != size:
            fail(f"linked delivery {name} is {size} bytes, the manifest says "
                 f"{item.get('bytes')}")

    # Not guarded on `not problems`. The budget is the one check whose answer
    # a human has to act on days ahead of the deadline, and hiding it behind
    # an unrelated missing PDF is how it stays unanswered until the submission tail.
    if human:
        limit = human["delivery"]["attachment_limit_mb"] * 1024 * 1024
        mb = total / 1024 / 1024
        if total <= limit:
            ok(f"{mb:.1f} MB of attachments, inside the "
               f"{human['delivery']['attachment_limit_mb']} MB budget")
        else:
            route = human["delivery"]["route"].lower()
            if "link" in route or "drive" in route or "split" in route:
                ok(f"{mb:.1f} MB is over the "
                   f"{human['delivery']['attachment_limit_mb']} MB budget, and "
                   f"the recorded route handles it: {human['delivery']['route']}")
            else:
                fail(f"{mb:.1f} MB of attachments against a "
                     f"{human['delivery']['attachment_limit_mb']} MB budget, and "
                     f"the recorded route is attachments. Finding this out on "
                     f"26 September is how a submission fails to arrive.")

# ------------------------------------------------------- the published spec
# "The organizers reserve the right to modify, postpone, or cancel the Grand
# Challenge or any stage." Everything above is checked against a capture taken
# on 26 August. Without this, a changed deadline or a changed deliverable list
# would be discovered by not qualifying.
section("the published competition record")
# Overridable only so the fixture suite can stub it at the process boundary.
# Chunk 4.8 runs the real one: a package checked against a stub is checked against
# nothing.
SPEC_CHECKER = os.environ.get("UAVX_SPEC_CHECKER") or str(
    REPO / "scripts" / "check_competition_spec.py")
spec = subprocess.run([sys.executable, SPEC_CHECKER],
                      capture_output=True, text=True, cwd=str(REPO))
for line in spec.stdout.strip().splitlines():
    print(f"  {line.strip()}")
if spec.returncode != 0:
    fail("the published competition record does not match research/. Read the "
         "diff above before sending anything.")

# --------------------------------------------------------------- the send
section("submission state")
sent = SUB / "sent-receipt.json"
if problems:
    print(f"\nNOT READY: {len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)

if not sent.is_file():
    print(f"""
  Package is complete and nothing is left for a machine to check.

  Sending the email is a human step. The loop halts here by design.

  Send to {ORGANISER_EMAIL} with everything in the attachment manifest,
  then record it so this becomes a closed state:

      submission/sent-receipt.json
      {{"sent_at": "2026-09-26T...", "to": "{ORGANISER_EMAIL}",
       "message_id": "...", "commit_sha": "{frozen_sha}",
       "attachment_manifest_sha256": "{attachment_sha}"}}
""")
    sys.exit(2)

s = load_json(sent) or {}
for field in ("sent_at", "to", "message_id", "commit_sha",
              "attachment_manifest_sha256"):
    if not s.get(field):
        fail(f"sent-receipt.json is missing {field}")
if frozen_sha and s.get("commit_sha") != frozen_sha:
    fail(f"sent-receipt records {str(s.get('commit_sha'))[:12]}, frozen source "
         f"is {str(frozen_sha)[:12]}")
# Round 4 finding 5: binding the send to the commit alone said nothing about
# what was attached to the email. This binds it to the exact file list.
if attachment_sha and s.get("attachment_manifest_sha256") != attachment_sha:
    fail("sent-receipt describes a different set of attachments than the one "
         "in submission/attachment-manifest.json")
if s.get("to") != ORGANISER_EMAIL:
    fail(f"sent to {s.get('to')}, must be {ORGANISER_EMAIL}")

if problems:
    sys.exit(1)

print(f"  ok    sent {s['sent_at']} to {s['to']}, message {s['message_id']}")
print("\nSUBMITTED")
sys.exit(0)
