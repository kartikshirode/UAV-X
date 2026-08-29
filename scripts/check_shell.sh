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

set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
cd "$REPO"

fails=0
n=0

while IFS= read -r f; do
  [ -f "$f" ] || continue
  n=$((n + 1))

  if LC_ALL=C grep -q $'\r' "$f"; then
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
  exit 1
fi

printf '  ok    %d shell scripts parse, none carry carriage returns\n' "$n"
