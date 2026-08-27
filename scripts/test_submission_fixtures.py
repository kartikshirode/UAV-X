#!/usr/bin/env python3
"""Prove W5 rejects a tampered package.

Round 4 asked for exactly this: mutate one byte of the source archive and one
required run record, show the checker says no, then test the attachment budget
with an oversized package. Every one of those paths was previously either absent
or satisfied by a matching pair of invented strings.

Each fixture builds a complete submission directory, breaks one thing, runs the
real `scripts/check_submission.py` against it and requires the specific
complaint. The package is deliberately incomplete in other ways, since there is
no proposal PDF or demo video yet, so a fixture asserts on its own message
rather than on the exit code alone.

    python3 scripts/test_submission_fixtures.py

Exit 0 if every fixture behaved as specified.
"""

import hashlib
import json
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
ARCHIVE_PATHS = ("uavx_ws/", "scenarios/", "scripts/", "stage-1/")

CASES: list = []


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, text=True, check=True).stdout


def build_package(dest: Path) -> dict:
    """A submission directory that is internally consistent before tampering."""
    dest.mkdir(parents=True, exist_ok=True)
    commit = git("rev-parse", "HEAD").strip()

    archive = dest / "uavx-source.zip"
    subprocess.run(
        ["git", "-C", str(REPO), "archive", "--format=zip",
         "--prefix=uavx-source/", "-o", str(archive), commit, "--",
         "uavx_ws", "scenarios", "scripts", "stage-1"],
        capture_output=True, check=False)
    if not archive.is_file() or archive.stat().st_size == 0:
        # uavx_ws does not exist yet, so name only the paths that do.
        present = [p for p in ("scenarios", "scripts", "stage-1")
                   if (REPO / p).is_dir()]
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

    (dest / "fresh-install-receipt.json").write_text(json.dumps({
        "commit_sha": commit,
        "archive_sha256": sha256_bytes(archive.read_bytes()),
        "result": "pass",
    }, indent=2), encoding="utf-8")

    (dest / "human-preflight.json").write_text(json.dumps({
        "registered": {"done": "2026-08-27", "email": "someone@example.com"},
        "eligibility": {"done": "2026-08-27",
                        "declaration": "no attachment to PUSHPAK, the Drone "
                                       "Centre, IIT Bombay, IISER Bhopal or VJTI"},
        "clarification_channel": {"done": "2026-08-27"},
        "organiser_email": {"sent": "2026-08-27", "answers": "pending",
                            "fallback": "repo link plus zip, video under 180 s"},
        "delivery": {"attachment_limit_mb": 25,
                     "route": "attachments to the organiser address",
                     "fallback": "shared drive link if over budget"},
    }, indent=2), encoding="utf-8")
    shutil.copy(REPO / "submission" / "human-preflight.schema.json",
                dest / "human-preflight.schema.json")

    (dest / "INSTALL.md").write_text("install " * 300, encoding="utf-8")

    runs = dest / "runs"
    runs.mkdir(exist_ok=True)
    manifest_runs = {}
    for scenario in ("survey_baseline", "relay_required", "direct_only",
                     "relay_kill", "encounter", "encounter_noyield",
                     "mission_integrated"):
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
        (runs / f"{rid}.jsonl").write_text(json.dumps(rec), encoding="utf-8")
        manifest_runs[scenario] = str((runs / f"{rid}.jsonl").resolve())

    (dest / "evidence-manifest.json").write_text(
        json.dumps({"runs": manifest_runs}, indent=2), encoding="utf-8")

    attachments = []
    for name in ("INSTALL.md", "uavx-source.zip"):
        p = dest / name
        attachments.append({"name": name, "bytes": p.stat().st_size,
                            "sha256": sha256_bytes(p.read_bytes())})
    write_attachments(dest, attachments)
    return {"commit": commit, "tree": tree, "archive": archive, "runs": runs,
            "manifest_runs": manifest_runs}


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


def case(name: str, expect: str):
    def deco(fn):
        CASES.append((name, expect, fn))
        return fn
    return deco


@case("untouched package", "")
def _clean(dest, built):
    return


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
    r = json.loads((dest / "fresh-install-receipt.json").read_text(encoding="utf-8"))
    r["archive_sha256"] = man["archive_sha256"]
    (dest / "fresh-install-receipt.json").write_text(json.dumps(r, indent=2),
                                                     encoding="utf-8")


@case("a required run record emptied", "fails the run-record schema")
def _empty_run(dest, built):
    Path(built["manifest_runs"]["relay_kill"]).write_text("{}", encoding="utf-8")


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
    (dest / "INSTALL.md").write_text("something else entirely", encoding="utf-8")


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
               UAVX_RUNS_DIR=str(dest / "runs"))
    p = subprocess.run([sys.executable, str(CHECKER)], capture_output=True,
                       text=True, timeout=600, env=env, cwd=str(REPO))
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    if subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                      capture_output=True).returncode:
        print("FAIL  not a git repository, so no commit to freeze against")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="uavx-sub-"))
    failures = 0

    # The untouched package still fails, because there is no proposal and no
    # video yet. What matters is that it does not fail for any of the reasons
    # below, which is what makes each fixture's message meaningful.
    baseline_dir = tmp / "baseline"
    build_package(baseline_dir)
    _, baseline_out = run_checker(baseline_dir)

    for name, expect, tamper in CASES:
        dest = tmp / name.replace(" ", "_").replace(",", "")
        built = build_package(dest)
        tamper(dest, built)
        rc, out = run_checker(dest)

        if not expect:
            good = rc != 0
            why = "even an untampered package is incomplete until W5 fills it in"
            print(f"  {'ok  ' if good else 'FAIL'}  {name:<52} rc={rc}")
            if not good:
                failures += 1
                print(f"           {why}")
            continue

        said = expect in out
        clean_before = expect not in baseline_out
        good = rc == 1 and said and clean_before
        print(f"  {'ok  ' if good else 'FAIL'}  {name:<52} rc={rc}")
        if not good:
            failures += 1
            if not said:
                print(f"           never said {expect!r}")
            elif not clean_before:
                print(f"           says {expect!r} even untampered, so the "
                      f"fixture proves nothing")
            for line in [x for x in out.splitlines() if "FAIL" in x][:4]:
                print(f"           {line.strip()}")

    print()
    if failures:
        print(f"FAILED: {failures} of {len(CASES)} submission fixtures behaved wrongly")
        return 1
    print(f"all {len(CASES)} submission fixtures behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
