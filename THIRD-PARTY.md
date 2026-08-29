# Third party components

The competition rules make the entrant responsible for not infringing anyone else's intellectual property. This file says what this submission is built on and under what terms.

Nothing listed here is redistributed in the source archive. Every one of them is fetched at install time by the scripts in `stage-1/setup/`, pinned to the exact commit or package version in [stage-1/setup/versions.lock](stage-1/setup/versions.lock). What this archive contains is our own code, plus the instructions that go and get the rest.

Every licence below was read off the copy actually installed on the machine that produced the results, not looked up. The command that reads them is in the notes at the bottom, so the table can be rechecked rather than believed.

## Components

| Component | Version pinned in `versions.lock` | Licence | Upstream |
| --- | --- | --- | --- |
| PX4-Autopilot | `px4_sha` 99c4040 (v1.15.4) | BSD 3-Clause | https://github.com/PX4/PX4-Autopilot |
| Micro XRCE-DDS Agent | `xrce_agent_sha` 7362281 (v2.4.3) | Apache 2.0 | https://github.com/eProsima/Micro-XRCE-DDS-Agent |
| px4_msgs | `px4_msgs_sha` a1045ec | BSD 3-Clause | https://github.com/PX4/px4_msgs |
| px4_ros_com | `px4_ros_com_sha` 86e9aeb | BSD 3-Clause | https://github.com/PX4/px4_ros_com |
| Gazebo Classic | `gazebo_version` 11.10.2+dfsg-1 | Apache 2.0, with BSD 3-Clause parts | https://github.com/gazebosim/gazebo-classic |
| ROS 2 Humble Hawksbill | `ros_desktop_version` 0.10.0-1jammy | Apache 2.0 | https://github.com/ros2 |
| Ubuntu 22.04 LTS | `ubuntu_version` 22.04 | various, per package | https://ubuntu.com |

## What the licences ask for

Both families are permissive and neither restricts what this project does with them. Two obligations attach and both are met by this file existing.

BSD 3-Clause requires the copyright notice, the condition list and the disclaimer to be retained in redistributions of source. PX4, px4_msgs and px4_ros_com are on those terms. This submission does not redistribute their source, so the obligation is discharged by naming them here and pinning the commit that identifies exactly which copy is meant.

Apache 2.0 requires the licence to travel with the work, changed files to be marked, and any NOTICE file to be carried forward. The XRCE agent, Gazebo Classic and ROS 2 are on those terms. **No file in any of them has been modified.** Every one is installed unchanged from its pinned commit or package version, which is what makes that statement checkable rather than a claim.

## What is ours

Everything under `uavx_ws/`, `scenarios/`, `scripts/` and `stage-1/`. Terms are in [LICENSE](LICENSE). Nothing in this project vendors, forks or copies third party source into our tree.

## Rechecking this table

The licences above came from the installed copies, read like this inside the WSL environment:

```bash
for d in ~/PX4-Autopilot ~/src/Micro-XRCE-DDS-Agent \
         ~/ws_uavx/src/px4_msgs ~/ws_uavx/src/px4_ros_com; do
  head -3 "$d"/LICENSE
done
grep -iE '^License:' /usr/share/doc/gazebo/copyright
grep -iE '^License:' /usr/share/doc/ros-humble-rclcpp/copyright
```

Rerun it if a pin in `versions.lock` moves. A licence can change between releases, and a pin that moves without this table moving is how a table stops being true.
