#!/usr/bin/env bash
# Run one scenario and publish one trustworthy record.
#
# The contract is stage-1/architecture.md section 1b, under
# `scripts/run_scenario.sh`, and it is frozen. scripts/gate.sh, both rehearsal
# wrappers and scripts/fresh_install.sh were all written against this script
# before it existed, and between them they use every flag below.
#
#   <scenario>            path to the scenario YAML. Required
#   --run-id <id>         use this id rather than minting one
#   --record <path>       capture video to this file, headless
#   --record-seconds <n>  how much to capture. The run continues past it
#   --overlay-text <s>    burn this into every frame
#   --runs-dir <dir>      where records go, default runs/
#
# Exit codes are fixed:  0 complete with both artifacts, 10 invalid arguments
# or YAML, 20 a missing dependency, 30 a child process failed, 31 the scenario
# timed out, 32 an artifact failed schema or provenance validation, 40 recording
# failed. No `latest` file is written for any non-zero exit.
#
# Everything below the argument checks belongs to uavx_sim.scenario_runner.
# This script exists to do three things a Python module should not: check the
# arguments before anything is launched, so exit 10 costs nothing and leaves
# nothing behind; load the ROS environment, because Ubuntu's .bashrc returns on
# its second line for a non-interactive shell and nothing is inherited; and
# guarantee teardown even if the runner dies in a way it cannot handle.
#
# The ROS setup files read unbound variables by design, so they are sourced
# through `uavx_source`, which saves the `-u` flag and puts it back. Sourcing
# one directly under `set -euo pipefail` kills the caller on
# `COLCON_TRACE: unbound variable`.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

usage() {
  printf 'usage: %s <scenario.yaml> [--run-id ID] [--runs-dir DIR]\n' "$0"
  printf '                        [--record PATH --record-seconds N --overlay-text TEXT]\n'
}

# Two exits, two meanings, and neither of them is gdie. gdie exits 1, and 1 is
# not one of the codes this script is allowed to return.
bad_args() { printf '  FAIL  %s\n' "$*" >&2; exit 10; }
no_dependency() { printf '  FAIL  %s\n' "$*" >&2; exit 20; }

SCENARIO=""
RUN_ID=""
RECORD=""
RECORD_SECONDS=""
OVERLAY_TEXT=""
RUNS_DIR="${UAVX_RUNS_DIR}"

# ------------------------------------------------------------------ arguments
# Before the environment, before the launcher, before the runs directory is
# even looked at. Round 8 asked for an invalid launch to take its documented
# code and leave neither latest artifact behind, and the cheapest way to be
# sure of that is to fail before anything exists to leave behind.
while [ $# -gt 0 ]; do
  case "$1" in
    --run-id)
      [ $# -ge 2 ] || bad_args "--run-id needs a value"
      RUN_ID="$2"; shift 2 ;;
    --record)
      [ $# -ge 2 ] || bad_args "--record needs a path"
      RECORD="$2"; shift 2 ;;
    --record-seconds)
      [ $# -ge 2 ] || bad_args "--record-seconds needs a number"
      RECORD_SECONDS="$2"; shift 2 ;;
    --overlay-text)
      [ $# -ge 2 ] || bad_args "--overlay-text needs a string"
      OVERLAY_TEXT="$2"; shift 2 ;;
    --runs-dir)
      [ $# -ge 2 ] || bad_args "--runs-dir needs a directory"
      RUNS_DIR="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    -*)
      usage >&2
      bad_args "unknown option: $1" ;;
    *)
      [ -z "$SCENARIO" ] || bad_args "two scenarios given, ${SCENARIO} and $1. One run is one scenario."
      SCENARIO="$1"; shift ;;
  esac
done

[ -n "$SCENARIO" ] || { usage >&2; bad_args "no scenario given"; }
[ -n "$RUNS_DIR" ] || bad_args "--runs-dir must not be empty"

# A relative path is resolved against the repository, not against whatever
# directory the caller happened to be in, because the record cites its scenario
# by repository relative path.
if [ ! -f "$SCENARIO" ] && [ -f "${UAVX_REPO}/${SCENARIO}" ]; then
  SCENARIO="${UAVX_REPO}/${SCENARIO}"
fi
[ -f "$SCENARIO" ] \
  || bad_args "no scenario at ${SCENARIO}. Nothing was launched and nothing was written."

