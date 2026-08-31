#!/usr/bin/env bash
# Every shell script in the repo parses, and none of them carry carriage
# returns. Cheap, and it closes a hole that cost a full gate run to find.
#
# Two facts about the target distro, both checked on Ubuntu 22.04 with
# bash 5.1.16 on 29 August 2026:
#
#   1. A script whose parse fails after at least one successful command exits
#      0, not 2. bash 5.2 (Git Bash on the Windows side) exits 2 for the same
#      file, so this is invisible when developing and green when it matters.
#   2. `bash -n` on that same file also exits 0. The message goes to stderr and
#      the status does not move, so the usual `bash -n || die` is decoration
#      here. Empty stderr is the only reliable signal.
#
# The way in is editing a .sh from the Windows side. .gitattributes normalises
# on commit, but the gates run against the working tree, and a CRLF script dies
# on line 1 with `$'\r': command not found`. verify.sh did exactly that and
# still reported success, which is how this was found.
#
# The carriage return scan runs in python on purpose. `grep $'\r'` under MSYS
# opens the file in text mode and strips them, so the first version of this
# check reported a clean tree from Git Bash and found a broken 04-px4.sh from
# WSL. A checker whose answer depends on which shell called it is worse than
# no checker, because one of the two answers is reassuring.

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
cd "$REPO"

fails=0
n=0

# Round 8 found both Python scans failing under Git Bash because the Windows
# Store python3 shim was on PATH but could not execute. Command substitution
# swallowed the non-zero status and this script printed green over two empty
# scan results. Pick an interpreter by running it, not by finding its name.
PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c "import pathlib" >/dev/null 2>&1; then
    PYTHON_BIN="$candidate"
    break
  fi
done
[ -n "$PYTHON_BIN" ] \
  || { printf '  FAIL  no working Python interpreter for byte and token scans\n'; exit 1; }

# One pass, bytes, no shell in the way.
if ! CR_FILES="$(git ls-files '*.sh' | "$PYTHON_BIN" -c '
import sys, pathlib
for line in sys.stdin.read().splitlines():
    p = pathlib.Path(line)
    if p.is_file() and b"\r" in p.read_bytes():
        print(line)
')"; then
  printf '  FAIL  the carriage-return scan did not complete\n'
  exit 1
fi

# Round 8 found two literal `\n` tokens where line continuations were meant.
# Bash parses them as an argument named `n`, so syntax checks stay green and
# the called program fails on an option it never received. An unquoted token
# of that exact shape is not a newline in shell source.
if ! ESCAPE_FILES="$(git ls-files '*.sh' | "$PYTHON_BIN" -c '
import re, sys, pathlib
for name in sys.stdin.read().splitlines():
    path = pathlib.Path(name)
    if not path.is_file():
        continue
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(r"(^|\s)\\n(?=\s|$)", line):
            print(f"{name}:{number}")
')"; then
  printf '  FAIL  the literal-token scan did not complete\n'
  exit 1
fi

while IFS= read -r f; do
  [ -f "$f" ] || continue
  n=$((n + 1))

  if printf '%s\n' "$CR_FILES" | grep -qxF "$f"; then
    printf '  FAIL  %s has carriage returns. It will die on its first line under bash.\n' "$f"
    fails=$((fails + 1))
    continue
  fi

  bad_escape="$(printf '%s\n' "$ESCAPE_FILES" | grep -E "^${f}:[0-9]+$" || true)"
  if [ -n "$bad_escape" ]; then
    printf '  FAIL  %s has a literal \\n shell token at %s. Use a backslash and a real newline.\n' \
      "$f" "${bad_escape##*:}"
    fails=$((fails + 1))
    continue
  fi

  err="$(bash -n "$f" 2>&1)"
  if [ -n "$err" ]; then
    printf '  FAIL  %s does not parse:\n        %s\n' "$f" "${err%%$'\n'*}"
    fails=$((fails + 1))
    continue
  fi
done < <(git ls-files '*.sh')

if [ "$n" -eq 0 ]; then
  printf '  FAIL  found no shell scripts to check, which means this checked nothing.\n'
  exit 1
fi

if [ "$fails" -ne 0 ]; then
  printf '\n  %d of %d shell scripts are broken.\n' "$fails" "$n"
  printf '  For carriage returns:  git rm --cached -r . && git reset --hard\n'
  printf '  is NOT the fix. Rewrite the file with LF endings; .gitattributes\n'
  printf '  already says eol=lf, so the index is probably already correct.\n'
  exit 1
fi

printf '  ok    %d shell scripts parse, none carry carriage returns or literal \\n tokens\n' "$n"
