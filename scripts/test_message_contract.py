#!/usr/bin/env python3
"""Prove exact ROS message contracts reject incompatible interfaces."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECKER = REPO / "scripts" / "check_message_contract.py"
sys.path.insert(0, str(REPO / "scripts"))
from check_message_contract import expected_contracts  # noqa: E402


def write_contracts(root: Path) -> None:
    for name, lines in expected_contracts().items():
        (root / f"{name}.msg").write_text("\n".join(lines) + "\n",
                                          encoding="utf-8")


def run(root: Path) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(CHECKER), "--actual-dir",
                           str(root)], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="uavx-msg-contract-"))
    failures = 0
    try:
        write_contracts(root)
        rc, _ = run(root)
        if rc == 0:
            print("ok    exact message definitions")
        else:
            print(f"FAIL  exact definitions returned {rc}")
            failures += 1

        packet = root / "SwarmPacket.msg"
        packet.write_text(packet.read_text(encoding="utf-8").replace(
            "uint32 sequence", "uint16 sequence"), encoding="utf-8")
        rc, out = run(root)
        if rc == 1 and "differs at line" in out:
            print("ok    changed field width rejected")
        else:
            print("FAIL  changed field width was not rejected")
            failures += 1

        write_contracts(root)
        role = root / "RoleAssignment.msg"
        role.write_text(role.read_text(encoding="utf-8").replace(
            "uint8 RELAY=1", "uint8 RELAY=2"), encoding="utf-8")
        rc, out = run(root)
        if rc == 1 and "RoleAssignment differs" in out:
            print("ok    changed role constant rejected")
        else:
            print("FAIL  changed role constant was not rejected")
            failures += 1

        write_contracts(root)
        (root / "RunMetrics.msg").unlink()
        rc, out = run(root)
        if rc == 1 and "RunMetrics cannot be read" in out:
            print("ok    missing message rejected")
        else:
            print("FAIL  missing message was not rejected")
            failures += 1
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        return 1
    print("message contract fixtures passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
