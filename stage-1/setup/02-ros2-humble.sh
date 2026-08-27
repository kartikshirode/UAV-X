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

# Round 4 finding 8: this installed unversioned Humble packages and nothing
# ever compared them, so a fresh install could run different ROS binaries and
# pass every gate. Ask for the locked version first. Apt archives do drop older
# builds, so if the exact version is gone, install what is there and then fail
# loudly rather than proceed on a stack the evidence did not come off.
DESKTOP_VER="$(lock ros_desktop_version)"
say "installing ros-${ROS_DISTRO_NAME}-desktop=${DESKTOP_VER}, this is the big download"
if ! sudo apt-get install -y "ros-${ROS_DISTRO_NAME}-desktop=${DESKTOP_VER}"; then
  warn "the locked ros-${ROS_DISTRO_NAME}-desktop version is no longer in the archive"
  warn "installing what is available; verify.sh will refuse the mismatch"
  sudo apt-get install -y "ros-${ROS_DISTRO_NAME}-desktop"
fi
sudo apt-get install -y ros-dev-tools
sudo apt-get install -y python3-colcon-common-extensions python3-rosdep

# Say so now, in the install, rather than leaving it for a gate three weeks on.
say "comparing the installed ROS against versions.lock"
ros_mismatch=0
for pair in \
  "ros-${ROS_DISTRO_NAME}-desktop:ros_desktop_version" \
  "ros-${ROS_DISTRO_NAME}-ros-core:ros_core_version" \
  "ros-${ROS_DISTRO_NAME}-rclpy:ros_rclpy_version" \
  "ros-${ROS_DISTRO_NAME}-rclcpp:ros_rclcpp_version" \
  "ros-${ROS_DISTRO_NAME}-rmw-fastrtps-cpp:ros_rmw_fastrtps_cpp_version"; do
  pkg="${pair%%:*}"; key="${pair##*:}"
  want="$(lock "$key")"
  got="$(dpkg-query -W -f='${Version}' "$pkg" 2>/dev/null || echo MISSING)"
  if [ "$want" = "$got" ]; then
    printf '  ok    %-38s %s\n' "$pkg" "$got"
  else
    printf '  FAIL  %-38s locked %s, installed %s\n' "$pkg" "$want" "$got"
    ros_mismatch=1
  fi
done
if [ "$ros_mismatch" -ne 0 ]; then
  die "the installed ROS does not match versions.lock. Either the archive has moved on, in which case update the lock deliberately and rerun every scenario, or something else changed it. Do not edit the lock to make this pass."
fi

if [ ! -f /etc/ros/rosdep/sources.list.d/20-default.list ]; then
  sudo rosdep init
fi
rosdep update

if ! grep -q "source /opt/ros/${ROS_DISTRO_NAME}/setup.bash" "$HOME/.bashrc"; then
  echo "source /opt/ros/${ROS_DISTRO_NAME}/setup.bash" >> "$HOME/.bashrc"
fi

done_with ros2
say "ros2 done. check with: source /opt/ros/${ROS_DISTRO_NAME}/setup.bash && ros2 doctor --report"
