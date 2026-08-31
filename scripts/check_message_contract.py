#!/usr/bin/env python3
"""Compare the built ROS message interfaces with the frozen design.

Round 8 found that chunk 1.1 checked only whether five names resolved and
whether four SwarmPacket field names appeared somewhere in one interface.
A changed integer width, a missing constant or a different field order still
passed. The architecture owns the definitions, so this checker reads its five
message blocks and compares the generated interfaces line for line.

Chunk 1.1, on the first real build. The text pass above proves less than it
looks. `ros2 interface show` prints the installed .msg byte for byte, and
under `colcon build --symlink-install` the installed file is a symlink back
into the source tree, so the comparison is the source file against itself. It
catches drift between architecture.md and the .msg, which is worth having, and
it says nothing about what the code generator made of either.

So `--generated` adds a second pass that imports the built Python classes and
reads `get_fields_and_field_types()` and the class constants. That is the
artifact every node will actually send, and standing rule 5 is that we assert
on artifacts rather than on metadata. The gate runs both.
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


# What rosidl reports for each spelling architecture.md uses. Anything not
# listed passes through unchanged, which covers the integer types.
GENERATED_TYPE = {
    "float64": "double",
    "float32": "float",
    "bool": "boolean",
}


def as_generated_type(declared: str) -> str:
    """Render one architecture.md type the way rosidl reports it."""
    if declared.endswith("[]"):
        inner = as_generated_type(declared[:-2])
        return f"sequence<{inner}>"
    if declared.endswith("]") and "[" in declared:
        base, _, size = declared[:-1].partition("[")
        return f"{as_generated_type(base)}[{size}]"
    return GENERATED_TYPE.get(declared, declared)


def split_contract(lines: list) -> tuple:
    """Separate a normalized contract into its fields and its constants.

    A constant line carries an `=` and an upper case name; everything else is
    a field. Both orders matter, so both come back as lists.
    """
    fields, constants = [], []
    for line in lines:
        declared, _, rest = line.partition(" ")
        if "=" in rest:
            name, _, value = rest.partition("=")
            constants.append((name.strip(), value.strip(), declared))
        else:
            fields.append((rest.strip(), as_generated_type(declared)))
    return fields, constants


def check_generated(expected: dict) -> int:
    """Compare the built Python classes with the frozen definitions.

    Returns the number of failures. Refuses to pass when the package cannot
    be imported: a generated-interface check that quietly skips itself is the
    Gazebo failure again, green on a machine where the thing does not exist.
    """
    try:
        import importlib
        module = importlib.import_module("uavx_msgs.msg")
    except ImportError as exc:
        print(f"  FAIL  cannot import uavx_msgs.msg, so nothing here checks "
              f"the generated interface: {exc}")
        return 1

    failures = 0
    for name in MESSAGES:
        cls = getattr(module, name, None)
        if cls is None:
            print(f"  FAIL  uavx_msgs.msg has no generated {name}")
            failures += 1
            continue
        want_fields, want_constants = split_contract(expected[name])
        got_fields = list(cls.get_fields_and_field_types().items())
        if got_fields != want_fields:
            print(f"  FAIL  generated {name} fields are {got_fields}, "
                  f"architecture.md says {want_fields}")
            failures += 1
            continue
        wrong = [(cname, getattr(cls, cname, "<missing>"), value)
                 for cname, value, _ in want_constants
                 if str(getattr(cls, cname, "<missing>")) != value]
        if wrong:
            print(f"  FAIL  generated {name} constants disagree: {wrong}")
            failures += 1
            continue
        print(f"  ok    generated {name}, {len(want_fields)} fields, "
              f"{len(want_constants)} constants")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actual-dir", type=Path,
                        help="read Name.msg files here instead of calling ros2")
    parser.add_argument("--generated", action="store_true",
                        help="also compare the built Python classes. Needs a "
                             "sourced overlay, and fails if it cannot import "
                             "rather than skipping.")
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

    if args.generated:
        print()
        failures += check_generated(expected)

    if failures:
        return 1
    if args.generated:
        print("all five message definitions and their generated classes "
              "match architecture.md")
    else:
        print("all five message definitions match architecture.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
