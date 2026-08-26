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
#   static  - over uavx_ws/src
#   live    - over the running ROS graph, with remaps already resolved
#
# Usage:  scripts/check_seam.sh [--static-only]
# The allowlist is stage-1/architecture.md section 1.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

STATIC_ONLY=0
[ "${1:-}" = "--static-only" ] && STATIC_ONLY=1

SRC="${UAVX_WS_SRC}/src"
VIOLATIONS=0

viol() { printf '  VIOLATION  %s\n' "$*"; VIOLATIONS=$((VIOLATIONS + 1)); }
pass() { printf '  ok         %s\n' "$*"; }

[ -d "$SRC" ] || gdie "no source at ${SRC}. Nothing to check yet."

# ------------------------------------------------------------------- static
gsay "static pass over ${SRC}"

# Only the link layer and the evaluator may read ground truth. Everything else
# reading a pose interface is the swarm becoming omniscient, which is exactly
# the claim the proposal must not make.
GROUND_TRUTH_RX='VehicleLocalPosition|VehicleGlobalPosition|VehicleOdometry|/gazebo/model_states|ModelStates'
while IFS= read -r f; do
  case "$f" in
    */uavx_comms/*link_layer*|*/uavx_eval/*) continue ;;
  esac
  if grep -nE "$GROUND_TRUTH_RX" "$f" >/dev/null 2>&1; then
    viol "$(realpath --relative-to="$UAVX_REPO" "$f") reads ground truth. Only the link layer and uavx_eval may."
  fi
done < <(find "$SRC" -type f \( -name '*.py' -o -name '*.cpp' -o -name '*.hpp' \) 2>/dev/null)

# A swarm process may only ever name its own vehicle. Anything constructing an
# endpoint for a second vehicle id is either the link layer or a bypass.
while IFS= read -r f; do
  case "$f" in
    */uavx_comms/*link_layer*|*/uavx_eval/*) continue ;;
  esac
  ids="$(grep -oE '/uavx/(uav_[0-9]+|gcs)/' "$f" 2>/dev/null | sort -u | wc -l)"
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

[ "$VIOLATIONS" -eq 0 ] && pass "no static violations"

if [ "$STATIC_ONLY" -eq 1 ]; then
  [ "$VIOLATIONS" -eq 0 ] || gdie "${VIOLATIONS} seam violation(s) found statically"
  gsay "static pass clean"
  exit 0
fi

# --------------------------------------------------------------------- live
gsay "live pass over the running ROS graph"
uavx_load_env
uavx_require_ros

nodes="$(ros2 node list 2>/dev/null || true)"
[ -n "$nodes" ] || gdie "no ROS nodes running. The live pass needs a scenario up."

while IFS= read -r node; do
  [ -n "$node" ] || continue
  case "$node" in
    */link_layer|*/metrics_collector) continue ;;   # outside the swarm, by design
  esac

  # Which vehicle does this node belong to? Its namespace decides.
  own="$(printf '%s' "$node" | grep -oE 'uav_[0-9]+|gcs' | head -1 || true)"
  [ -n "$own" ] || continue

  info="$(ros2 node info "$node" 2>/dev/null || true)"

  # Any /uavx/<other>/ endpoint is a bypass. Remaps are already resolved here,
  # which is the whole reason the live pass exists.
  while IFS= read -r ep; do
    [ -n "$ep" ] || continue
    other="$(printf '%s' "$ep" | grep -oE 'uav_[0-9]+|gcs' | head -1 || true)"
    if [ -n "$other" ] && [ "$other" != "$own" ]; then
      viol "${node} holds ${ep}, which belongs to ${other}"
    fi
  done < <(printf '%s' "$info" | grep -oE '/uavx/[a-z0-9_]+/(tx|rx)' | sort -u)

  # Reading another vehicle's PX4 namespace is the same bypass wearing a hat.
  while IFS= read -r ep; do
    [ -n "$ep" ] || continue
    other="$(printf '%s' "$ep" | grep -oE 'uav_[0-9]+' | head -1 || true)"
    if [ -n "$other" ] && [ "$other" != "$own" ]; then
      viol "${node} touches PX4 namespace ${ep} belonging to ${other}"
    fi
  done < <(printf '%s' "$info" | grep -oE '/uav_[0-9]+/[a-z0-9_/]+' | sort -u)

  # Services and actions again, this time as the graph actually built them.
  svc_count="$(printf '%s' "$info" | awk '/Service Servers:/{f=1;next}/Action|Subscribers:|Publishers:/{f=0}f' | grep -cE '/uavx/' || true)"
  if [ "${svc_count:-0}" -gt 0 ]; then
    viol "${node} exposes ${svc_count} service(s) under /uavx/"
  fi
done <<< "$nodes"

if [ "$VIOLATIONS" -eq 0 ]; then
  pass "no live violations"
  gsay "seam holds"
  exit 0
fi

gdie "${VIOLATIONS} seam violation(s). The communication resilience claim does not hold; fix before reporting any delivery number."
