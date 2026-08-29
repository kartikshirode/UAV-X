#!/usr/bin/env python3
"""Prove W5 accepts a real package and rejects a tampered one.

Round 4 asked for the tamper cases. Round 5 finding 2 found the hole underneath
them: the suite had never once seen a package the checker accepts. Its baseline
was missing a run record, a proposal and a video, the clean case counted any
nonzero exit as success, and nothing asserted the documented exit 2 or exit 0.
Every negative case was therefore mutated from something already broken, and a
permanently failing W5 checker would have passed the whole suite.

So the oracle comes first. A complete package must reach exit 2, the same
package with a send receipt must reach exit 0, and only then is anything
mutated. Each mutation is cloned from that passing baseline and must produce
exit 1 with its own diagnostic. Knock-on complaints are fine and are counted;
a complaint the baseline already made is not, because that would mean the case
was measuring pre-existing breakage again.

Two things are stubbed at the process boundary and nowhere deeper: uavx_eval,
which does not exist until W2, and the live competition-record check, which is
a network call against a target the organisers edit.

    python3 scripts/test_submission_fixtures.py

Exit 0 if every check behaved as specified.
"""

import hashlib
import json
from datetime import date
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_submission.py"

sys.path.insert(0, str(REPO / "scripts"))
import fixture_assets                                       # noqa: E402
import check_submission_const as K                          # noqa: E402

# Round 5 finding 2: the fixture hard-coded seven scenarios while the checker
# required eight, so link_loss was added to one and not the other and the
# baseline silently stopped matching. Read the list from the checker.
SCENARIOS = K.REQUIRED_RUNS
SECTIONS = K.REQUIRED_SECTIONS

TODAY = date.today().isoformat()

CASES: list = []


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout


# Round 6 finding 5: the schema defines observations, handback and relay_slot
# and required none of them, so this fixture omitted all three and the baseline
# it calls valid was a package whose communication evidence was absent. The
# schema requires them per scenario now, and the baseline has to carry what a
# real run of that scenario would.
OUTAGE_SCENARIOS = {"relay_kill", "link_loss", "mission_integrated", "queue_drain"}

# The two scenarios a gate captures a ROS graph during. Round 6 finding 6: the
# run record has to name the graph it was accepted against, or a reused
# snapshot from the previous scenario is indistinguishable from a fresh one.
GRAPH_SCENARIOS = {"relay_required", "mission_integrated"}

# Round 6 finding 1: the old baseline wrote archive_sha256, commit_sha and
# result: pass straight into the receipt and expected the package to pass. It
# was the false green rather than a test of it. The executor is stubbed at the
# process boundary, the same way the spec checker and uavx_eval are, and what
# it leaves behind is what a real run of scripts/fresh_install.sh leaves
# behind: a transcript with one completed section per step and a smoke run id
# that appears in it.
FRESH_STEPS = json.loads((REPO / "scripts" / "rehearsal-steps.json")
                         .read_text(encoding="utf-8"))["archive_install"]


def stub_fresh_install(dest: Path, archive_sha: str, commit: str) -> None:
    smoke = "smoke_20260926T000100"
    lines = [f"target_kind=clean-prefix", "target=/tmp/uavx-fresh-install",
             f"archive_sha256={archive_sha}", f"commit={commit}",
             "started=2026-09-26T00:00:00Z", ""]
    for label in FRESH_STEPS:
        lines += ["", f"=== {label}", "$ some command", "output", "[exit 0]"]
    lines += ["", "=== installed version set", f"run_id={smoke}", "[exit 0]"]
    transcript = dest / "fresh-install-transcript.log"
    transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

    (dest / "fresh-install-receipt.json").write_text(json.dumps({
        "kind": "archive-install",
        "result": "pass",
        "target": {"kind": "clean-prefix", "name": "/tmp/uavx-fresh-install"},
        "archive_sha256": archive_sha,
        "commit_sha": commit,
        "started": "2026-09-26T00:00:00Z",
        "done": "2026-09-26",
        "ended": "2026-09-26T00:40:00Z",
        "transcript": "submission/fresh-install-transcript.log",
        "transcript_sha256": sha256_bytes(transcript.read_bytes()),
        "smoke_run_id": smoke,
        "steps_run": list(FRESH_STEPS),
    }, indent=2), encoding="utf-8")


