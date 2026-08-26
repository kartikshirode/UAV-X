#!/usr/bin/env bash
# The single definition of every week's gate.
#
# Round 2 finding 2: the gates were written out in stage-1/plan.md,
# stage-1/decisions.md and .claude/weekly-loop.md, and the three had already
# drifted apart. One of them would have failed a correct implementation. Prose
# cannot be the source of truth for something a machine enforces, so this script
# is the source of truth and those three files describe it instead.
#
# Usage:  bash scripts/gate.sh <week>     week is 1..5
#         bash scripts/gate.sh preflight  environment only
#
# Exit 0 means the week passes. Anything else means it does not.
#
# Read the exit code from wsl.exe itself, never from a $? inside a quoted
# command. `wsl.exe -- bash -lc 'cmd; echo $?'` prints 0 whatever happened.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

WEEK="${1:-}"
[ -n "$WEEK" ] || gdie "usage: gate.sh <1..5|preflight>"

uavx_load_env

# ---------------------------------------------------------------- preflight
# Runs before every week. Proves the shell the gate is standing in is real.
gate_preflight() {
  # Round 3 finding 4: registration and eligibility were filed as open items,
  # not blocking. An unregistered or ineligible entrant has no valid submission
  # however good the simulation is, and eligibility disqualifies at any stage.
  # Five weeks of work behind an invalid entry is the worst available outcome,
  # so the loop refuses to start without a receipt. See stage-1/human-preflight.md.
  gsay "preflight: human dependencies"
  local hp="${UAVX_REPO}/submission/human-preflight.json"
  if [ ! -f "$hp" ]; then
    gdie "no ${hp}. Registration, eligibility, the clarification channel, the organiser questions and the delivery method are human steps and none of them can be done by an agent. See stage-1/human-preflight.md."
  fi
  python3 - "$hp" <<'PYHP' || gdie "human preflight receipt is incomplete"
import json, sys
need = ["registered", "eligibility", "clarification_channel", "organiser_email", "delivery"]
d = json.load(open(sys.argv[1], encoding="utf-8"))
missing = [k for k in need if not d.get(k)]
if missing:
    print(f"  missing: {', '.join(missing)}")
    sys.exit(1)
if not d["registered"].get("done"):
    print("  registration has no date")
    sys.exit(1)
if not d["eligibility"].get("declaration"):
    print("  eligibility has no declaration")
    sys.exit(1)
if not d["delivery"].get("attachment_limit_mb"):
    print("  delivery has no attachment budget, so W5 cannot check the package fits")
    sys.exit(1)
print(f"  registered {d['registered']['done']}, eligibility declared, delivery budget {d['delivery']['attachment_limit_mb']} MB")
PYHP

  # Round 3 finding 10: agents were handed files that told them both to use and
  # never to use the launcher that crashes the distro. Prose drifts because
  # nothing checks it.
  gsay "preflight: documents agree"
  python3 "${UAVX_REPO}/scripts/check_docs.py" || gdie "the documents contradict each other"

  gsay "preflight: environment"
  uavx_require_ros
  uavx_require_px4_msgs
  uavx_require_sim
  printf '  ros2            %s\n' "$(command -v ros2)"
  printf '  colcon          %s\n' "$(command -v colcon)"
  printf '  gzserver        %s\n' "$(command -v gzserver)"
  printf '  px4             %s\n' "${UAVX_PX4_DIR}/build/px4_sitl_default/bin/px4"
  printf '  agent           %s\n' "$(command -v MicroXRCEAgent || echo MISSING)"
  command -v MicroXRCEAgent >/dev/null 2>&1 || gdie "MicroXRCEAgent missing"

  gsay "preflight: no simulator left running from a previous gate"
  for p in gzserver gzclient px4 MicroXRCEAgent; do
    if pgrep -x "$p" >/dev/null 2>&1; then
      gdie "$p is already running. A previous gate did not clean up; kill it before trusting any result."
    fi
  done
  printf '  clean\n'
}

# Builds our packages once the workspace exists. Before W1 creates it there is
# nothing to build and that is not a failure.
gate_build() {
  if [ ! -d "${UAVX_WS_SRC}/src" ]; then
    gsay "build: ${UAVX_WS_SRC}/src does not exist yet, nothing to build"
    return 0
  fi
  gsay "build: colcon, output on ext4 not on /mnt/c"
  colcon build \
    --base-paths "${UAVX_WS_SRC}" \
    --build-base "${UAVX_BUILD_BASE}" \
    --install-base "${UAVX_INSTALL_BASE}" \
    --symlink-install \
    || gdie "colcon build failed"
  [ -f "${UAVX_INSTALL_BASE}/setup.bash" ] \
    || gdie "colcon reported success and produced no install/setup.bash"
  uavx_source "${UAVX_INSTALL_BASE}/setup.bash" || gdie "cannot source the overlay just built"
}

# colcon test exits 0 with failing tests. test-result is what actually reports.
gate_test() {
  gsay "test: $*"
  colcon test \
    --base-paths "${UAVX_WS_SRC}" \
    --build-base "${UAVX_BUILD_BASE}" \
    --install-base "${UAVX_INSTALL_BASE}" \
    --packages-select "$@" \
    --return-code-on-test-failure \
  || gdie "colcon test failed for: $*"
  colcon test-result --test-result-base "${UAVX_BUILD_BASE}" --verbose \
    || gdie "colcon test-result reported failures for: $*"
}

