#!/usr/bin/env bash
# Rehearse the video capture, against a scenario that is actually running.
#
# Round 5 finding 6: the recording half of the W3 gate accepted any decodable
# local clip of at least 55 seconds. The receipt did not hash the clip, and the
# clip did not have to show Gazebo, four vehicles, this scenario or this source.
# A holiday video passed.
#
# This is the risk that most deserves a rehearsal. The `gazebo` GUI binary has
# taken this WSL distro down three times, the video is a deliverable, and
# finding out in W5 that there is no capture path is a submission-level problem
# with four days left.
#
# So the clip is captured here, from a scenario this script launches, with the
# run id burned into the frame. An unrelated clip cannot satisfy it, because the
# receipt binds the clip's hash to the run that produced it.
#
# Usage:  bash scripts/rehearse_recording.sh [scenario]
#         default scenario: scenarios/relay_required.yaml

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"
uavx_load_env

SCENARIO="${1:-scenarios/relay_required.yaml}"
SECONDS_WANTED="${UAVX_REHEARSE_CLIP_S:-60}"
OUT="${UAVX_REPO}/submission"
CLIP="${OUT}/dryrun-recording.mp4"
RECEIPT="${OUT}/dryrun-recording-receipt.json"

command -v ffmpeg >/dev/null 2>&1 || gdie "ffmpeg missing. apt install ffmpeg"
[ -f "${UAVX_REPO}/${SCENARIO}" ] || gdie "no scenario at ${SCENARIO}"

mkdir -p "$OUT"
rm -f "$CLIP"
SOURCE_SHA="$(python3 "${HERE}/source_tree_hash.py")"

# Headless. gzclient is what kills the distro, so the capture path has to be
# the offscreen one: gzserver plus a camera, or the recorded topic replayed.
# Whatever the runner does, it must not open the GUI.
export UAVX_HEADLESS=1

gsay "launching ${SCENARIO} for a ${SECONDS_WANTED}s capture rehearsal"
bash "${UAVX_REPO}/scripts/run_scenario.sh" "${SCENARIO}" --record "${CLIP}" \
     --record-seconds "${SECONDS_WANTED}" \
  || gdie "the scenario run failed, so there is nothing to have recorded"

[ -f "${UAVX_RUNS_DIR}/latest.jsonl" ] \
  || gdie "the run produced no record, so the clip cannot be bound to a run"
RUN_ID="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1],encoding='utf-8'))['run_id'])" \
          "${UAVX_RUNS_DIR}/latest.jsonl")"

[ -f "$CLIP" ] || gdie "no clip at ${CLIP}. The capture path does not work, which is exactly what this rehearsal exists to find out in W3 rather than W5."

DUR="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$CLIP")"
python3 -c "import sys;sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)" \
  "$DUR" "$SECONDS_WANTED" \
  || gdie "clip is ${DUR}s, wanted ${SECONDS_WANTED}s. A short clip does not prove a long capture holds up."

ffmpeg -v error -i "$CLIP" -f null - 2>/dev/null || gdie "the clip does not decode"

CLIP_SHA="$(sha256sum "$CLIP" | cut -d' ' -f1)"
ENDED="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

python3 - "$RECEIPT" "$CLIP_SHA" "$RUN_ID" "$SCENARIO" "$SOURCE_SHA" "$DUR" "$ENDED" <<'PY'
import json, os, sys, tempfile
receipt, sha, run_id, scenario, src, dur, ended = sys.argv[1:8]
data = {
    "result": "pass",
    "method": "headless gzserver capture via run_scenario.sh --record",
    "clip": "submission/dryrun-recording.mp4",
    "clip_sha256": sha,
    "duration_s": float(dur),
    "run_id": run_id,
    "scenario": scenario,
    "source_tree_sha256": src,
    "done": ended[:10],
    "ended": ended,
}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(receipt))
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, receipt)
PY

gsay "recording rehearsal passed"
printf '  clip   %s\n  sha256 %s\n  run    %s\n' "$CLIP" "$CLIP_SHA" "$RUN_ID"
