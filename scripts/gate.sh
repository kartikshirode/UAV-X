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
  # Round 4 finding 7: this used to check three fields by hand and accept any
  # truthy object for the rest, so a half-written receipt passed. It now runs
  # against submission/human-preflight.schema.json, which is also what W5 reads
  # the delivery budget out of.
  gsay "preflight: human dependencies"
  python3 "${UAVX_REPO}/scripts/check_human_preflight.py" \
    || gdie "human preflight is incomplete. See stage-1/human-preflight.md."

  # Round 3 finding 10: agents were handed files that told them both to use and
  # never to use the launcher that crashes the distro. Prose drifts because
  # nothing checks it.
  gsay "preflight: documents agree"
  python3 "${UAVX_REPO}/scripts/check_docs.py" || gdie "the documents contradict each other"

  # Round 6, found while fixing finding 2. bash 5.1, which is what Ubuntu 22.04
  # ships and therefore what every gate runs under, exits 0 when a script fails
  # to parse after one successful command. `bash -n` exits 0 on the same file.
  # A .sh edited from the Windows side and saved with CRLF dies on line 1 and
  # reports success. verify.sh did precisely this and printed "all checks
  # passed" having run none of them.
  gsay "preflight: the shell scripts parse"
  bash "${UAVX_REPO}/scripts/check_shell.sh" || gdie "a shell script in this repo is broken"

  # The organisers can change the rules or the timeline at any point, and the
  # only way we would find out is by looking.
  #
  # Round 6 finding 8: this ran with --allow-offline and `|| gdie`, so exit 3,
  # the code that means "offline, but a real check happened inside the last
  # seven days", killed the week. That is the documented fallback for the WSL
  # DNS drops, and it could never once have worked. W1 to W4 take 0 or 3. W5
  # calls this function with UAVX_SPEC_STRICT=1 and takes only 0.
  gsay "preflight: the published competition record"
  # Overridable only so test_gate_preflight.py can hand it a checker that
  # returns each documented code on demand. Every real week runs the real one.
  # >>> spec-decision, extracted verbatim by test_gate_preflight.py
  local spec_checker="${UAVX_SPEC_CHECKER:-${UAVX_REPO}/scripts/check_competition_spec.py}"
  local spec_rc=0
  if [ "${UAVX_SPEC_STRICT:-0}" = "1" ]; then
    python3 "$spec_checker" || spec_rc=$?
    [ "$spec_rc" -eq 0 ] \
      || gdie "W5 checks the published record online. It exited ${spec_rc}; 1 means it could not be read, 2 means it changed, 3 means nobody has read it today. None of those is a state to send from."
  else
    python3 "$spec_checker" --allow-offline || spec_rc=$?
    case "$spec_rc" in
      0) ;;
      3) gsay "preflight: offline, working from a check inside the seven day limit" ;;
      *) gdie "the published competition record has changed, or has not been read recently enough. Read the diff before doing any more work." ;;
    esac
  fi
  # <<< spec-decision

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

  # Round 3 finding 7: the version verifier existed but no gate ran it, so gates
  # could run happily after a checkout changed the stack under them.
  gsay "preflight: stack matches versions.lock"
  bash "${UAVX_REPO}/stage-1/setup/verify.sh" >/dev/null 2>&1     || gdie "verify.sh failed. The installed stack does not match stage-1/setup/versions.lock. Run it directly to see which pin drifted."
  printf '  locked
