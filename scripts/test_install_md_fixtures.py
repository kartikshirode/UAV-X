#!/usr/bin/env python3
"""Exercise the submitted-install command runner with real shells."""

import shutil
import subprocess
import sys
import tempfile
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER = REPO / "scripts" / "run_install_md.sh"


def bash_command() -> list[str]:
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return [found]
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    return [str(git_bash)] if git_bash.is_file() else []


def bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if len(value) > 2 and value[1] == ":":
        return f"/{value[0].lower()}{value[2:]}"
    return value


def run(doc: Path) -> subprocess.CompletedProcess:
    bin_dir = doc.parent / "bin"
    bin_dir.mkdir(exist_ok=True)
    python3 = bin_dir / "python3"
    python3.write_text(f"#!/usr/bin/env bash\n{bash_path(Path(sys.executable))} \"$@\"\n",
                       encoding="utf-8", newline="\n")
    python3.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([
        str(bin_dir), r"C:\Program Files\Git\usr\bin", env.get("PATH", "")])
    return subprocess.run([*bash_command(), str(RUNNER), str(doc)],
                          capture_output=True, text=True, cwd=str(doc.parent),
                          env=env)


def main() -> int:
    if not bash_command():
        print("FAIL  no Bash interpreter available")
        return 1
    root = Path(tempfile.mkdtemp(prefix="uavx-install-md-"))
    failures = 0
    try:
        good = root / "good.md"
        good.write_text("""# Install

```bash
mkdir -p work
cd work
export UAVX_FIXTURE_VALUE=kept
printf '%s\\n' \\
  \"$UAVX_FIXTURE_VALUE\" > value.txt
test \"$(cat value.txt)\" = kept
```

```console
$ test -f value.txt
this line is command output, not a command
```
""", encoding="utf-8")
        proc = run(good)
        if proc.returncode == 0:
            print("ok    state, continuations and console output are handled")
        else:
            failures += 1
            print(f"FAIL  valid instructions exited {proc.returncode}")
            print((proc.stdout + proc.stderr).strip())

        bad = root / "masked-pipeline.md"
        bad.write_text("""# Install

```bash
false | true
```
""", encoding="utf-8")
        proc = run(bad)
        if proc.returncode != 0 and "FAILED" in proc.stderr:
            print("ok    a failed pipeline cannot hide behind its last command")
        else:
            failures += 1
            print("FAIL  false | true was accepted")
            print((proc.stdout + proc.stderr).strip())
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if failures:
        return 1
    print("INSTALL.md runner fixtures passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
