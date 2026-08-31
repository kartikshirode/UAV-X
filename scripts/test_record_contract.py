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


if __name__ == "__main__":
    sys.exit(main())
