#!/usr/bin/env bash
# PX4 Autopilot, newest v1.15.x tag, built for SITL against Gazebo Classic.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did px4; then say "px4 already done, skipping"; exit 0; fi

if [ ! -d "$PX4_DIR/.git" ]; then
  say "resolving the newest ${PX4_BRANCH_GLOB} tag"
  PX4_TAG="${PX4_TAG:-$(git ls-remote --tags --refs https://github.com/PX4/PX4-Autopilot.git "${PX4_BRANCH_GLOB}" \
    | awk -F/ '{print $NF}' | sort -V | tail -1)}"
  [ -n "$PX4_TAG" ] || die "no tag matched ${PX4_BRANCH_GLOB}"
  say "cloning PX4 at ${PX4_TAG} into ${PX4_DIR}"
  # No --depth here on purpose. PX4's build reads git describe for its version string.
  git clone https://github.com/PX4/PX4-Autopilot.git --recursive -b "$PX4_TAG" "$PX4_DIR"
else
  say "PX4 already cloned at ${PX4_DIR}, leaving it alone"
fi

cd "$PX4_DIR"
say "PX4 checkout is $(git describe --tags 2>/dev/null || echo unknown)"

if ! already_did px4-deps; then
  say "PX4 dependency script, skipping the NuttX cross-compiler since this is SITL only"
  bash ./Tools/setup/ubuntu.sh --no-nuttx
  done_with px4-deps
fi

say "building px4_sitl gazebo-classic, this takes a while on first run"
make px4_sitl gazebo-classic || die "PX4 SITL build failed. Read the last 40 lines above before rerunning."

done_with px4
say "px4 done"
