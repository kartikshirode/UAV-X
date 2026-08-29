#!/usr/bin/env bash
# Rehearse the install, and be the thing that writes the receipt.
#
# Round 5 finding 6: check_dryruns.py accepted JSON saying result=pass with any
# five strings in steps_run. It never ran an installer, never read an exit code,
# never checked a target existed. The W3 gate that depends on it could be
# satisfied by typing a file, which is the same shape as round 4 finding 1 one
# layer deeper.
#
# So the gate no longer trusts a receipt. This script produces one, and it can
# only produce one by actually doing the work: every setup step runs here, its
# return code is captured here, the transcript is kept as an artifact, and the
# receipt is written atomically at the end and never on a failure.
#
# What this is NOT: a clean-machine install. It rebuilds from the working tree
# into a fresh prefix on a machine that already has the apt packages. The
# archive install onto a clean target is W5's job and is a different claim. Both
# are named for what they are, because round 5 is right that calling this a
# fresh install would be a lie the gate then certifies.
#
# Usage:  bash scripts/rehearse_install.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"
uavx_load_env

TARGET="${UAVX_REHEARSE_PREFIX:-$HOME/uavx-rehearse}"
OUT="${UAVX_REPO}/submission"
TRANSCRIPT="${OUT}/dryrun-install-transcript.log"
RECEIPT="${OUT}/dryrun-install-receipt.json"

mkdir -p "$OUT"
rm -rf "$TARGET"
mkdir -p "$TARGET"

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SOURCE_SHA="$(python3 "${HERE}/source_tree_hash.py")"

gsay "rebuild rehearsal into ${TARGET}"
printf 'target=%s\nstarted=%s\nsource_tree_sha256=%s\n\n' \
  "$TARGET" "$STARTED" "$SOURCE_SHA" > "$TRANSCRIPT"

STEPS=()
RCS=()
run_step() {
  local label="$1"; shift
  printf '\n=== %s\n$ %s\n' "$label" "$*" >> "$TRANSCRIPT"
  set +e
  "$@" >> "$TRANSCRIPT" 2>&1
  local rc=$?
  set -e
  printf '[exit %d]\n' "$rc" >> "$TRANSCRIPT"
  STEPS+=("$label")
  RCS+=("$rc")
  if [ "$rc" -ne 0 ]; then
    gdie "rehearsal step '${label}' exited ${rc}. No receipt written. See ${TRANSCRIPT}."
  fi
  printf '  ok    %-34s exit 0\n' "$label"
}

# The stack itself is already installed; what is rehearsed is that the pinned
# versions still hold and that our tree builds and flies from scratch.
run_step "verify.sh, stack matches versions.lock" \
  bash "${UAVX_REPO}/stage-1/setup/verify.sh"

run_step "colcon build into a fresh prefix" \
  colcon build --base-paths "${UAVX_WS_SRC}" \
    --build-base "${TARGET}/build" --install-base "${TARGET}/install" \
    --symlink-install

run_step "the built overlay sources" \
  bash -c "set -u; . '${TARGET}/install/setup.bash'; command -v ros2 >/dev/null"

run_step "colcon test in the fresh prefix" \
  colcon test --base-paths "${UAVX_WS_SRC}" \
    --build-base "${TARGET}/build" --install-base "${TARGET}/install" \
    --return-code-on-test-failure

run_step "smoke run, four vehicles airborne" \
  bash "${UAVX_REPO}/scripts/run_smoke.sh" --vehicles 4

ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TRANSCRIPT_SHA="$(sha256sum "$TRANSCRIPT" | cut -d' ' -f1)"

# Atomic, and only now. A receipt written before the last step passes is a
# receipt for something that did not happen.
python3 - "$RECEIPT" "$TARGET" "$STARTED" "$ENDED" "$SOURCE_SHA" \
         "$TRANSCRIPT_SHA" "${STEPS[@]}" <<'PY'
import json, os, sys, tempfile
receipt, target, started, ended, src, tsha, *steps = sys.argv[1:]
data = {
    "kind": "rebuild-rehearsal",
    "result": "pass",
    "target": target,
    "started": started,
    "done": ended[:10],
    "ended": ended,
    "source_tree_sha256": src,
    "transcript": "submission/dryrun-install-transcript.log",
    "transcript_sha256": tsha,
    "steps_run": steps,
}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(receipt))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, receipt)
PY

gsay "rehearsal passed, receipt written"
printf '  transcript %s\n  sha256     %s\n' "$TRANSCRIPT" "$TRANSCRIPT_SHA"
