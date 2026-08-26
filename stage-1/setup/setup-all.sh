#!/usr/bin/env bash
# Runs every step in order. Safe to rerun; finished steps skip themselves.
# Expect an hour or more on a cold machine, most of it downloads and the PX4 build.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

for step in 01-base 02-ros2-humble 03-gazebo-classic 04-px4 05-ros2-bridge; do
  printf '\n########## %s\n' "$step"
  bash "$HERE/${step}.sh"
done

printf '\nAll steps reported done. Open a new shell, then run stage-1/setup/verify.sh\n'
