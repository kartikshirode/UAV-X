# Installing the UAV-X simulation stack

Takes a fresh Windows 11 machine to 4 PX4 vehicles flying together in Gazebo Classic with ROS 2 seeing all of them. Reasoning behind the version choices is in [decisions.md](../decisions.md).

**Status: not yet run end to end.** The scripts are written and syntax checked; the first real run is in progress. This file gets corrected the moment reality disagrees with it.

## What you end up with

| Piece | Version |
| --- | --- |
| Distro | Ubuntu 22.04 LTS under WSL2 |
| ROS 2 | Humble Hawksbill, desktop |
| Simulator | Gazebo Classic 11 |
| PX4 | newest v1.15.x tag |
| Bridge | Micro XRCE-DDS Agent v2.4.2, px4_msgs, px4_ros_com |

## Before you start

On the Windows side, in PowerShell:

```powershell
wsl --install -d Ubuntu-22.04 --no-launch
wsl -d Ubuntu-22.04
```

`--no-launch` skips the first-run username prompt so the download can happen unattended. Launching it afterwards is where you set the username and password.

Budget 15 GB of disk and an hour or two of wall clock. Most of that is downloads and the PX4 build, and neither wants your attention.

## Running it

Inside the Ubuntu shell:

```bash
cd /mnt/c/Users/Kartik/Documents/Kartik/EDU/Local/Projects/Techfest/PUSHPAK-Grand-Challenge/UAV-X
bash stage-1/setup/setup-all.sh
```

Reruns are safe. Each step drops a marker in `~/.uavx-setup` when it finishes, so a second run skips whatever already worked and picks up at the step that broke.

Steps in order:

| Script | Does |
| --- | --- |
| `01-base.sh` | apt update, build tooling, locale, universe repo |
| `02-ros2-humble.sh` | ROS 2 Humble desktop, colcon, rosdep |
| `03-gazebo-classic.sh` | gazebo11 from the osrfoundation repo |
| `04-px4.sh` | clones PX4 at the newest v1.15.x tag, runs its dependency script, builds `px4_sitl gazebo-classic` |
| `05-ros2-bridge.sh` | Micro XRCE-DDS Agent, then a colcon workspace holding px4_msgs and px4_ros_com |

Then open a fresh shell so the `.bashrc` lines take effect, and check:

```bash
bash stage-1/setup/verify.sh
```

It prints ok or FAIL per item and exits non-zero if anything is missing. Nothing flies during verify.

## Things that go wrong here

**Gazebo Classic gets installed on its own, not by PX4.** `Tools/setup/ubuntu.sh` pulls a different simulator depending on PX4 version and distro, which is exactly the mismatch that eats week 1. Script 03 pins gazebo11 from osrfoundation before PX4 is touched.

**The ROS signing key expired in 2025.** Old install guides tell you to drop a keyring into `/usr/share/keyrings` by hand and those instructions now fail. Script 02 installs the `ros2-apt-source` package instead, which handles rotation.

**No `--depth 1` on the PX4 clone.** PX4's build reads `git describe` for its version string and a shallow clone breaks it.

**GUI under WSLg.** If `verify.sh` reports no DISPLAY and no WAYLAND_DISPLAY, WSLg isn't giving you a display and Gazebo's GUI won't open. That isn't a blocker for the work; run SITL with `HEADLESS=1` and everything except the picture still happens. It does block recording the demo video, so it has to be fixed before 20 September.

**PX4's dependency script wants a lot of apt.** `--no-nuttx` is passed because nothing here targets real flight hardware. If you ever do want to flash a board, rerun it without that flag.