def evidence_blocks(scenario: str) -> dict:
    out = {}
    if scenario in GRAPH_SCENARIOS:
        out["graph_snapshot_sha256"] = hashlib.sha256(
            scenario.encode()).hexdigest()
    if scenario not in OUTAGE_SCENARIOS:
        return out

    ids = [f"uav_{n}:{i}" for n in (3, 4) for i in range(225)]
    out.update({
        "observations": {
            "generated_ids": ids,
            "delivered_ids": list(ids),
            "generated": len(ids),
            "unique_delivered": len(ids),
            "duplicated": 0,
            "expired": 0,
            "evicted": 0,
            "peak_queue_depth": 450,
            "backlog_drain_s": 2.1,
            "control_queue_max_delay_s": 0.004,
            "missing_ids": [],
            "unexpected_ids": [],
        },
        "relay_slot": {
            "commanded": [317.3, -36.8, 75.0],
            "clearance_m": 52.4,
            "band_reserved": True,
            "raised_from": None,
        },
    })
    if scenario == "link_loss":
        out["handback"] = {
            "epoch": 1,
            "epoch_owner": "uav_4",
            "staying_member": "uav_4",
            "prepared_path": ["uav_4", "uav_2", "uav_1", "gcs"],
            "prepared_path_computations": 2,
            "confirmed_observation_id": "uav_4:118",
            "release_sender": "uav_4",
            "confirmed_at": 268.4,
            "release_at": 269.1,
            "observation_gap_count": 0,
            "acknowledged_ids": ["uav_4:118"],
        }
    return out


def build_package(dest: Path) -> dict:
    """A submission directory that is internally consistent before tampering."""
    dest.mkdir(parents=True, exist_ok=True)
    commit = git("rev-parse", "HEAD").strip()

    archive = dest / "uavx-source.zip"
    subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=zip",
         "--prefix=uavx-source/", "-o", str(archive), commit, "--",
         "uavx_ws", "scenarios", "scripts", "stage-1", "LICENSE",
         "THIRD-PARTY.md"],
        capture_output=True, check=False)
    if not archive.is_file() or archive.stat().st_size == 0:
        # uavx_ws does not exist yet, so name only the paths that do.
        present = [p for p in ("scenarios", "scripts", "stage-1", "LICENSE",
                               "THIRD-PARTY.md") if (REPO / p).exists()]
        subprocess.run(
            ["git", "-C", str(REPO), "archive", "--format=zip",
             "--prefix=uavx-source/", "-o", str(archive), commit, "--", *present],
            capture_output=True, check=True)

    tree = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "source_tree_hash.py"),
         "--ref", commit], capture_output=True, text=True, check=True
    ).stdout.strip()

    (dest / "source-manifest.json").write_text(json.dumps({
        "commit_sha": commit,
        "archive": archive.name,
        "archive_sha256": sha256_bytes(archive.read_bytes()),
        "source_tree_sha256": tree,
        "built_by": "scripts/freeze_source.sh",
    }, indent=2), encoding="utf-8")

    stub_fresh_install(dest, sha256_bytes(archive.read_bytes()), commit)

    (dest / "human-preflight.json").write_text(json.dumps({
        "registered": {"done": "2026-08-27", "email": "someone@example.com"},
        "eligibility": {"done": "2026-08-27",
                        "declaration": "no attachment to PUSHPAK, the Drone "
                                       "Centre, IIT Bombay, IISER Bhopal or VJTI"},
        "clarification_channel": {"done": "2026-08-27",
                                  "last_checked": TODAY},
        "organiser_email": {"sent": "2026-08-27", "answers": "pending",
                            "fallback": "repo link plus zip, video under 180 s",
                            "last_checked": TODAY},
        "delivery": {"attachment_limit_mb": 25,
                     "route": "attachments to the organiser address",
                     "fallback": "shared drive link if over budget"},
        "compliance_review": {
            "done": TODAY, "by": "fixture",
            "statement": "Stage 1 and Stage 2 are simulation only, so no "
                         "operational permission attaches; the DGCA and "
                         "Digital Sky obligations are recorded for Stage 3."},
    }, indent=2), encoding="utf-8")
    shutil.copy(REPO / "submission" / "human-preflight.schema.json",
                dest / "human-preflight.schema.json")

    (dest / "INSTALL.md").write_text("install " * 300, encoding="utf-8")

    runs = dest / "runs"
    runs.mkdir(exist_ok=True)
    manifest_runs = {}
    for scenario in SCENARIOS:
        rid = f"{scenario}_20260926T000000"
        rec = {
            "run_id": rid,
            "scenario_path": f"scenarios/{scenario}.yaml",
            "scenario_sha256": "0" * 64,
            "seed": 1,
            "commit_sha": commit,
            "started_at": "2026-09-26T00:00:00",
            "ended_at": "2026-09-26T00:05:00",
            "completion": "complete",
            "vehicle_ids_observed": ["uav_1", "uav_2", "uav_3", "uav_4"],
            "pose_sample_count": 4800,
            "versions": lock_versions(),
            "metrics": {},
            "app_packets_sent_by_node": {"uav_4": 1200},
            "app_packets_delivered_by_node": {"uav_4": 1180},
            "injected_events": [],
            "requested_duration_s": 240,
            "elapsed_sim_s": 240,
            "source_tree_sha256": tree,
        }
        rec.update(evidence_blocks(scenario))
        (runs / f"{rid}.jsonl").write_text(json.dumps(rec), encoding="utf-8")
        manifest_runs[scenario] = str((runs / f"{rid}.jsonl").resolve())

    (dest / "evidence-manifest.json").write_text(
        json.dumps({"runs": manifest_runs}, indent=2), encoding="utf-8")

    # The deliverables themselves. A baseline without these is not a package
    # the checker could ever accept, and every negative case below is only
    # meaningful because it is mutated from one that is.
    fixture_assets.write_pdf(
        dest / "proposal.pdf",
        fixture_assets.proposal_pages(sorted(manifest_runs_ids(manifest_runs)),
                                      SECTIONS, K.REQUIRED_COMPLIANCE))
    if not fixture_assets.write_video(dest / "demo.mp4", 60):
        raise RuntimeError("ffmpeg is not available, so no valid baseline "
                           "package can be built. A suite that silently skips "
                           "its own oracle is the bug this file exists to stop.")

    attachments = []
    for name in ("proposal.pdf", "INSTALL.md", "demo.mp4", archive.name):
        p = dest / name
        attachments.append({"name": name, "bytes": p.stat().st_size,
                            "sha256": sha256_bytes(p.read_bytes())})
    write_attachments(dest, attachments)
    return {"commit": commit, "tree": tree, "archive": archive, "runs": runs,
            "manifest_runs": manifest_runs}


