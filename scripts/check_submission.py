#!/usr/bin/env python3
"""Verify the Stage 1 submission package is real, not just present.

Round 2 finding 3: the planned checker tested existence, a page count, a video
duration and a checklist that claimed the files were there. A blank seven-page
PDF, a corrupt three-second MP4, an archive missing half the source and a
hand-written checklist all satisfied that contract. The loop could also mark W5
green without an email ever being sent, because sending is a human step and
nothing recorded whether it happened.

So this checks content, not presence, and it ends by refusing to pass until a
human has recorded the send.

    python3 scripts/check_submission.py

Exit 0 means the package is ready AND the send has been recorded. Exit 2 means
the package is good but the email has not gone yet, which is the normal state
the moment before a human sends it. Exit 1 means something is actually wrong.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SUB = REPO / "submission"

# Techfest Stage 1, from context.md: a 6-8 page technical proposal with software
# architecture, a working proof-of-concept simulation, source code, installation
# instructions, and a demo video.
MIN_PAGES, MAX_PAGES = 6, 8
MAX_VIDEO_S = 180          # our assumption, not theirs; see organiser-email.md
MIN_VIDEO_S = 30
ORGANISER_EMAIL = "pushpak_gc2026@aero.iitb.ac.in"

# Sections the proposal must actually contain, because "6 to 8 pages" is not
# evidence that architecture was covered.
REQUIRED_SECTIONS = [
    "architecture",
    "communication",
    "relay",
    "fault",
    "safety",
    "results",
]

# Things that must never end up in a submitted archive.
FORBIDDEN_IN_ARCHIVE = [
    re.compile(r"(^|/)\.env($|\.)"),
    re.compile(r"(^|/)\.git/"),
    re.compile(r"\.(pem|key)$"),
    re.compile(r"(^|/)(build|install|log)/"),
    re.compile(r"__pycache__/"),
]

problems: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)
    print(f"  FAIL  {msg}")


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def section(title: str) -> None:
    print(f"\n--- {title}")


# ---------------------------------------------------------------- proposal
section("proposal")
pdf = SUB / "proposal.pdf"
if not pdf.is_file():
    fail("submission/proposal.pdf missing")
else:
    text = ""
    pages = None
    if have("pdftotext"):
        try:
            text = subprocess.run(
                ["pdftotext", str(pdf), "-"], capture_output=True, text=True, timeout=120
            ).stdout
            pages = text.count("\f")
        except Exception as exc:  # noqa: BLE001
            fail(f"pdftotext failed: {exc}")
    else:
        fail("pdftotext not installed, cannot read the PDF. apt install poppler-utils")

    if pages is not None:
        if MIN_PAGES <= pages <= MAX_PAGES:
            ok(f"page count {pages}")
        else:
            fail(f"page count {pages}, must be {MIN_PAGES} to {MAX_PAGES}")

    words = len(text.split())
    if words < 1500:
        fail(f"proposal has only {words} words. A 6 to 8 page document is not that short; is it mostly images or empty?")
    else:
        ok(f"word count {words}")

    low = text.lower()
    missing = [s for s in REQUIRED_SECTIONS if s not in low]
    if missing:
        fail(f"proposal never mentions: {', '.join(missing)}")
    else:
        ok("covers architecture, communication, relay, fault, safety, results")

# ------------------------------------------------------------------- video
section("video")
video = next((p for p in (SUB / "demo.mp4", SUB / "demo.mkv") if p.is_file()), None)
if video is None:
    fail("submission/demo.mp4 missing")
elif not have("ffprobe"):
    fail("ffprobe not installed, cannot verify the video decodes. apt install ffmpeg")
else:
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)],
            capture_output=True, text=True, timeout=120).stdout.strip())
    except Exception as exc:  # noqa: BLE001
        dur = -1.0
        fail(f"ffprobe could not read a duration: {exc}")

    if dur >= 0:
        if MIN_VIDEO_S <= dur <= MAX_VIDEO_S:
            ok(f"duration {dur:.0f}s")
        else:
            fail(f"duration {dur:.0f}s, expected {MIN_VIDEO_S} to {MAX_VIDEO_S}")

    # Decode the whole thing. A file with a valid header and a corrupt body
    # reports a duration perfectly well.
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"],
        capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or proc.stderr.strip():
        fail(f"video does not decode cleanly: {proc.stderr.strip()[:200]}")
    else:
        ok("decodes end to end with no errors")

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

# ------------------------------------------------------------------ archive
section("source archive")
archive = next(
    (p for p in SUB.glob("uavx-source.*") if p.suffix in {".zip", ".gz", ".tgz"}), None
) if SUB.is_dir() else None
if archive is None:
    fail("no submission/uavx-source.{zip,tar.gz} found")
else:
    if archive.suffix == ".zip":
        names = zipfile.ZipFile(archive).namelist()
    else:
        with tarfile.open(archive) as tf:
            names = tf.getnames()

    ok(f"{archive.name}, {len(names)} entries")

    leaked = [n for n in names if any(rx.search(n) for rx in FORBIDDEN_IN_ARCHIVE)]
    if leaked:
        fail(f"archive contains build products or secrets: {leaked[:5]}")
    else:
        ok("no build products, git metadata or key material")

    needed = ["uavx_ws/src", "scenarios/", "scripts/", "stage-1/setup/"]
    absent = [n for n in needed if not any(n in e for e in names)]
    if absent:
        fail(f"archive is missing: {', '.join(absent)}")
    else:
        ok("carries source, scenarios, scripts and setup")

# ------------------------------------------------------------- source freeze
# Round 3 finding 3: receipts used to be compared against current HEAD, which
# cannot work. W4 and W5 necessarily commit after the W3 install, and writing
# the sent receipt then committing it moves HEAD again, so the receipt
# invalidates itself on the next run.
#
# The fix is that the SOURCE ARCHIVE is the thing being submitted, not the
# working tree. Everything binds to the frozen commit C recorded in the archive
# manifest and to the archive's own hash. Later packaging commits move HEAD
# freely without touching the claim.
section("source freeze")
manifest_path = SUB / "source-manifest.json"
frozen_sha = None
archive_sha256 = None
if not manifest_path.is_file():
    fail("submission/source-manifest.json missing. Freeze the source and build the archive from it.")
else:
    man = json.loads(manifest_path.read_text())
    frozen_sha = man.get("commit_sha")
    archive_sha256 = man.get("archive_sha256")
    if not frozen_sha or len(frozen_sha) != 40:
        fail("source-manifest.json has no valid commit_sha")
    if not archive_sha256:
        fail("source-manifest.json has no archive_sha256")
    else:
        ok(f"source frozen at {frozen_sha[:12]}")

    # The frozen commit must actually exist in this repo's history.
    exists = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{frozen_sha}^{{commit}}"],
                            capture_output=True).returncode == 0
    if not exists:
        fail(f"frozen commit {str(frozen_sha)[:12]} is not in this repository")

# ------------------------------------------------------- fresh install receipt
section("fresh install receipt")
receipt = SUB / "fresh-install-receipt.json"
if not receipt.is_file():
    fail("submission/fresh-install-receipt.json missing. Run scripts/fresh_install_test.sh against the archive.")
else:
    r = json.loads(receipt.read_text())
    # Bound to the archive, not to a moving HEAD.
    if archive_sha256 and r.get("archive_sha256") != archive_sha256:
        fail("the fresh install tested a different archive than the one being submitted")
    elif frozen_sha and r.get("commit_sha") != frozen_sha:
        fail(f"receipt is for {str(r.get('commit_sha'))[:12]}, frozen source is {str(frozen_sha)[:12]}")
    else:
        ok("fresh install verified against the submitted archive")
    if r.get("result") != "pass":
        fail(f"receipt records result={r.get('result')}")

# ----------------------------------------------------------- run evidence
section("run evidence")
runs = REPO / "runs"
required_runs = [
    "survey_baseline", "relay_required", "direct_only", "relay_kill", "encounter",
]
if not runs.is_dir():
    fail("runs/ does not exist, so no metric in the proposal has evidence behind it")
else:
    present = {p.stem.rsplit("_", 1)[0] for p in runs.glob("*.jsonl")}
    for name in required_runs:
        if any(name in p.name for p in runs.glob("*.jsonl")):
            ok(f"{name} run recorded")
        else:
            fail(f"no recorded run for {name}")

# --------------------------------------------------------------- the send
section("submission state")
sent = SUB / "sent-receipt.json"
if problems:
    print(f"\nNOT READY: {len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    sys.exit(1)

if not sent.is_file():
    print("""
  Package is complete and nothing is left for a machine to check.

  Sending the email is a human step. The loop halts here by design.

  Send to pushpak_gc2026@aero.iitb.ac.in with everything in submission/,
  then record it so this becomes a closed state:

      submission/sent-receipt.json
      {"sent_at": "2026-09-26T...", "to": "pushpak_gc2026@aero.iitb.ac.in",
       "message_id": "...", "commit_sha": "<frozen source commit from source-manifest.json>"}
""")
    sys.exit(2)

s = json.loads(sent.read_text())
for field in ("sent_at", "to", "message_id", "commit_sha"):
    if not s.get(field):
        fail(f"sent-receipt.json is missing {field}")
# Bound to the frozen source, so committing this very file does not invalidate it.
if frozen_sha and s.get("commit_sha") != frozen_sha:
    fail(f"sent-receipt records {str(s.get('commit_sha'))[:12]}, frozen source is {str(frozen_sha)[:12]}")
if s.get("to") != ORGANISER_EMAIL:
    fail(f"sent to {s.get('to')}, must be {ORGANISER_EMAIL}")

if problems:
    sys.exit(1)

print(f"  ok    sent {s['sent_at']} to {s['to']}, message {s['message_id']}")
print("\nSUBMITTED")
sys.exit(0)
