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
import re, shlex, sys
doc, out = sys.argv[1], sys.argv[2]
text = open(doc, encoding="utf-8").read()
blocks = re.findall(r"```(?:bash|sh|shell|console)\n(.*?)```", text, re.S)
if not blocks:
    sys.exit(f"{doc} contains no bash or sh code block, so it gives a reader "
             f"nothing to run. It is a deliverable and it is what a judge "
             f"opens first.")
commands = []
for block in blocks:
    for line in block.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("$ "):
            s = s[2:]
        commands.append(s)
if not commands:
    sys.exit(f"{doc} has code blocks and no runnable command in them")
lines = ["set -euo pipefail",
         "trap 'rc=$?; printf \\\"\\nFAILED: INSTALL.md script line %s exited %s\\n\\\" \\\"$LINENO\\\" \\\"$rc\\\" >&2; exit \\\"$rc\\\"' ERR"]
for index, command in enumerate(commands, 1):
    lines.append(f"printf '\\n--- INSTALL.md [{index}] %s\\n' "
                 f"{shlex.quote(command)}")
    lines.append(command)
open(out, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
print(f"  {len(commands)} command(s) from {len(blocks)} block(s) in {doc}")
PY
rc=$?
[ "$rc" -eq 0 ] || exit "$rc"

# One strict shell keeps `cd`, exported variables and sourced setup files alive
# for later lines. Pipe failures also stop the install instead of being hidden
# by a successful command on the right.
if ! bash "$SCRIPT"; then
  printf 'This is what a judge would hit. Fix the instructions, not this script.\n' >&2
  exit 1
fi

printf '\n  all INSTALL.md commands succeeded\n'
