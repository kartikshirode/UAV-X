#!/usr/bin/env python3
"""Prove run records reject broken time and provenance semantics."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "validate_record.py"
EXAMPLE = REPO / "scenarios" / "run-record.example.jsonl"

# Chunk 1.7. architecture.md calls this the checked example, and it was checked
# only against the schema. It carried gazebo11 where versions.lock enforces
# gazebo, and it was missing both event fields w1_runner asserts of every
# harness run, so the one record in the repository showing what a passing run
# looks like could not have passed. These are the requirements the gate makes
# of a harness record, copied from gate.sh w1_runner.
HARNESS_REQUIREMENTS = [
    "completion==complete",
    "clock_source==ros_sim_time",
    "pose_sample_count>=100",
    "vehicle_ids_observed==uav_1,uav_2,uav_3,uav_4",
    "injected_event_observed==true",
    "injected_event_count>=1",
    "resources.peak_rss_mib>0",
    "resources.peak_rss_mib<10500",
    "resources.swap_used_mib==0",
    "resources.samples>=10",
]


def run(path: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="uavx-record-contract-"))
    base = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    cases = [
        ("checked example", lambda rec: None, 0, "satisfies"),
        ("short complete run", lambda rec: rec.update(elapsed_sim_s=5), 1,
         "below the accepted 95 percent"),
        ("reversed wall times", lambda rec: rec.update(
            ended_at="2026-09-01T11:59:00Z"), 1, "earlier than started_at"),
        ("duplicate observed vehicle", lambda rec: rec.update(
            vehicle_ids_observed=["uav_1", "uav_1"]), 1, "duplicate ids"),
        ("wall clock metric source", lambda rec: rec.update(
            clock_source="wall_time"), 1, "not one of"),
        ("date-only wall timestamp", lambda rec: rec.update(
            started_at="2026-09-01"), 1, "RFC 3339 timestamp"),
        ("event requested at the run boundary", lambda rec: rec.update(
            injected_events=[{"type": "kill", "target": "uav_2",
                              "requested_t": 60, "observed_t": 60}]), 1,
         "requested_t is outside"),
        ("non-finite resource number", lambda rec: rec["resources"].update(
            peak_rss_mib=float("nan")), 1, "finite JSON number"),
    ]
    failures = 0
    try:
        for index, (name, mutate, want_rc, phrase) in enumerate(cases):
            record = json.loads(json.dumps(base))
            mutate(record)
            path = root / f"case-{index}.jsonl"
            path.write_text(json.dumps(record), encoding="utf-8")
            rc, out = run(path)
            if rc == want_rc and phrase in out:
                print(f"ok    {name}")
            else:
                print(f"FAIL  {name}: rc={rc}, wanted {want_rc} and {phrase!r}")
                failures += 1

        path = root / "two-lines.jsonl"
        path.write_text(EXAMPLE.read_text(encoding="utf-8") * 2,
                        encoding="utf-8")
        rc, out = run(path)
        if rc == 2 and "not valid JSON" in out:
            print("ok    a JSONL record cannot hide a second object")
        else:
            print("FAIL  a second JSON object was not rejected")
            failures += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        return 1
    print("run record contract fixtures passed")
    return 0


def check_example_against_the_gate() -> int:
    """The example must satisfy what the gate asks of a harness run."""
    import json as _json
    sys.path.insert(0, str(REPO / "scripts"))
    from validate_record import check_require                  # noqa: E402
    from check_submission_const import PEAK_RSS_CEILING_MIB    # noqa: E402

    record = _json.loads(EXAMPLE.read_text(encoding="utf-8"))
    bad = 0
    for expression in HARNESS_REQUIREMENTS:
        why = check_require(record, expression)
        if why:
            print(f"  FAIL  the checked example fails {expression}: {why}")
            bad += 1
    ceiling = record["resources"]["peak_rss_mib"]
    if ceiling >= PEAK_RSS_CEILING_MIB:
        print(f"  FAIL  the example claims {ceiling} MiB resident, at or over "
              f"the {PEAK_RSS_CEILING_MIB} MiB ceiling")
        bad += 1
    lock_path = REPO / "stage-1" / "setup" / "versions.lock"
    pins = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            pins[key.strip()] = value.strip()
    for key, want in pins.items():
        got = record["versions"].get(key)
        if got in (None, "fixture"):
            continue
        if got != want:
            print(f"  FAIL  the example pins {key}={got!r}, versions.lock "
                  f"says {want!r}")
            bad += 1
    if not bad:
        print(f"  ok    the checked example satisfies all "
              f"{len(HARNESS_REQUIREMENTS)} harness requirements and the lock")
    return bad


if __name__ == "__main__":
    # Both, and the exit code is the worse of the two. A suite that checks the
    # fixtures and not the example everyone copies is checking the easy half.
    rc = main()
    sys.exit(1 if check_example_against_the_gate() else rc)
