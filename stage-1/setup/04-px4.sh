#!/usr/bin/env bash
# PX4 Autopilot at the commit in versions.lock, built for SITL against Gazebo
# Classic. It used to resolve the newest v1.15.x tag, which meant a fresh
# install in September could build something nobody had ever run.

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
  # --no-sim-tools matters more than --no-nuttx and it went in late, found while
  # building the cluster image. Read Tools/setup/ubuntu.sh at the pinned commit,
  # lines 220 to 240: on Ubuntu 22.04 its simulation branch prints "Gazebo
  # (Garden) will be installed. Earlier versions will be removed", adds
  # packages.osrfoundation.org to the apt sources and installs gz-garden.
  #
  # That is precisely the sequence 03-gazebo-classic.sh exists to undo, and
  # setup-all.sh runs 03 and then 04, so this step was putting back what the
  # step before it had just taken out. A fresh machine following INSTALL.md
  # would have ended with the Garden repo enabled and Classic at risk on the
  # next apt upgrade. This one escaped because 03 was evidently re-run by hand
  # afterwards; gz-plugin2-cli and gz-transport12-cli are still installed here
  # as residue, while the repo itself is gone.
  #
  # Every simulation dependency that branch installs is listed by hand below,
  # minus the Garden packages, and minus ant and openjdk, which exist only for
  # jmavsim and nothing in this repo uses jmavsim. This exact sequence built PX4
  # and flew four vehicles inside the cluster image, with all four git SHAs and
  # all six apt pins matching versions.lock.
  say "PX4 dependency script, without NuttX and without its simulation branch"
  bash ./Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools

  say "the simulation dependencies PX4 needs, without the Garden packages"
  sudo apt-get update
  sudo apt-get install -y --no-install-recommends \
    bc dmidecode gstreamer1.0-libav gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-ugly libeigen3-dev \
    libgstreamer-plugins-base1.0-dev libimage-exiftool-perl libopencv-dev \
    libxml2-utils pkg-config protobuf-compiler \
    || die "the PX4 simulation dependencies would not install"

  # Not decoration. This is what catches the same thing again if a future PX4
  # pin moves the Garden logic somewhere else in that script.
  [ ! -f /etc/apt/sources.list.d/gazebo-stable.list ] \
    || die "the PX4 dependency script put the osrfoundation repo back. It serves Garden on jammy and 03-gazebo-classic.sh exists to keep it off this machine."
  [ "$(dpkg -l 2>/dev/null | grep -c '^ii  gz-garden' || true)" -eq 0 ] \
    || die "gz-garden is installed. Gazebo Classic is the pinned simulator and Garden conflicts with it."
  for b in gzserver gzclient gazebo; do
    command -v "$b" > /dev/null 2>&1 \
      || die "the PX4 dependency install removed ${b}. Classic was installed by step 03 and something has taken it away."
  done
  say "gazebo is still $(dpkg-query -W -f='${Version}' gazebo 2>/dev/null || echo unknown)"

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
