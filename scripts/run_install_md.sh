#!/usr/bin/env bash
# Execute the shell commands in INSTALL.md, in order, exactly as written.
#
# Round 7 finding 2. The fresh-install step was labelled "the submitted
# INSTALL.md path" and ran stage-1/setup/setup-all.sh. Those are different
# things: one is the file a judge is handed, the other is the path we happen to
# use. INSTALL.md was not even in the archive, so the instructions being
# certified were instructions nobody had ever run.
#
# This runs the delivered file. If a command in it is wrong, missing or in the
# wrong order, the install fails here, which is the same wall the judge would
# hit and roughly three weeks earlier.
#
# It reads every ```bash or ```sh fenced block, in document order, and runs the
# lines in them. A line starting with `#` is a comment. A line starting with
# `$ ` has the prompt stripped, because that is how people write them.
#
# Usage:  bash scripts/run_install_md.sh INSTALL.md

set -uo pipefail

DOC="${1:-INSTALL.md}"
[ -f "$DOC" ] || { printf 'FAILED: no %s here\n' "$DOC" >&2; exit 1; }

SCRIPT="$(mktemp)"
trap 'rm -f "$SCRIPT"' EXIT

python3 - "$DOC" "$SCRIPT" <<'PY'
import re, sys
doc, out = sys.argv[1], sys.argv[2]
text = open(doc, encoding="utf-8").read()
blocks = re.findall(r"```(?:bash|sh|shell|console)\n(.*?)```", text, re.S)
if not blocks:
    sys.exit(f"{doc} contains no bash or sh code block, so it gives a reader "
             f"nothing to run. It is a deliverable and it is what a judge "
             f"opens first.")
lines = []
for block in blocks:
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("$ "):
            s = s[2:]
        lines.append(s)
if not lines:
    sys.exit(f"{doc} has code blocks and no runnable command in them")
open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print(f"  {len(lines)} command(s) from {len(blocks)} block(s) in {doc}")
PY
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

# One command at a time, so the transcript says which one broke rather than
# leaving the reader to guess from a build log.
n=0
while IFS= read -r cmd; do
  n=$((n + 1))
  printf '\n--- INSTALL.md [%d] %s\n' "$n" "$cmd"
  # shellcheck disable=SC2086
  if ! bash -c "$cmd"; then
    printf '\nFAILED: INSTALL.md command %d exited non-zero:\n  %s\n' "$n" "$cmd" >&2
    printf 'This is what a judge would hit. Fix the instructions, not this script.\n' >&2
    exit 1
  fi
done < "$SCRIPT"

printf '\n  all %d INSTALL.md commands succeeded\n' "$n"