def manifest_runs_ids(manifest_runs: dict) -> list:
    return [Path(v).stem for v in manifest_runs.values()]


def stub_uavx_eval(root: Path) -> Path:
    """Stub the external run validator at its process boundary, not inside.

    uavx_eval does not exist until W2. Faking it any deeper than the subprocess
    call would mean the fixture stopped exercising the code path W5 actually
    takes.
    """
    pkg = root / "stub" / "uavx_eval"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "check.py").write_text(
        "import sys\n"
        "print('stub uavx_eval.check: accepted', sys.argv[1:])\n"
        "sys.exit(0)\n", encoding="utf-8")

    # The live competition record is a network call against a moving target.
    # W5 must make it for real. A fixture must not, or the suite starts failing
    # whenever the organisers edit a sentence, which is a thing they do.
    (root / "stub_spec.py").write_text(
        "print('  ok    stub spec check')\n", encoding="utf-8")
    return root / "stub"


def write_attachments(dest: Path, attachments: list) -> None:
    (dest / "attachment-manifest.json").write_text(json.dumps({
        "attachments": attachments,
        "route": "attachments to the organiser address",
    }, indent=2), encoding="utf-8")


def lock_versions() -> dict:
    """The enforced half of versions.lock, which is what a run record carries."""
    out = {}
    for line in (REPO / "stage-1" / "setup" / "versions.lock").read_text(
            encoding="utf-8").splitlines():
        m = re.match(r"^([a-z0-9_]+)=(.*)$", line)
        if m and not m.group(1).startswith("observed_"):
            out[m.group(1)] = m.group(2)
    return out


