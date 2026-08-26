#!/usr/bin/env bash
# Gazebo Classic (gazebo11) from the OSRF stable repo.
# Installed on its own rather than left to the PX4 setup script, which pulls
# a different simulator depending on version and distro.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did gazebo; then say "gazebo already done, skipping"; exit 0; fi

say "adding the osrfoundation repo"
wait_for_net packages.osrfoundation.org
sudo $CURL https://packages.osrfoundation.org/gazebo.gpg \
  -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
sudo apt-get update

say "installing gazebo11"
sudo apt-get install -y gazebo libgazebo-dev
sudo apt-get install -y "ros-${ROS_DISTRO_NAME}-gazebo-ros-pkgs" || \
  warn "gazebo_ros_pkgs not installed. Not needed for PX4 SITL, only if a ROS-side gazebo plugin is wanted later."

gazebo --version | head -1 || die "gazebo did not run"

done_with gazebo
say "gazebo done"
