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

MODE="both"
SNAPSHOT=""
case "${1:-}" in
  --static-only)   MODE="static" ;;
  --from-snapshot) MODE="snapshot"; SNAPSHOT="${2:-}"
                   [ -n "$SNAPSHOT" ] || gdie "--from-snapshot needs a file" ;;
  "")              MODE="both" ;;
  *)               gdie "unknown argument: $1" ;;
esac

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
# Simulator ground truth only. Round 3 finding 2: the previous pattern also
# banned VehicleLocalPosition and VehicleOdometry, which are a vehicle's OWN PX4
# state estimate. Section 1 explicitly allows a node its own PX4 namespace, and
# the mission executor cannot fly without it, so that ban would have made a
# correct implementation unbuildable.
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

if [ "$MODE" = "static" ]; then
  [ "$VIOLATIONS" -eq 0 ] || gdie "${VIOLATIONS} seam violation(s) found statically"
  gsay "static pass clean"
  exit 0
fi

# --------------------------------------------------------------------- live
# Round 3 finding 2 listed four holes in the old live pass, all fixed here:
#   - it skipped any node whose name lacked a vehicle id, so an unknown process
#     was silently ignored. Now every process must appear in the manifest.
#   - a shared topic such as /swarm/broadcast was invisible. Now any /uavx/
#     endpoint outside the per-node tx/rx pair is a violation.
#   - direction was never checked, so publishing to your own rx was legal.
#   - normal ROS parameter services under /uavx/ were reported as illegal.

gsay "live pass (${MODE})"

if [ "$MODE" = "snapshot" ]; then
  [ -f "$SNAPSHOT" ] || gdie "no graph snapshot at ${SNAPSHOT}. The scenario runner must capture one while the scenario is up."
  GRAPH_SRC="$SNAPSHOT"
  gsay "reading graph snapshot ${SNAPSHOT}"
else
  uavx_load_env
  uavx_require_ros
  [ -n "$(ros2 node list 2>/dev/null || true)" ] || gdie "no ROS nodes running. The live pass needs a scenario up."
  GRAPH_SRC="live"
fi

# ROS gives every node these. They are infrastructure, not swarm traffic.
PARAM_SVC='describe_parameters|get_parameter_types|get_parameters|list_parameters|set_parameters|set_parameters_atomically|get_type_description'

python3 - "$GRAPH_SRC" <<'PYSEAM'
import json, re, subprocess, sys

src = sys.argv[1]
VEHICLES = ["uav_1", "uav_2", "uav_3", "uav_4"]
# Every process expected in a swarm scenario. An unknown one is a finding, not
# something to skip past.
MANIFEST = (
    [f"/{v}/router" for v in VEHICLES]
    + [f"/{v}/mission_executor" for v in VEHICLES]
    + [f"/{v}/role_manager" for v in VEHICLES]
    + ["/gcs/gcs_node"]
)
OUTSIDE = ["/link_layer", "/metrics_collector", "/scenario_runner"]
GROUND_TRUTH = re.compile(r"/gazebo/|model_states|ModelStates|GetModelState")
PARAM_SVC = re.compile(
    r"(describe_parameters|get_parameter_types|get_parameters|list_parameters"
    r"|set_parameters|set_parameters_atomically|get_type_description)$")

def graph():
    if src != "live":
        return json.load(open(src, encoding="utf-8"))
    out = {}
    nodes = subprocess.run(["ros2", "node", "list"], capture_output=True,
                           text=True).stdout.split()
    for n in nodes:
        info = subprocess.run(["ros2", "node", "info", n], capture_output=True,
                              text=True).stdout
        cur, d = None, {"publishers": [], "subscribers": [], "services": [],
                        "actions": []}
        for line in info.splitlines():
            t = line.strip()
            low = t.lower()
            if low.startswith("subscribers:"): cur = "subscribers"; continue
            if low.startswith("publishers:"):  cur = "publishers";  continue
            if "service" in low and low.endswith(":"): cur = "services"; continue
            if "action" in low and low.endswith(":"):  cur = "actions";  continue
            if cur and t.startswith("/"):
                d[cur].append(t.split(":")[0].strip())
        out[n] = d
    return out

g = graph()
violations = []

seen = set(g)
for want in MANIFEST:
    if not any(n.endswith(want) or n == want for n in seen):
        violations.append(f"expected process {want} is absent from the graph")

for node, ep in sorted(g.items()):
    if any(o in node for o in OUTSIDE):
        continue
    m = re.search(r"(uav_\d+|gcs)", node)
    if not m:
        violations.append(f"{node} is not in the manifest and is not an outside process")
        continue
    own = m.group(1)

    for kind in ("publishers", "subscribers", "services", "actions"):
        for topic in ep.get(kind, []):
            if kind == "services" and PARAM_SVC.search(topic):
                continue  # standard ROS parameter plumbing
            if topic.startswith("/uavx/"):
                parts = topic.strip("/").split("/")
                if len(parts) < 3:
                    violations.append(f"{node} holds shared endpoint {topic}; swarm traffic is per node only")
                    continue
                who, leaf = parts[1], parts[2]
                if who != own:
                    violations.append(f"{node} holds {topic}, which belongs to {who}")
                elif leaf not in ("tx", "rx"):
                    violations.append(f"{node} holds {topic}; only tx and rx exist under a vehicle")
                elif kind == "publishers" and leaf != "tx":
                    violations.append(f"{node} publishes to {topic}; nodes publish to tx only")
                elif kind == "subscribers" and leaf != "rx":
                    violations.append(f"{node} subscribes to {topic}; nodes subscribe to rx only")
                elif kind in ("services", "actions"):
                    violations.append(f"{node} exposes a {kind[:-1]} at {topic}; swarm traffic goes through tx/rx only")
            elif GROUND_TRUTH.search(topic):
                # Only the link layer and the evaluator may see simulator truth.
                # The static pass catches this in source; the live pass has to
                # catch it too, because a remap can introduce it at launch.
                violations.append(f"{node} reads simulator ground truth {topic}; only the link layer and uavx_eval may")
            elif re.match(r"^/(uav_\d+)/", topic):
                who = re.match(r"^/(uav_\d+)/", topic).group(1)
                if who != own:
                    violations.append(f"{node} touches PX4 namespace {topic} belonging to {who}")
            elif kind in ("services", "actions") and not PARAM_SVC.search(topic):
                if any(v in topic for v in VEHICLES) or "swarm" in topic:
                    violations.append(f"{node} exposes {kind[:-1]} {topic} outside the seam")

if violations:
    for v in violations:
        print(f"  VIOLATION  {v}")
    print("")
    print(f"{len(violations)} seam violation(s). The communication resilience claim does not hold.")
    sys.exit(1)

print("  ok         live graph respects the seam")
sys.exit(0)
PYSEAM
rc=$?

if [ "$rc" -ne 0 ] || [ "$VIOLATIONS" -ne 0 ]; then
  gdie "seam violated. Fix before reporting any delivery number."
fi

gsay "seam holds"
exit 0
