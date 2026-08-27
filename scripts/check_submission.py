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
control were not on the list at all. Now W5 revalidates every run against the
schema and against `uavx_eval.check`, binds each to the archived source, and
requires the proposal to cite the run ids it is quoting numbers from.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonschema_mini import validate                      # noqa: E402
from source_tree_hash import blob_id                      # noqa: E402
import check_human_preflight                              # noqa: E402

REPO = Path(__file__).resolve().parent.parent
# Overridable so scripts/test_submission_fixtures.py can point the whole checker
# at a tampered copy and prove it says no. A checker nobody has watched reject
# something is not evidence, and this repo has shipped two of those.
SUB = Path(os.environ.get("UAVX_SUBMISSION_DIR") or (REPO / "submission"))
RUNS = Path(os.environ.get("UAVX_RUNS_DIR") or (REPO / "runs"))

# Techfest Stage 1, from context.md: a 6-8 page technical proposal with software
# architecture, a working proof-of-concept simulation, source code, installation
# instructions, and a demo video.
MIN_PAGES, MAX_PAGES = 6, 8
MAX_VIDEO_S = 180          # our assumption, not theirs; see organiser-email.md
MIN_VIDEO_S = 30
ORGANISER_EMAIL = "pushpak_gc2026@aero.iitb.ac.in"

REQUIRED_SECTIONS = [
    "architecture", "communication", "relay", "fault", "safety", "results",
    # From the rules, not the rubric: "Proposed solutions must comply with all
    # applicable laws, aviation requirements, and safety protocols in India."
    # A BVLOS swarm proposal that never mentions BVLOS regulation is ignoring a
    # stated rule, in front of a panel from an aerospace department.
    "regulat",
]

# Every scenario whose numbers the proposal is allowed to quote. The integrated
# mission and the safety control are on it because round 4 finding 6 found the
# one run that proves the swarm, and the one run that gives the safety result
# its meaning, were both missing from the W5 list.
REQUIRED_RUNS = [
    "survey_baseline", "relay_required", "direct_only", "relay_kill",
    "link_loss", "encounter", "encounter_noyield", "mission_integrated",
]

# What the archive is allowed to hold, matching scripts/freeze_source.sh.
ARCHIVE_PATHS = ("uavx_ws/", "scenarios/", "scripts/", "stage-1/")
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
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path.name}: {exc}")
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------- human preflight
# W5 needs the delivery budget, and if the receipt is not valid there is no
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
        proposal_text = subprocess.run(
            ["pdftotext", str(pdf), "-"], capture_output=True, text=True,
            timeout=120).stdout
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

# ------------------------------------------------------------------- video
section("video")
video = next((p for p in (SUB / "demo.mp4", SUB / "demo.mkv") if p.is_file()), None)
if video is None:
    fail("submission/demo.mp4 missing")
elif not have("ffprobe") or not have("ffmpeg"):
    fail("ffmpeg not installed, cannot verify the video decodes. apt install ffmpeg")
else:
    try:
        dur = float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)],
            capture_output=True, text=True, timeout=120).stdout.strip())
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

# ------------------------------------------------------------- source freeze
section("source freeze")
manifest = load_json(SUB / "source-manifest.json") if (SUB / "source-manifest.json").is_file() else None
frozen_sha = archive_sha256 = source_tree_sha = None
archive = None

if manifest is None:
    fail("submission/source-manifest.json missing. Run scripts/freeze_source.sh.")
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

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            entries = {i.filename: zf.read(i.filename)
                       for i in zf.infolist() if not i.is_dir()}
    else:
        with tarfile.open(archive) as tf:
            entries = {}
            for m in tf.getmembers():
                if m.isfile():
                    fh = tf.extractfile(m)
                    entries[m.name] = fh.read() if fh else b""

    ok(f"{len(entries)} files in the archive")

    leaked = [n for n in entries if any(rx.search(n) for rx in FORBIDDEN_IN_ARCHIVE)]
    if leaked:
        fail(f"archive contains build products or secrets: {leaked[:5]}")
    else:
        ok("no build products, git metadata or key material")

    # The part that was missing entirely: prove the bytes came from commit C.
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

# --------------------------------------------------------------- licensing
# From the rules: "Participants retain ownership of their intellectual property
# ... submissions must not infringe on third-party IP." This submission ships
# our code on top of PX4, Gazebo and ROS 2, so it has to say what is ours, under
# what terms, and what it is standing on.
section("licensing")
for name, why in (("LICENSE", "the terms our own code is offered under"),
                  ("THIRD-PARTY.md", "what the submission depends on and under "
                                     "what licence")):
    if archive is None:
        break
    hit = any(n.endswith("/" + name) or n == name for n in entries)
    if hit:
        ok(f"{name} is in the archive, {why}")
    else:
        fail(f"the archive has no {name}. The rules make the entrant responsible "
             f"for not infringing third-party IP, and this submission is built "
             f"on PX4, Gazebo and ROS 2.")

# ------------------------------------------------------- fresh install receipt
section("fresh install receipt")
receipt_path = SUB / "fresh-install-receipt.json"
if not receipt_path.is_file():
    fail("submission/fresh-install-receipt.json missing. Run the fresh install "
         "against the archive.")
else:
    r = load_json(receipt_path) or {}
    if archive_sha256 and r.get("archive_sha256") != archive_sha256:
        fail("the fresh install tested a different archive than the one being "
             "submitted")
    elif frozen_sha and r.get("commit_sha") != frozen_sha:
        fail(f"receipt is for {str(r.get('commit_sha'))[:12]}, frozen source is "
             f"{str(frozen_sha)[:12]}")
    else:
        ok("fresh install verified against the submitted archive")
    if r.get("result") != "pass":
        fail(f"receipt records result={r.get('result')}")

# ----------------------------------------------------------- run evidence
section("run evidence")
evidence = load_json(SUB / "evidence-manifest.json") if (SUB / "evidence-manifest.json").is_file() else None
schema = load_json(REPO / "scenarios" / "run-record.schema.json")

if evidence is None:
    fail("submission/evidence-manifest.json missing. It names the exact run "
         "record behind each scenario; a glob over runs/ counts whatever "
         "happens to be lying there.")
else:
    named_runs = evidence.get("runs", {})
    for scenario in REQUIRED_RUNS:
        rel = named_runs.get(scenario)
        if not rel:
            fail(f"evidence manifest names no run for {scenario}")
            continue
        path = REPO / rel
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
    attachment_sha = hashlib.sha256(att_path.read_bytes()).hexdigest()
    total = 0
    for item in att.get("attachments", []):
        p = SUB / item.get("name", "")
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

    for needed in ("proposal.pdf", "INSTALL.md"):
        if not any(i.get("name") == needed for i in att.get("attachments", [])):
            fail(f"attachment manifest does not include {needed}")

    # Not guarded on `not problems`. The budget is the one check whose answer
    # a human has to act on days ahead of the deadline, and hiding it behind
    # an unrelated missing PDF is how it stays unanswered until W5.
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
spec = subprocess.run(
    [sys.executable, str(REPO / "scripts" / "check_competition_spec.py")],
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
