#!/usr/bin/env bash
# Enforce the tx/rx seam. This is the 25% of the rubric that nothing else guards.
#
# Round 2 finding 7: the plan asked for "a test asserting no swarm node
# subscribes to another vehicle's topics directly". That covers one bypass out
# of six. A node can use a shared broadcast topic, read simulator poses, call a
# service, remap a legal-looking name at launch, build a name at runtime, or
# publish straight at the GCS. None of those is literally "another vehicle's
# topic".
#
# Two passes, because neither alone is enough. A source grep misses launch
# remaps and names built at runtime. A live graph check misses code paths that
# did not run in this scenario.
#
#   static    over uavx_ws/src
#   graph     over a captured snapshot, or the live graph, per scenario
#
# Round 4 finding 4 moved the graph half into scripts/seam_graph.py so the
# fixtures can read what it said rather than only whether it exited non-zero.
# On a tree with no uavx_ws/src every fixture used to fail here, in this
# wrapper, and the suite counted eight of those failures as eight bypasses
# correctly caught.
#
# Usage:
#   scripts/check_seam.sh --static-only
#   scripts/check_seam.sh --snapshot <file> --scenario <name>
#   scripts/check_seam.sh --live --scenario <name>
#
# The allowlist is stage-1/architecture.md section 1.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

MODE=""
SNAPSHOT=""
SCENARIO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --static-only) MODE="static" ;;
    --snapshot)    MODE="snapshot"; SNAPSHOT="${2:-}"; shift
                   [ -n "$SNAPSHOT" ] || gdie "--snapshot needs a file" ;;
    --live)        MODE="live" ;;
    --scenario)    SCENARIO="${2:-}"; shift
                   [ -n "$SCENARIO" ] || gdie "--scenario needs a name" ;;
    *)             gdie "unknown argument: $1" ;;
  esac
  shift
done
[ -n "$MODE" ] || gdie "one of --static-only, --snapshot <file>, --live is required"
[ "$MODE" = "static" ] || [ -n "$SCENARIO" ] \
  || gdie "--scenario is required for a graph pass; the manifest differs per scenario"

SRC="${UAVX_WS_SRC}/src"
VIOLATIONS=0

viol() { printf '  VIOLATION  %s\n' "$*"; VIOLATIONS=$((VIOLATIONS + 1)); }
pass() { printf '  ok         %s\n' "$*"; }

# ------------------------------------------------------------------- static
if [ "$MODE" = "static" ]; then
  [ -d "$SRC" ] || gdie "no source at ${SRC}. Nothing to check yet."
  gsay "static pass over ${SRC}"

  # Only the link layer and the evaluator may read ground truth. Everything else
  # reading a pose interface is the swarm becoming omniscient, which is exactly
  # the claim the proposal must not make.
  # Simulator ground truth only. Round 3 finding 2: the previous pattern also
  # banned VehicleLocalPosition and VehicleOdometry, which are a vehicle's OWN
  # PX4 state estimate. Section 1 explicitly allows a node its own PX4
  # namespace, and the mission executor cannot fly without it, so that ban would
  # have made a correct implementation unbuildable.
  GROUND_TRUTH_RX='/gazebo/model_states|gazebo_msgs|ModelStates|GetModelState'
  while IFS= read -r f; do
    case "$f" in
      */uavx_comms/*link_layer*|*/uavx_eval/*) continue ;;
    esac
    if grep -nE "$GROUND_TRUTH_RX" "$f" >/dev/null 2>&1; then
      viol "$(realpath --relative-to="$UAVX_REPO" "$f") reads simulator ground truth. Only the link layer and uavx_eval may."
    fi
  done < <(find "$SRC" -type f \( -name '*.py' -o -name '*.cpp' -o -name '*.hpp' \) 2>/dev/null)

  # A swarm process may only ever name its own vehicle. Anything constructing an
  # endpoint for a second vehicle id is either the link layer or a bypass.
  while IFS= read -r f; do
    case "$f" in
      */uavx_comms/*link_layer*|*/uavx_eval/*) continue ;;
    esac
    # `|| true` is load bearing. grep exits 1 when a file names no vehicle at
    # all, which is the normal case, and under `set -o pipefail` that failure
    # propagates out of the command substitution and `set -e` kills the script
    # mid-scan. It printed its header, exited 1 and checked nothing. Nobody saw
    # it because uavx_ws/src does not exist yet, so the guard above always fired
    # first: the static pass had never once run over a source tree.
    ids="$( { grep -oE '/uavx/(uav_[0-9]+|gcs)/' "$f" 2>/dev/null || true; } | sort -u | wc -l)"
    if [ "${ids:-0}" -gt 1 ]; then
      viol "$(realpath --relative-to="$UAVX_REPO" "$f") hard-codes endpoints for ${ids} different vehicles."
    fi
  done < <(find "$SRC" -type f \( -name '*.py' -o -name '*.cpp' \) 2>/dev/null)

  # Services and actions between swarm nodes bypass the link layer entirely, so
  # they carry swarm data over a channel the radio model never sees.
  while IFS= read -r f; do
    case "$f" in
      */uavx_comms/*link_layer*|*/uavx_eval/*) continue ;;
    esac
    if grep -nE 'create_service|create_client|ActionServer|ActionClient' "$f" >/dev/null 2>&1; then
      viol "$(realpath --relative-to="$UAVX_REPO" "$f") creates a service or action. Swarm traffic goes through tx/rx only."
    fi
  done < <(find "$SRC" -type f \( -name '*.py' -o -name '*.cpp' \) 2>/dev/null)

  if [ "$VIOLATIONS" -eq 0 ]; then
    pass "no static violations"
    gsay "static pass clean"
    exit 0
  fi
  gdie "${VIOLATIONS} seam violation(s) found statically"
fi

# -------------------------------------------------------------------- graph
gsay "graph pass, scenario ${SCENARIO}, source ${MODE}"

if [ "$MODE" = "live" ]; then
  uavx_load_env
  uavx_require_ros
  python3 "${HERE}/seam_graph.py" --scenario "$SCENARIO" --live || gdie "tx/rx seam violated at runtime"
else
  python3 "${HERE}/seam_graph.py" --scenario "$SCENARIO" --snapshot "$SNAPSHOT" \
    || gdie "tx/rx seam violated in the captured graph"
fi

gsay "seam holds"
exit 0
