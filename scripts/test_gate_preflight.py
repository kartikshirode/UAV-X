#!/usr/bin/env python3
"""The gate's decision on each exit code the spec checker can return.

Round 6 finding 8: preflight ran `check_competition_spec.py --allow-offline`
under `|| gdie`, so exit 3 killed the week. 3 is the code that means "could not
reach the API, but a real online check happened inside the last seven days",
which is the documented fallback for the WSL DNS drops. The fallback existed in
the prose and could never once have run.

Nothing here reimplements the decision. The block between the two
`spec-decision` markers in gate.sh is lifted out verbatim and run against a
checker that returns whatever code the case asks for, so if somebody rewrites
that block these cases move with it or they fail.

    python3 scripts/test_gate_preflight.py

Exit 0 if every documented code produces the documented decision.
"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "scripts" / "gate.sh"

START = "# >>> spec-decision"
END = "# <<< spec-decision"

HARNESS = """set -uo pipefail
UAVX_REPO={repo}
python3() {{ {python} "$@"; }}
gsay() {{ printf 'SAY %s\\n' "$*"; }}
gdie() {{ printf 'DIE %s\\n' "$*"; exit 1; }}
spec_decision() {{
{block}
}}
spec_decision
"""

# A stand-in for check_competition_spec.py. It records the arguments it was
# handed, so a case can also prove the gate asked for the mode it claims.
STUB = """import sys, pathlib
pathlib.Path({log!r}).write_text(" ".join(sys.argv[1:]), encoding="utf-8")
sys.exit({code})
"""

# code, strict, expected gate result, what the code means
CASES = [
    (0, False, "pass", "record verified online"),
    (3, False, "pass", "offline, last real check inside seven days"),
    (1, False, "die", "record unreadable, or nobody has read it in over a week"),
    (2, False, "die", "the record changed"),
    (0, True, "pass", "send check, record verified online"),
    (3, True, "die", "send check cannot run offline"),
    (1, True, "die", "send check, record unreadable"),
    (2, True, "die", "send check, the record changed"),
]


def extract_block() -> str:
    text = GATE.read_text(encoding="utf-8")
    if START not in text or END not in text:
        sys.exit(f"gate.sh no longer carries the {START!r} markers, so this "
                 f"test would be checking a copy of the decision instead of "
                 f"the decision.")
    block = text.split(START, 1)[1].split(END, 1)[0]
    # `local` outside a function is an error, and the harness wraps the block in
    # one, so this is only true of the block as extracted.
    if "local spec_rc" not in block:
        sys.exit("the extracted block no longer captures the checker's exit "
                 "code into spec_rc. Read gate.sh before trusting this file.")
    return block.rstrip("\n")


def find_bash() -> str:
    """Git Bash, never the Windows WSL shim.

    `bash` on PATH under Windows resolves to C:/Windows/System32/bash.exe,
    which launches WSL and then fails with execvpe(/bin/bash). It cost an
    afternoon in round 4 and it is worth refusing by name.
    """
    for cand in (r"C:\\Program Files\\Git\\bin\\bash.exe",
                 r"C:\\Program Files\\Git\\usr\\bin\\bash.exe",
                 "/usr/bin/bash", "/bin/bash"):
        if Path(cand).is_file():
            return cand
    import shutil
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    sys.exit("no usable bash found")


def main() -> int:
    block = extract_block()
    bash = find_bash()
    bad = 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        script = tmp / "harness.sh"
        script.write_text(
            HARNESS.format(repo=str(REPO).replace("\\", "/"),
                           python=str(Path(sys.executable)).replace("\\", "/"),
                           block=block),
            encoding="utf-8", newline="\n")

        for code, strict, expect, why in CASES:
            log = tmp / f"args-{code}-{int(strict)}.txt"
            stub = tmp / f"spec-{code}-{int(strict)}.py"
            stub.write_text(
                STUB.format(log=str(log), code=code),
                encoding="utf-8", newline="\n")

            env = dict(os.environ)
            git_usr = Path(r"C:\Program Files\Git\usr\bin")
            if git_usr.is_dir():
                env["PATH"] = str(git_usr) + os.pathsep + env.get("PATH", "")
            env["UAVX_SPEC_CHECKER"] = str(stub)
            env["UAVX_SPEC_STRICT"] = "1" if strict else "0"
            res = subprocess.run([bash, str(script)], capture_output=True,
                                 text=True, env=env, cwd=str(REPO))

            got = "pass" if res.returncode == 0 else "die"
            label = f"exit {code}, {'send' if strict else 'build'}"
            if got != expect:
                print(f"  FAIL  {label}: {why}\n"
                      f"        wanted the gate to {expect}, it chose {got}\n"
                      f"        {(res.stdout + res.stderr).strip()[:200]}")
                bad += 1
                continue

            # The mode has to match the chunk, not just the outcome. The send check passing
            # --allow-offline would accept a stale record and still exit 0 here.
            args = log.read_text(encoding="utf-8") if log.is_file() else ""
            offline = "--allow-offline" in args
            if strict and offline:
                print(f"  FAIL  {label}: the send check ran with "
                      f"--allow-offline. Chunk 4.8 has to be online.")
                bad += 1
                continue
            if not strict and not offline:
                print(f"  FAIL  {label}: a build week ran the checker without "
                      f"--allow-offline, so a DNS drop stops the week.")
                bad += 1
                continue

            print(f"  ok    {label} -> {expect:4}  {why}")

        # Round 8 found dispatch validation below uavx_load_env. A bad chunk
        # on an unprovisioned shell then blamed ROS instead of the bad id.
        res = subprocess.run([bash, str(REPO / "scripts" / "gate.sh"), "9.9"],
                             capture_output=True, text=True, env=env,
                             cwd=str(REPO))
        output = res.stdout + res.stderr
        if res.returncode == 1 and "unknown chunk: 9.9" in output:
            print("  ok    an unknown chunk is rejected before environment setup")
        else:
            print(f"  FAIL  unknown chunk dispatch: rc={res.returncode}\n"
                  f"        {output.strip()[:300]}")
            bad += 1

    if bad:
        print(f"\n{bad} of {len(CASES)} preflight decisions are wrong")
        return 1
    print(f"\nall {len(CASES)} spec exit codes and early dispatch behaved as documented")
    return 0


if __name__ == "__main__":
    sys.exit(main())