'

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
  # scenario is up, and --snapshot reads it afterwards.
  gsay "W3: seam, static pass"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --static-only || gdie "tx/rx seam violated in source"

  run_scenario scenarios/relay_required.yaml
  gsay "W3: seam, graph captured during relay_required"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --snapshot \
    "${UAVX_RUNS_DIR}/latest-graph.json" --scenario relay_required \
    --expect-run "${UAVX_RUNS_DIR}/latest.jsonl" \
    || gdie "tx/rx seam violated at runtime"
  check_run scenarios/relay_required.yaml \
    --require "delivery_ratio>=0.95" \
    --require "delivery_ratio_by_node.uav_4>=0.95" \
    --require "delivered_hops_by_node.uav_4>=2" \
    --require "app_packets_sent_by_node.uav_4>=100"

  run_scenario scenarios/direct_only.yaml
  check_run scenarios/direct_only.yaml \
    --require "delivery_ratio_by_node.uav_4==0" \
    --require "app_packets_sent_by_node.uav_4>=100"

  # Round 4 finding 1: the plan promises both of these in W3 so W5 does not
  # inherit them, and the gate asked for neither, which meant the promise was
  # decoration. The recording one matters most: the gazebo GUI binary has taken
  # this distro down three times, and the video is a deliverable.
  #
  # Round 6 finding 4: adding check_dryruns.py fixed the wrong half. The
  # gate still only read receipts, and the two wrappers that produce them
  # were never invoked by anything. A week could go green on files left
  # over from the week before, or on files somebody typed.
  #
  # So the stale receipts go first. If a wrapper fails, the gate stops at
  # the wrapper, and there is no old receipt left behind for the checker
  # to accept in its place.
  gsay "W3: the two rehearsals W5 has no room for"
  rm -f "${UAVX_REPO}/submission/dryrun-install-receipt.json" \
        "${UAVX_REPO}/submission/dryrun-install-transcript.log" \
        "${UAVX_REPO}/submission/dryrun-recording-receipt.json" \
        "${UAVX_REPO}/submission/dryrun-recording-run.jsonl" \
        "${UAVX_REPO}/submission/dryrun-recording.mp4"
  bash "${UAVX_REPO}/scripts/rehearse_install.sh" \
    || gdie "the rebuild rehearsal failed. See submission/dryrun-install-transcript.log."
  bash "${UAVX_REPO}/scripts/rehearse_recording.sh" \
    || gdie "the 60 second recording rehearsal failed. Finding this out in W5 leaves four days and no capture path."
  python3 "${UAVX_REPO}/scripts/check_dryruns.py" \
    || gdie "the rehearsals ran and their evidence does not hold up"
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
    --require "observations_set_equal==true" \
    --require "observations.evicted==0" \
    --require "observations.expired==0" \
    --require "relay_slot.clearance_m>=15" \
    --require "relay_slot.band_reserved==true" \
    --require "delivery_ratio_after_recovery>=0.90" \
    --require "relay_role_moved==true" \
    --require "pose_sample_count>=1000" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0" \
    --require "collision_contacts==0"

  # The other half of the fault the organisers name. "Fail OR LOSE
  # CONNECTIVITY", said twice in the published material, and every other
  # scenario tests the first half. A quiet vehicle still occupies airspace and
  # still comes back, and neither of those is true of a dead one.
  run_scenario scenarios/link_loss.yaml
  check_run scenarios/link_loss.yaml \
    --require "injected_event_observed==true" \
    --require "time_to_reconnect_s<=45" \
    --require "relay_role_moved==true" \
    --require "relay_role_holder==uav_3" \
    --require "observations_set_equal==true" \
    --require "observations.evicted==0" \
    --require "observations.expired==0" \
    --require "observations.unexpected_count==0" \
    --require "route_restored_after_blackout==true" \
    --require "relay_role_released==true" \
    --require "mover_returned_to_station==true" \
    --require "outage_count_after_release==0" \
    --require "handback.prepared_path==uav_4>uav_2>uav_1>gcs" \
    --require "handback.confirmed_at<handback.release_at" \
    --require "handback.observation_gap_count==0" \
    --require "relay_slot.clearance_m>=15" \
    --require "relay_slot.band_reserved==true" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0" \
    --require "contact_monitor_samples>0"

  # Round 6 finding 7. Everything above says the handback happened; none of
  # it said who owned it. The coordinator rule is a property of the
  # disconnected component, and the component has merged by the time this
  # runs, so the owner is carried from the election instead of recomputed.
  # It is never the relay: uav_3 is both the lowest id member of the
  # component and the elected mover, so ownership passes to uav_4. An
  # implementation that takes any of the three other defensible readings
  # fails here rather than in front of a judge.
  check_run scenarios/link_loss.yaml \
    --require "handback.epoch_owner==uav_4" \
    --require "handback.epoch_owner!=relay_role_holder" \
    --require "handback.staying_member==uav_4" \
    --require "handback.release_sender==uav_4" \
    --require "handback.prepared_path_computations>=2" \
    --require "handback.confirmed_observation_id=~^uav_4:[0-9]+$" \
    --require "observations.backlog_drain_s<=2.25" \
    --require "observations.control_queue_max_delay_s<=0.05"
  gsay "W4: the swarm recovered a link it lost and gave the vehicle back, with one named node owning the transaction"

  # Round 6 finding 5. The queue is sized against a 45 second outage and the
  # drain bound of 2.25 s comes off that number, but the two accepted
  # recoveries are 32.5 s and 28.0 s, so nothing had ever held the route
  # down for the duration the arithmetic assumes. This one holds it for the
  # full 45 s and never restores the relay, which is the depth the store
  # and forward design claims to survive.
  run_scenario scenarios/queue_drain.yaml
  check_run scenarios/queue_drain.yaml \
    --require "injected_event_observed==true" \
    --require "outage_duration_s>=45" \
    --require "observations.generated>=450" \
    --require "observations_set_equal==true" \
    --require "observations.unexpected_count==0" \
    --require "observations.evicted==0" \
    --require "observations.expired==0" \
    --require "observations.peak_queue_depth<=512" \
    --require "observations.peak_queue_depth>=450" \
    --require "observations.backlog_drain_s<=2.25" \
    --require "observations.control_queue_max_delay_s<=0.05" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0"
  gsay "W4: 45 seconds of backlog delivered exactly once, and control never queued behind it"

  # Safety, with a negative control. Round 3 finding 8: without the control a
  # pass is indistinguishable from two vehicles that happened to miss each
  # other, and zero contacts is indistinguishable from no contact monitor.
  run_scenario scenarios/encounter.yaml
  check_run scenarios/encounter.yaml \
    --require "yield_events_by_node.uav_4>=1" \
    --require "yield_hold_seconds>0" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0" \
    --require "collision_contacts==0" \
    --require "contact_monitor_samples>0" \
    --require "pose_sample_count>=1000" \
    --require "vehicles_completed==2"

  run_scenario scenarios/encounter_noyield.yaml
  check_run scenarios/encounter_noyield.yaml \
    --require "separation_violations>=1" \
    --require "contact_monitor_samples>0"
  gsay "W4: the control violated separation, so the yield rule caused the safe result"

  # Round 4 finding 1: W4 ran three scenarios that each prove one subsystem and
  # never ran the one that proves they compose, while the plan called that run
  # the proof of concept and pointed the proposal and the video at it. A week
  # can go green without its own headline result only if nothing checks.
  run_scenario scenarios/mission_integrated.yaml

  # And the seam again, on the W4 graph. W3 accepted the seam over a graph with
  # no role managers in it, because uavx_roles did not exist yet. Without this
  # the newest code in the swarm is the only code the seam check never sees.
  gsay "W4: seam, static pass now that uavx_roles exists"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --static-only || gdie "tx/rx seam violated in source"
  gsay "W4: seam, graph captured during the integrated mission"
  bash "${UAVX_REPO}/scripts/check_seam.sh" --snapshot \
    "${UAVX_RUNS_DIR}/latest-graph.json" --scenario mission_integrated \
    --expect-run "${UAVX_RUNS_DIR}/latest.jsonl" \
    || gdie "tx/rx seam violated at runtime, with roles running"

  check_run scenarios/mission_integrated.yaml \
    --require "coverage_fraction>=0.95" \
    --require "coverage_source==pose_samples" \
    --require "coverage_fraction_at_kill<=0.80" \
    --require "injected_event_observed==true" \
    --require "observations.generated>=100" \
    --require "observations.evicted==0" \
    --require "observations.expired==0" \
    --require "observations_set_equal==true" \
    --require "observations.unexpected_count==0" \
    --require "relay_slot.clearance_m>=15" \
    --require "relay_role_moved==true" \
    --require "relay_role_holder==uav_3" \
    --require "strip_reassigned_to==uav_4" \
    --require "time_to_reconnect_s<=45" \
    --require "delivered_hops_by_node.uav_4>=2" \
    --require "min_pairwise_separation_m>=10" \
    --require "separation_violations==0" \
    --require "collision_contacts==0" \
    --require "contact_monitor_samples>0" \
    --require "pose_sample_count>=1000"
  gsay "W4: the swarm surveyed, lost its relay, elected, repositioned and finished"
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
  5) export UAVX_SPEC_STRICT=1; gate_preflight; gate_build; gate_w5 ;;
  *) gdie "unknown week: ${WEEK}" ;;
esac

gsay "gate ${WEEK} PASSED"
