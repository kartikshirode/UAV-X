#!/usr/bin/env bash
# Gazebo Classic 11, from Ubuntu 22.04 universe.
#
# Do NOT add the osrfoundation repo here. Gazebo Classic went end of life in
# January 2025 and packages.osrfoundation.org/gazebo/ubuntu-stable now serves
# Gazebo Garden for jammy instead. Adding it pulls in gz-garden, gz-tools2 and
# the libgz-* stack, and gz-tools2 conflicts with the `gazebo` package, so apt
# quietly resolves the conflict by installing the Classic *libraries* and
# skipping the Classic *binaries*. The result looks installed and has no
# gzserver, which is what PX4 actually needs. That cost a full build cycle.
#
# jammy universe has gazebo 11.10.2+dfsg-1, binaries included. That is the one.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did gazebo; then say "gazebo already done, skipping"; exit 0; fi

if [ -f /etc/apt/sources.list.d/gazebo-stable.list ]; then
  say "removing the osrfoundation repo, it serves Garden on jammy and blocks Classic"
  sudo rm -f /etc/apt/sources.list.d/gazebo-stable.list
  sudo apt-get update
fi

if dpkg -l 2>/dev/null | grep -q '^ii  gz-garden'; then
  say "removing the Gazebo Garden stack, it conflicts with Gazebo Classic"
  sudo apt-get remove -y gz-garden gz-tools2 || true
  sudo apt-get autoremove -y || true
fi

say "installing gazebo classic 11 from jammy universe"
sudo apt-get install -y gazebo libgazebo-dev
sudo apt-get install -y "ros-${ROS_DISTRO_NAME}-gazebo-ros-pkgs" || \
  warn "gazebo_ros_pkgs not installed. Not needed for PX4 SITL, only for a ROS-side gazebo plugin later."

# Assert on the binaries, never on package metadata. dpkg-query -W returns a
# version for a package apt merely knows about, so a version check passes on a
# machine where nothing was installed. The file either exists or it does not.
for bin in gzserver gzclient gazebo; do
  command -v "$bin" >/dev/null 2>&1 || die "${bin} missing after install. Gazebo Classic did not land."
done

say "gazebo classic $(dpkg-query -W -f='${Version}' gazebo 2>/dev/null), gzserver at $(command -v gzserver)"

done_with gazebo
say "gazebo done"