if [ -n "$RUN_ID" ]; then
  # architecture.md section 1b freezes the grammar, and the id is also a
  # filename. A dash or a slash here would be a path this script then writes to.
  case "$RUN_ID" in
    *[!A-Za-z0-9_]*) bad_args "run id ${RUN_ID} is not [A-Za-z0-9_]+" ;;
  esac
  [ "${#RUN_ID}" -ge 8 ] \
    || bad_args "run id ${RUN_ID} is shorter than the 8 characters the record schema requires"
fi

if [ -n "$RECORD_SECONDS" ]; then
  case "$RECORD_SECONDS" in
    ''|*[!0-9.]*|.|*.*.*) bad_args "--record-seconds must be a number, got ${RECORD_SECONDS}" ;;
  esac
  # The number is compared against the scenario duration inside the runner,
  # which is the only place that has read the YAML.
  awk -v n="$RECORD_SECONDS" 'BEGIN { exit (n > 0) ? 0 : 1 }' \
    || bad_args "--record-seconds must be positive, got ${RECORD_SECONDS}"
fi

if [ -n "$RECORD" ]; then
  [ -n "$RECORD_SECONDS" ] \
    || bad_args "--record requires --record-seconds"
  [ -n "$OVERLAY_TEXT" ] \
    || bad_args "--record requires --overlay-text. A clip nobody can tie to a run proves nothing."
elif [ -n "$RECORD_SECONDS" ] || [ -n "$OVERLAY_TEXT" ]; then
  bad_args "--record-seconds and --overlay-text only mean anything with --record"
fi

# ---------------------------------------------------------------- environment
uavx_source "/opt/ros/${ROS_DISTRO_NAME}/setup.bash" \
  || no_dependency "no ROS at /opt/ros/${ROS_DISTRO_NAME}. Run stage-1/setup/setup-all.sh."
uavx_source "${UAVX_DEPS_WS}/install/setup.bash" \
  || no_dependency "no dependency workspace at ${UAVX_DEPS_WS}. Run stage-1/setup/05-ros2-bridge.sh."
uavx_source "${UAVX_INSTALL_BASE}/setup.bash" \
  || no_dependency "the repo overlay is not built. Run: colcon build --base-paths ${UAVX_WS_SRC}"

python3 -c "import uavx_sim.scenario_runner" >/dev/null 2>&1 \
  || no_dependency "uavx_sim.scenario_runner is not importable. The overlay built but did not install the package, or it did not build at all."

# ------------------------------------------------------------------ teardown
# Installed here and not one line earlier. Everything above this point either
# launches nothing or is an argument check, and a trap that tears down a
# simulator on an invalid-argument exit would be a side effect of typing a flag
# wrong. Below it, the runner has its own teardown and this is the net under it:
# a Python process killed outright still leaves a machine with nothing running.
runner_teardown() {
  local rc=$?
  trap - INT TERM EXIT
  if [ "$rc" -ne 0 ]; then
    pkill -x px4 >/dev/null 2>&1 || true
    pkill -x MicroXRCEAgent >/dev/null 2>&1 || true
    pkill -x gzserver >/dev/null 2>&1 || true
    pkill -x gzclient >/dev/null 2>&1 || true
    sleep 2
    pkill -KILL -x px4 >/dev/null 2>&1 || true
    pkill -KILL -x MicroXRCEAgent >/dev/null 2>&1 || true
    pkill -KILL -x gzserver >/dev/null 2>&1 || true
    pkill -KILL -x gzclient >/dev/null 2>&1 || true
  fi
  exit "$rc"
}
trap runner_teardown INT TERM EXIT

RUNNER_ARGS=("$SCENARIO" --runs-dir "$RUNS_DIR")
if [ -n "$RUN_ID" ]; then
  RUNNER_ARGS+=(--run-id "$RUN_ID")
fi
if [ -n "$RECORD" ]; then
  RUNNER_ARGS+=(--record "$RECORD" --record-seconds "$RECORD_SECONDS" \
                --overlay-text "$OVERLAY_TEXT")
fi

# The runner owns every remaining exit code. Read its status directly rather
# than through a pipeline, because a pipe would report the last stage's status
# and every code above zero is part of the contract.
set +e
python3 -m uavx_sim.scenario_runner "${RUNNER_ARGS[@]}"
RUNNER_RC=$?
set -e
exit "$RUNNER_RC"
