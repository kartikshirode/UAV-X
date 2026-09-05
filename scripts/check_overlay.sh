#!/usr/bin/env bash
# Is a built overlay one that `ros2 run` can actually use? Chunk 3.6.
#
# The rebuild rehearsal used to answer this by sourcing the overlay under
# `set -u` and looking for `ros2`. Both halves were wrong. colcon's setup.bash
# has never been `set -u` clean, so the step exited 127 against a build that
# had just finished cleanly; and finding `ros2` proves the underlay is on the
# path, not that anything in this workspace was installed anywhere useful.
#
# Chunk 3.1 is why the second half matters. uavx_gcs was missing setup.cfg, so
# its console script installed to bin/ instead of lib/uavx_gcs/, the package
# built, every test passed, and `ros2 run uavx_gcs gcs_node` would have failed
# at the first scenario that needed it. A rehearsal that says the overlay works
# has to mean the executables the frozen scenarios invoke.
#
# Usage:  bash scripts/check_overlay.sh <install prefix>
#
# Exit 0 if the overlay sources and every executable below is where ros2 run
# looks for it. 1 otherwise.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

PREFIX="${1:-}"
[ -n "$PREFIX" ] || gdie "usage: check_overlay.sh <install prefix>"
[ -d "$PREFIX" ] || gdie "no overlay at ${PREFIX}"

# Every `ros2 run <package> <executable>` the frozen scenarios invoke, from
# uavx_sim.survey and uavx_sim.comms. Written out rather than derived: this
# file is what those two are checked against, and deriving the list from them
# would make it agree with itself.
EXECUTABLES="
uavx_sim:scenario_runner
uavx_mission:mission_executor
uavx_eval:metrics_collector
uavx_comms:router
uavx_comms:link_layer
uavx_gcs:gcs_node
"

uavx_load_env
uavx_source "${PREFIX}/setup.bash" \
  || gdie "the overlay at ${PREFIX} has no setup.bash to source"

uavx_require_ros

MISSING=0
for entry in $EXECUTABLES; do
  package="${entry%%:*}"
  executable="${entry##*:}"
  path="${PREFIX}/${package}/lib/${package}/${executable}"
  if [ -x "$path" ]; then
    printf '  ok    %s %s\n' "$package" "$executable"
    continue
  fi
  # Say where it went instead, because "not found" and "installed to the
  # wrong prefix" are different faults with different fixes, and the second
  # one is the one a missing setup.cfg produces.
  stray="$(find "${PREFIX}/${package}" -name "$executable" -type f 2>/dev/null | head -1)"
  if [ -n "$stray" ]; then
    printf '  MISSING  %s %s is at %s, not at %s. An ament_python package needs setup.cfg with script_dir=$base/lib/%s.\n' \
      "$package" "$executable" "$stray" "$path" "$package"
  else
    printf '  MISSING  %s %s is nowhere under %s/%s\n' \
      "$package" "$executable" "$PREFIX" "$package"
  fi
  MISSING=$((MISSING + 1))
done

[ "$MISSING" -eq 0 ] \
  || gdie "${MISSING} executable(s) the frozen scenarios invoke are not where ros2 run looks"

printf '  ok    the overlay sources and all 6 executables are installed\n'
