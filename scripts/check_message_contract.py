#!/usr/bin/env python3
"""Compare the built ROS message interfaces with the frozen design.

Round 8 found that chunk 1.1 checked only whether five names resolved and
whether four SwarmPacket field names appeared somewhere in one interface.
A changed integer width, a missing constant or a different field order still
passed. The architecture owns the definitions, so this checker reads its five
message blocks and compares the generated interfaces line for line.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ARCHITECTURE = REPO / "stage-1" / "architecture.md"
MESSAGES = ("SwarmPacket", "Hello", "LinkState", "RoleAssignment",
            "RunMetrics")


def normalized(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(re.sub(r"\s+", " ", line))
    return lines


def expected_contracts() -> dict[str, list[str]]:
    body = ARCHITECTURE.read_text(encoding="utf-8")
    out = {}
    for name in MESSAGES:
        pattern = rf"\*\*`{re.escape(name)}\.msg`\*\*\s*\n+```(?:msg)?\n(.*?)```"
        match = re.search(pattern, body, re.S)
        if not match:
            raise ValueError(f"architecture.md has no fenced {name}.msg definition")
        out[name] = normalized(match.group(1))
    return out


def actual_contract(name: str, actual_dir: Path | None) -> tuple[int, str]:
    if actual_dir is not None:
        path = actual_dir / f"{name}.msg"
        if not path.is_file():
            return 2, f"no {path}"
        return 0, path.read_text(encoding="utf-8")
    try:
        proc = subprocess.run(
            ["ros2", "interface", "show", f"uavx_msgs/msg/{name}"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, str(exc)
    if proc.returncode != 0:
        return 2, (proc.stderr or proc.stdout).strip()
    return 0, proc.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-dir", type=Path,
                        help="read Name.msg files here instead of calling ros2")
    args = parser.parse_args()
    try:
        expected = expected_contracts()
    except (OSError, ValueError) as exc:
        print(f"  FAIL  cannot read the frozen message contract: {exc}")
        return 2

    failures = 0
    for name in MESSAGES:
        rc, text = actual_contract(name, args.actual_dir)
        if rc:
            print(f"  FAIL  uavx_msgs/msg/{name} cannot be read: {text}")
            failures += 1
            continue
        got = normalized(text)
        want = expected[name]
        if got != want:
            first = next((i for i, pair in enumerate(zip(got, want), 1)
                          if pair[0] != pair[1]), min(len(got), len(want)) + 1)
            got_line = got[first - 1] if first <= len(got) else "<missing>"
            want_line = want[first - 1] if first <= len(want) else "<no line>"
            print(f"  FAIL  uavx_msgs/msg/{name} differs at line {first}: "
                  f"got {got_line!r}, wanted {want_line!r}")
            failures += 1
        else:
            print(f"  ok    uavx_msgs/msg/{name}, {len(want)} contract lines")

    if failures:
        return 1
    print("all five generated message interfaces match architecture.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