def refresh_attachment(dest: Path, name: str) -> None:
    """Re-hash one entry so a mutation trips its own check and nothing else."""
    man = json.loads((dest / "attachment-manifest.json").read_text(encoding="utf-8"))
    p = dest / name
    for item in man["attachments"]:
        if item["name"] == name:
            item["bytes"] = p.stat().st_size
            item["sha256"] = sha256_bytes(p.read_bytes())
    (dest / "attachment-manifest.json").write_text(json.dumps(man, indent=2),
                                                   encoding="utf-8")


def case(name: str, expect: str):
    def deco(fn):
        CASES.append((name, expect, fn))
        return fn
    return deco


@case("one byte flipped in the archive file",
      "The file on disk is not the one that was frozen")
def _byte(dest, built):
    data = bytearray(built["archive"].read_bytes())
    data[len(data) // 2] ^= 0x01
    built["archive"].write_bytes(bytes(data))


@case("a file inside the archive replaced, archive hash fixed up",
      "differ from commit")
def _repack(dest, built):
    src = built["archive"]
    tmp = src.with_suffix(".rebuilt.zip")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("scripts/check_geometry.py"):
                data = b"# swapped after the freeze\n"
            zout.writestr(item, data)
    tmp.replace(src)
    man = json.loads((dest / "source-manifest.json").read_text(encoding="utf-8"))
    man["archive_sha256"] = sha256_bytes(src.read_bytes())
    (dest / "source-manifest.json").write_text(json.dumps(man, indent=2),
                                               encoding="utf-8")
    stub_fresh_install(dest, man["archive_sha256"], built["commit"])
    refresh_attachment(dest, src.name)


# Round 6 finding 1, the case the old fixture was written as.
@case("a hand-written three field install receipt", "receipt kind is")
def _typed_install(dest, built):
    (dest / "fresh-install-receipt.json").write_text(json.dumps({
        "commit_sha": built["commit"],
        "archive_sha256": sha256_bytes(built["archive"].read_bytes()),
        "result": "pass",
    }, indent=2), encoding="utf-8")


@case("an install receipt naming no clean target", "names no clean target")
def _no_target(dest, built):
    r = json.loads((dest / "fresh-install-receipt.json").read_text(encoding="utf-8"))
    del r["target"]
    (dest / "fresh-install-receipt.json").write_text(json.dumps(r, indent=2),
                                                     encoding="utf-8")


@case("an install transcript edited after the fact", "does not hash to what")
def _edited_transcript(dest, built):
    t = dest / "fresh-install-transcript.log"
    t.write_text(t.read_text(encoding="utf-8") + "\nlooks fine to me\n",
                 encoding="utf-8")


@case("an install that built and never flew", "records no smoke run id")
def _no_smoke(dest, built):
    r = json.loads((dest / "fresh-install-receipt.json").read_text(encoding="utf-8"))
    r["smoke_run_id"] = ""
    (dest / "fresh-install-receipt.json").write_text(json.dumps(r, indent=2),
                                                     encoding="utf-8")


@case("a required run record emptied", "fails the run-record schema")
def _empty_run(dest, built):
    Path(built["manifest_runs"]["relay_kill"]).write_text("{}", encoding="utf-8")


# Round 6 finding 5. The three evidence blocks were defined and required by
# nothing, so a run could omit the entire communication result and validate.
@case("the outage run with its observation evidence removed",
      "missing 'observations'")
def _no_observations(dest, built):
    p = Path(built["manifest_runs"]["link_loss"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    del rec["observations"]
    p.write_text(json.dumps(rec), encoding="utf-8")


@case("the handback run with no epoch owner", "missing 'epoch_owner'")
def _no_owner(dest, built):
    p = Path(built["manifest_runs"]["link_loss"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    del rec["handback"]["epoch_owner"]
    p.write_text(json.dumps(rec), encoding="utf-8")


# The whole point of carrying both id sets rather than two counts.
@case("delivered counts that match while the ids do not",
      "delivered_ids")
def _wrong_ids(dest, built):
    p = Path(built["manifest_runs"]["queue_drain"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["observations"]["delivered_ids"] = [
        f"uav_9:{i}" for i in range(len(rec["observations"]["generated_ids"]))]
    p.write_text(json.dumps(rec), encoding="utf-8")


@case("a run produced from different source", "The evidence and the code do not match")
def _wrong_source(dest, built):
    p = Path(built["manifest_runs"]["mission_integrated"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["source_tree_sha256"] = "9" * 64
    p.write_text(json.dumps(rec), encoding="utf-8")


@case("a run whose injected event never fired", "never fired")
def _unfired(dest, built):
    p = Path(built["manifest_runs"]["relay_kill"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["injected_events"] = [{"type": "kill", "target": "uav_2",
                               "requested_t": 70, "observed_t": None}]
    p.write_text(json.dumps(rec), encoding="utf-8")


@case("a run cut short of its requested duration", "of a requested")
def _short(dest, built):
    p = Path(built["manifest_runs"]["survey_baseline"])
    rec = json.loads(p.read_text(encoding="utf-8"))
    rec["elapsed_sim_s"] = 20
    p.write_text(json.dumps(rec), encoding="utf-8")


@case("the integrated mission missing from the evidence manifest",
      "names no run for mission_integrated")
def _missing_run(dest, built):
    m = json.loads((dest / "evidence-manifest.json").read_text(encoding="utf-8"))
    del m["runs"]["mission_integrated"]
    (dest / "evidence-manifest.json").write_text(json.dumps(m, indent=2),
                                                 encoding="utf-8")


@case("attachments over the recorded budget with no other route",
      "is how a submission fails to arrive")
def _oversized(dest, built):
    big = dest / "demo.mp4"
    big.write_bytes(b"\0" * (30 * 1024 * 1024))
    write_attachments(dest, [
        {"name": "INSTALL.md", "bytes": (dest / "INSTALL.md").stat().st_size,
         "sha256": sha256_bytes((dest / "INSTALL.md").read_bytes())},
        {"name": "demo.mp4", "bytes": big.stat().st_size,
         "sha256": sha256_bytes(big.read_bytes())},
    ])


@case("an attachment swapped after the manifest was written",
      "does not match the hash in the attachment manifest")
def _swapped_attachment(dest, built):
    # Long enough to still be plausible instructions, so the only thing that
    # can fail is the hash. A fixture that trips three checks at once does not
    # tell you which one caught it.
    (dest / "INSTALL.md").write_text("different " * 300, encoding="utf-8")


# Round 6 finding 3, all three routes past the old check. The video that gets
# decoded and the video that gets sent were never required to be the same file.
@case("a text file delivered in place of the demo",
      "beside the demo video")
def _demo_txt(dest, built):
    txt = dest / "demo.txt"
    txt.write_text("the video is coming later", encoding="utf-8")
    man = json.loads((dest / "attachment-manifest.json").read_text(encoding="utf-8"))
    for item in man["attachments"]:
        if item["name"] == "demo.mp4":
            item["name"] = "demo.txt"
            item["bytes"] = txt.stat().st_size
            item["sha256"] = sha256_bytes(txt.read_bytes())
    (dest / "attachment-manifest.json").write_text(json.dumps(man, indent=2),
                                                   encoding="utf-8")


@case("a valid demo nobody listed for delivery",
      "not the one going in the email")
def _demo_unlisted(dest, built):
    man = json.loads((dest / "attachment-manifest.json").read_text(encoding="utf-8"))
    man["attachments"] = [i for i in man["attachments"] if i["name"] != "demo.mp4"]
    (dest / "attachment-manifest.json").write_text(json.dumps(man, indent=2),
                                                   encoding="utf-8")


@case("a good mp4 checked while a corrupt mkv is the one sent",
      "demo videos")
def _demo_two(dest, built):
    mkv = dest / "demo.mkv"
    mkv.write_bytes(b"\x1aE\xdf\xa3" + b"\x00" * 4096)
    man = json.loads((dest / "attachment-manifest.json").read_text(encoding="utf-8"))
    for item in man["attachments"]:
        if item["name"] == "demo.mp4":
            item["name"] = "demo.mkv"
            item["bytes"] = mkv.stat().st_size
            item["sha256"] = sha256_bytes(mkv.read_bytes())
    (dest / "attachment-manifest.json").write_text(json.dumps(man, indent=2),
                                                   encoding="utf-8")


@case("a second archive left in the directory", "expected exactly one source archive")
def _two_archives(dest, built):
    shutil.copy(built["archive"], dest / "uavx-source.tgz")


@case("a half-written human preflight receipt", "fails its schema")
def _bad_human(dest, built):
    d = json.loads((dest / "human-preflight.json").read_text(encoding="utf-8"))
    del d["delivery"]["route"]
    (dest / "human-preflight.json").write_text(json.dumps(d, indent=2),
                                               encoding="utf-8")


def run_checker(dest: Path) -> tuple:
    env = dict(os.environ, UAVX_SUBMISSION_DIR=str(dest),
               UAVX_RUNS_DIR=str(dest / "runs"),
               PYTHONPATH=str(stub_uavx_eval(dest)),
               UAVX_SPEC_CHECKER=str(dest / "stub_spec.py"))
    p = subprocess.run([sys.executable, str(CHECKER)], capture_output=True,
                       text=True, timeout=600, env=env, cwd=str(REPO))
    return p.returncode, p.stdout + p.stderr


def fails_in(out: str) -> list:
    return [l.strip() for l in out.splitlines() if "FAIL" in l]


def main() -> int:
    if subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                      capture_output=True).returncode:
        print("FAIL  not a git repository, so no commit to freeze against")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="uavx-sub-"))
    failures = 0

    # ---- the oracle. Everything below is only meaningful if this passes.
    print("--- the baseline must be a package the checker accepts")
    baseline = tmp / "baseline"
    built = build_package(baseline)
    rc, baseline_out = run_checker(baseline)
    baseline_fails = fails_in(baseline_out)
    good = rc == 2
    print(f"  {'ok  ' if good else 'FAIL'}  complete package, nothing sent yet     "
          f"rc={rc} (want 2)")
    if not good:
        failures += 1
        print("         a suite whose baseline is broken proves nothing about its")
        print("         negative cases, which is round 5 finding 2 exactly.")
        for line in fails_in(baseline_out)[:12]:
            print(f"         {line}")

    # ---- and the closed state, one field away from it.
    sent = tmp / "sent"
    build_package(sent)
    att = (sent / "attachment-manifest.json").read_bytes()
    (sent / "sent-receipt.json").write_text(json.dumps({
        "sent_at": "2026-09-26T09:00:00",
        "to": "pushpak_gc2026@aero.iitb.ac.in",
        "message_id": "<fixture@example.com>",
        "commit_sha": built["commit"],
        "attachment_manifest_sha256": hashlib.sha256(att).hexdigest(),
    }, indent=2), encoding="utf-8")
    rc, sent_out = run_checker(sent)
    good = rc == 0
    print(f"  {'ok  ' if good else 'FAIL'}  same package with a send recorded      "
          f"rc={rc} (want 0)")
    if not good:
        failures += 1
        for line in fails_in(sent_out)[:8]:
            print(f"         {line}")

    if failures:
        print("\nFAILED: the baseline is not valid, so no negative case below")
        print("        can distinguish its own mutation from the broken baseline.")
        return 1

    # ---- now the mutations, each cloned from the package that passes.
    print("\n--- each mutation, from the passing baseline")
    for name, expect, tamper in CASES:
        dest = tmp / name.replace(" ", "_").replace(",", "")
        built = build_package(dest)
        tamper(dest, built)
        rc, out = run_checker(dest)

        said = expect in out
        # The baseline is clean, so every complaint here was caused by this
        # mutation. A knock-on complaint is a consequence and is fine; a
        # complaint the baseline already made would mean the case was measuring
        # pre-existing breakage, which is round 5 finding 2.
        inherited = [l for l in fails_in(out) if l in baseline_fails]
        knock_on = [l for l in fails_in(out) if expect not in l]
        good = rc == 1 and said and not inherited
        note = f", {len(knock_on)} knock-on" if knock_on else ""
        print(f"  {'ok  ' if good else 'FAIL'}  {name:<52} rc={rc}{note}")
        if not good:
            failures += 1
            if rc != 1:
                print(f"           expected exit 1, got {rc}")
            elif not said:
                print(f"           never said {expect!r}")
            else:
                print(f"           {len(inherited)} failure(s) the baseline "
                      f"already had, so this case measures nothing:")
                for line in inherited[:4]:
                    print(f"             {line}")

    total = len(CASES) + 2
    print()
    if failures:
        print(f"FAILED: {failures} of {total} submission checks behaved wrongly")
        return 1
    print(f"all {total} submission checks behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
