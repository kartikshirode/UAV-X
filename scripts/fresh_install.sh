#!/usr/bin/env bash
# Install the frozen archive onto a clean target, and be the thing that writes
# the receipt.
#
# Round 6 finding 1. The binding W5 claim is that the archive being submitted
# installs and runs on a machine that is not this one. check_submission.py
# compared three fields, archive_sha256, commit_sha and the string "pass", and
# there was no script anywhere in the repository that produced them. A JSON
# file with three values in it certified the one install that matters, and the
# fixture that was meant to prove the checker worked wrote those three values
# by hand.
#
# rehearse_install.sh does not cover this and is not meant to. It rebuilds the
# working tree into a fresh prefix on the already-provisioned distro, before
# the W4 code and before the archive exists. Two different claims, named
# separately on purpose, because calling the rebuild a fresh install is a lie
# the gate would then certify.
#
# The target
# ----------
# Set UAVX_FRESH_DISTRO to the name of a disposable WSL distribution and every
# step runs inside it. Microsoft documents importing one:
# https://learn.microsoft.com/en-us/windows/wsl/install
#
# Without it the target is a clean directory prefix on this machine, wiped and
# recreated here. That is weaker, and the receipt says which it was, so a judge
# reading the record and a checker reading the JSON see the same thing.
#
# Usage:  bash scripts/fresh_install.sh
#         UAVX_FRESH_DISTRO=uavx-clean bash scripts/fresh_install.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"
uavx_load_env

OUT="${UAVX_REPO}/submission"
ARCHIVE="${OUT}/uavx-source.zip"
MANIFEST="${OUT}/source-manifest.json"
TRANSCRIPT="${OUT}/fresh-install-transcript.log"
RECEIPT="${OUT}/fresh-install-receipt.json"
TARGET="${UAVX_FRESH_PREFIX:-$HOME/uavx-fresh-install}"
DISTRO="${UAVX_FRESH_DISTRO:-}"

[ -f "$ARCHIVE" ]  || gdie "no archive at ${ARCHIVE}. Run scripts/freeze_source.sh first; this installs what is being submitted, not the working tree."
[ -f "$MANIFEST" ] || gdie "no source manifest at ${MANIFEST}"

ARCHIVE_SHA="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
WANT_SHA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['archive_sha256'])" "$MANIFEST")"
COMMIT="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['commit_sha'])" "$MANIFEST")"
[ "$ARCHIVE_SHA" = "$WANT_SHA" ] \
  || gdie "the archive on disk hashes to ${ARCHIVE_SHA:0:16} and the manifest says ${WANT_SHA:0:16}. Re-freeze before installing."

if [ -n "$DISTRO" ]; then
  TARGET_KIND="wsl-distro"
  TARGET_NAME="$DISTRO"
else
  TARGET_KIND="clean-prefix"
  TARGET_NAME="$TARGET"
fi

STARTED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -rf "$TARGET"
mkdir -p "$TARGET" "$OUT"

printf 'target_kind=%s\ntarget=%s\narchive_sha256=%s\ncommit=%s\nstarted=%s\n\n' \
  "$TARGET_KIND" "$TARGET_NAME" "$ARCHIVE_SHA" "$COMMIT" "$STARTED" > "$TRANSCRIPT"

STEPS=()
run_step() {
  local label="$1"; shift
  printf '\n=== %s\n$ %s\n' "$label" "$*" >> "$TRANSCRIPT"
  set +e
  "$@" >> "$TRANSCRIPT" 2>&1
  local rc=$?
  set -e
  printf '[exit %d]\n' "$rc" >> "$TRANSCRIPT"
  STEPS+=("$label")
  if [ "$rc" -ne 0 ]; then
    gdie "fresh install step '${label}' exited ${rc}. No receipt written. See ${TRANSCRIPT}."
  fi
  printf '  ok    %-46s exit 0\n' "$label"
}

# Everything below runs on the target. With a distro set, that means inside it.
on_target() {
  if [ -n "$DISTRO" ]; then
    wsl.exe -d "$DISTRO" -- bash -lc "$*"
  else
    bash -lc "$*"
  fi
}

SRC="${TARGET}/uavx-source"

gsay "installing ${ARCHIVE_SHA:0:12} onto ${TARGET_KIND} ${TARGET_NAME}"

run_step "unpack the frozen archive into a clean target" \
  on_target "cd '${TARGET}' && unzip -q '${ARCHIVE}'"

# The submitted instructions, not ours. If INSTALL.md does not work, the judge
# hits the same wall and this is where we find out.
run_step "the submitted INSTALL.md path" \
  on_target "cd '${SRC}' && bash stage-1/setup/setup-all.sh"

run_step "colcon build from the unpacked archive" \
  on_target "cd '${SRC}' && colcon build --base-paths uavx_ws --build-base '${TARGET}/build' --install-base '${TARGET}/install' --symlink-install"

run_step "verify.sh inside the target" \
  on_target "cd '${SRC}' && bash stage-1/setup/verify.sh"

run_step "smoke run, four vehicles airborne" \
  on_target "cd '${SRC}' && bash scripts/run_smoke.sh --vehicles 4 --runs-dir '${TARGET}/runs'"

SMOKE_RUN=""
if [ -f "${TARGET}/runs/latest.jsonl" ]; then
  SMOKE_RUN="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8')).get('run_id',''))" "${TARGET}/runs/latest.jsonl")"
fi
[ -n "$SMOKE_RUN" ] \
  || gdie "the smoke run left no run id. An install that builds and cannot fly is not an install that works."

# What is actually on the target now, read off the target rather than copied
# out of our own lock file.
VERSIONS="$(on_target "cd '${SRC}' && bash stage-1/setup/verify.sh" | grep -E '^\s+ok\s+' || true)"
printf '\n=== installed version set\n%s\n[exit 0]\n' "$VERSIONS" >> "$TRANSCRIPT"

ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TRANSCRIPT_SHA="$(sha256sum "$TRANSCRIPT" | cut -d' ' -f1)"

python3 - "$RECEIPT" "$TARGET_KIND" "$TARGET_NAME" "$ARCHIVE_SHA" "$COMMIT" \
         "$STARTED" "$ENDED" "$TRANSCRIPT_SHA" "$SMOKE_RUN" "${STEPS[@]}" <<'PY'
import json, os, sys, tempfile
(receipt, kind, name, archive_sha, commit, started, ended, tsha,
 smoke, *steps) = sys.argv[1:]
data = {
    "kind": "archive-install",
    "result": "pass",
    "target": {"kind": kind, "name": name},
    "archive_sha256": archive_sha,
    "commit_sha": commit,
    "started": started,
    "done": ended[:10],
    "ended": ended,
    "transcript": "submission/fresh-install-transcript.log",
    "transcript_sha256": tsha,
    "smoke_run_id": smoke,
    "steps_run": steps,
}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(receipt))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, receipt)
PY

gsay "fresh install passed, receipt written"
printf '  target     %s %s\n  archive    %s\n  smoke run  %s\n  transcript %s\n' \
  "$TARGET_KIND" "$TARGET_NAME" "${ARCHIVE_SHA:0:16}" "$SMOKE_RUN" "$TRANSCRIPT_SHA"
