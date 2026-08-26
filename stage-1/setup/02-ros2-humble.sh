#!/usr/bin/env bash
# ROS 2 Humble Hawksbill, desktop install.
# Uses the ros2-apt-source package rather than a hand-placed keyring, because the
# old ROS signing key expired in 2025 and this package handles rotation.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did ros2; then say "ros2 already done, skipping"; exit 0; fi

say "adding the ROS 2 apt source"
wait_for_net api.github.com
. /etc/os-release
ROS_APT_SOURCE_VERSION="$($CURL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
  | grep -F '"tag_name"' | awk -F'"' '{print $4}')"
[ -n "$ROS_APT_SOURCE_VERSION" ] || die "could not resolve the ros-apt-source release tag"

$CURL -o /tmp/ros2-apt-source.deb \
  "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.${VERSION_CODENAME}_all.deb"
sudo apt-get install -y /tmp/ros2-apt-source.deb
sudo apt-get update

say "installing ros-${ROS_DISTRO_NAME}-desktop, this is the big download"
sudo apt-get install -y "ros-${ROS_DISTRO_NAME}-desktop" ros-dev-tools
sudo apt-get install -y python3-colcon-common-extensions python3-rosdep

if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  sudo rosdep init
fi
rosdep update

if ! grep -q "source /opt/ros/${ROS_DISTRO_NAME}/setup.bash" "$HOME/.bashrc"; then
  echo "source /opt/ros/${ROS_DISTRO_NAME}/setup.bash" >> "$HOME/.bashrc"
fi

done_with ros2
say "ros2 done. check with: source /opt/ros/${ROS_DISTRO_NAME}/setup.bash && ros2 doctor --report"
