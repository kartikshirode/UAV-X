#!/usr/bin/env python3
"""Exercise the scenario input contract with one good file and bad variants.

Round 8 found that check_scenario.py only compared the duration and vehicle
count when a caller supplied flags. A duplicate vehicle or an event with no
target could therefore reach the runner with no defined meaning. Each case
starts from the same valid document and checks the diagnostic, so a missing
dependency is not counted as a caught defect.
"""

import copy
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_scenario.py"


BASE = {
    "name": "fixture",
    "seed": 17,
    "duration_s": 60,
    "vehicles": ["uav_1", "uav_2", "uav_3", "uav_4"],
    "injected_events": [{"type": "kill", "target": "uav_2", "at_s": 30}],
    "headless": True,
}


def run(path: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(CHECKER), str(path)],
                          capture_output=True, text=True, cwd=str(REPO))
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("FAIL: pyyaml is not installed, so fixtures cannot run")
        return 1

    cases = [
        ("valid scenario", lambda d: None, 0, "is the scenario"),
        ("duplicate vehicle", lambda d: d["vehicles"].append("uav_4"), 1,
         "duplicate ids"),
        ("unknown event type", lambda d: d["injected_events"][0].update(type="teleport"),
         1, "unknown type"),
        ("event outside duration", lambda d: d["injected_events"][0].update(at_s=61),
         1, "outside the scenario"),
        ("non-headless scenario", lambda d: d.update(headless=False), 1,
         "headless must be true"),
        ("scenario name mismatch", lambda d: d.update(name="other"), 1,
         "does not match"),
    ]

    failures = 0
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        for label, mutate, wanted_rc, needle in cases:
            doc = copy.deepcopy(BASE)
            mutate(doc)
            path = root / "fixture.yaml"
            path.write_text(yaml.safe_dump(doc), encoding="utf-8")
            rc, output = run(path)
            if rc != wanted_rc or needle not in output:
                failures += 1
                print(f"FAIL {label}: rc={rc}, wanted {wanted_rc}, output={output!r}")
            else:
                print(f"ok   {label}")

    if failures:
        print(f"{failures} scenario fixture(s) failed")
        return 1
    print("scenario contract fixtures passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
