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

# One pass, bytes, no shell in the way.
CR_FILES="$(git ls-files '*.sh' | python3 -c '
import sys, pathlib
for line in sys.stdin.read().splitlines():
    p = pathlib.Path(line)
    if p.is_file() and b"\r" in p.read_bytes():
        print(line)
')"

while IFS= read -r f; do
  [ -f "$f" ] || continue
  n=$((n + 1))

  if printf '%s\n' "$CR_FILES" | grep -qxF "$f"; then
    printf '  FAIL  %s has carriage returns. It will die on its first line under bash.\n' "$f"
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

printf '  ok    %d shell scripts parse, none carry carriage returns\n' "$n"