run_scenario() {
  local scenario="$1"
  [ -f "${UAVX_REPO}/${scenario}" ] || gdie "scenario not found: ${scenario}"
  uavx_invalidate_latest
  gsay "scenario: ${scenario}"
  bash "${UAVX_REPO}/scripts/run_scenario.sh" "${scenario}" \
    || gdie "scenario run failed: ${scenario}"
  [ -f "${UAVX_RUNS_DIR}/latest.jsonl" ] \
    || gdie "scenario finished and produced no runs/latest.jsonl"
}

# The checker verifies provenance before it looks at any metric, so a stale or
# hand-written file cannot satisfy a gate. See uavx_eval/check.py.
check_run() {
  local scenario="$1"; shift
  python3 -m uavx_eval.check "${UAVX_RUNS_DIR}/latest.jsonl" \
    --expect-scenario "${scenario}" \
    "$@" \
    || gdie "metric check failed for ${scenario}"
}

# -------------------------------------------------------------------- weeks
gate_w1() {
  gsay "W1: four vehicles airborne together, headless"
  bash "${UAVX_REPO}/scripts/run_smoke.sh" --vehicles 4 \
    || gdie "smoke run failed"
}

gate_w2() {
  uavx_require_overlay
  uavx_require_module uavx_eval
  gate_test uavx_mission uavx_eval
  run_scenario scenarios/survey_baseline.yaml
  check_run scenarios/survey_baseline.yaml \
    --require "coverage_fraction>=0.95" \
    --require "coverage_source==pose_samples"
}

gate_w3() {
  uavx_require_overlay
  gate_test uavx_comms uavx_msgs

  # Geometry first. It needs no simulator and costs a second, so a topology that
  # cannot demonstrate what the scenario claims fails immediately rather than
  # after a full SITL run.
  gsay "W3: frozen topology"
  python3 "${UAVX_REPO}/scripts/check_geometry.py" || gdie "frozen geometry does not hold"

  # The static seam pass runs here. The LIVE pass cannot: preflight has just
  # proved nothing is running, so ros2 node list would find no graph and W3
  # could never pass. The scenario runner captures a graph snapshot while each
  # scenario is up, and --from-snapshot reads it afterwards.
  gsay "W3: seam, static pass"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --static-only || gdie "tx/rx seam violated in source"

  run_scenario scenarios/relay_required.yaml
  gsay "W3: seam, live graph captured during relay_required"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --from-snapshot \
    "${UAVX_RUNS_DIR}/latest-graph.json" || gdie "tx/rx seam violated at runtime"
  check_run scenarios/relay_required.yaml \
    --require "delivery_ratio>=0.95" \
    --require "delivery_ratio_by_node.uav_4>=0.95" \
    --require "delivered_hops_by_node.uav_4>=2" \
    --require "app_packets_sent_by_node.uav_4>=100"

  run_scenario scenarios/direct_only.yaml
  check_run scenarios/direct_only.yaml \
    --require "delivery_ratio_by_node.uav_4==0" \
    --require "app_packets_sent_by_node.uav_4>=100"
}

gate_w4() {
  uavx_require_overlay
  gsay "W4: frozen topology"
  python3 "${UAVX_REPO}/scripts/check_geometry.py" || gdie "frozen geometry does not hold"
  gate_test uavx_roles uavx_sim
  run_scenario scenarios/relay_kill.yaml
  check_run scenarios/relay_kill.yaml \
    --require "injected_event_observed==true" \
    --require "time_to_reconnect_s<=45" \
    --require "delivery_ratio_after_recovery>=0.90" \
    --require "relay_role_moved==true" \
    --require "pose_sample_count>=1000" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0" \
    --require "collision_contacts==0"

  run_scenario scenarios/encounter.yaml
  check_run scenarios/encounter.yaml \
    --require "yield_events>=1" \
    --require "separation_violations==0" \
    --require "collision_contacts==0"
}

gate_w5() {
  gsay "W5: submission package"
  # Do NOT wrap this in `|| gdie`. The checker exits 2 for "package complete,
  # waiting for a human to send it", which is a different state from "package
  # broken". Collapsing both into 1 leaves the supervisor unable to tell a
  # finished week from a failed one, which is round 3 finding 3.
  set +e
  python3 "${UAVX_REPO}/scripts/check_submission.py"
  rc=$?
  set -e
  case "$rc" in
    0) gsay "W5: submitted and recorded" ;;
    2) gsay "W5: package complete, awaiting the human send. Halting here by design."
       exit 2 ;;
    *) gdie "submission package incomplete" ;;
  esac
}

# ------------------------------------------------------------------ dispatch
case "$WEEK" in
  preflight) gate_preflight ;;
  1) gate_preflight; gate_build; gate_w1 ;;
  2) gate_preflight; gate_build; gate_w2 ;;
  3) gate_preflight; gate_build; gate_w3 ;;
  4) gate_preflight; gate_build; gate_w4 ;;
  5) gate_preflight; gate_build; gate_w5 ;;
  *) gdie "unknown week: ${WEEK}" ;;
esac

gsay "gate ${WEEK} PASSED"
