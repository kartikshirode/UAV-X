#!/usr/bin/env bash
# PX4 Autopilot, newest v1.15.x tag, built for SITL against Gazebo Classic.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did px4; then say "px4 already done, skipping"; exit 0; fi

wait_for_net github.com

# Exact commit from versions.lock. The old code resolved the newest v1.15.*
# tag, which means a fresh install in September could build something nobody
# has ever run and produce results the submission would then be claiming.
PX4_SHA="$(lock px4_sha)"
checkout_locked https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR" "$PX4_SHA" "PX4"

cd "$PX4_DIR"
say "PX4 checkout is $(git describe --tags 2>/dev/null || echo unknown)"

if ! already_did px4-deps; then
  say "PX4 dependency script, skipping the NuttX cross-compiler since this is SITL only"
  bash ./Tools/setup/ubuntu.sh --no-nuttx
  done_with px4-deps
fi

# `make px4_sitl gazebo-classic` builds AND LAUNCHES the simulator. That is the
# documented way to fly it, and the wrong thing for a provisioning script: it
# ends by running sitl_run.sh, which starts gzserver and gzclient and then fails
# the build if the sim will not come up. Build the two targets separately.
say "building the PX4 SITL binary, slow on a first run"
make px4_sitl_default || die "PX4 SITL build failed. Read the last 40 lines above before rerunning."

say "building the gazebo-classic plugins"
make px4_sitl_default sitl_gazebo-classic || die "sitl_gazebo-classic build failed."

[ -x "$PX4_DIR/build/px4_sitl_default/bin/px4" ] || die "px4 binary missing after a build that reported success"

done_with px4
say "px4 done"
