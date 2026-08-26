# Installing the UAV-X simulation stack

Takes a fresh Windows 11 machine to 4 PX4 vehicles flying together in Gazebo Classic with ROS 2 seeing all of them. Reasoning behind the version choices is in [decisions.md](../decisions.md).

**Status: steps 01 to 03 verified on this machine, 26 August. Steps 04 and 05 in progress.** Everything below has been corrected against what actually happened rather than what the documentation promised, and three of the notes at the bottom exist because a run failed on them.

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

**Gazebo Classic comes from Ubuntu universe, not from osrfoundation.** This is the one that cost the most. Gazebo Classic went end of life in January 2025, and `packages.osrfoundation.org/gazebo/ubuntu-stable` now serves Gazebo Garden for jammy. Adding that repo pulls in `gz-garden` and `gz-tools2`, `gz-tools2` conflicts with the `gazebo` package, and apt resolves the conflict quietly: you end up with the Classic *libraries* installed and none of the Classic *binaries*. No `gzserver`, no `gzclient`, which are the two things PX4 SITL actually runs.

It looks fine from the outside. `apt` says "gazebo is already the newest version", `dpkg-query -W` reports 11.10.2, and nothing fails until PX4 tries to launch and says "You need to have gazebo simulator installed!" after a 20 minute build.

jammy universe carries gazebo 11.10.2+dfsg-1 with binaries included. Script 03 installs from there, removes the osrfoundation repo and the Garden stack if a previous run added them, and then checks that `gzserver`, `gzclient` and `gazebo` all exist as executables.

**Check for binaries, never for package versions.** `dpkg-query -W -f='${Version}' gazebo` returns a version for a package apt merely knows about. A version check passes on a machine with nothing installed. `command -v gzserver` does not.

**PX4 builds and launches from the same command.** `make px4_sitl gazebo-classic` is the documented way to fly it, and it ends by running `sitl_run.sh`, which starts gzserver and gzclient and fails the build if the sim will not come up. For provisioning you want `make px4_sitl_default` and then `make px4_sitl_default sitl_gazebo-classic`, which build without launching anything.

**The ROS signing key expired in 2025.** Old install guides tell you to drop a keyring into `/usr/share/keyrings` by hand and those instructions now fail. Script 02 installs the `ros2-apt-source` package instead, which handles rotation.

**No `--depth 1` on the PX4 clone.** PX4's build reads `git describe` for its version string and a shallow clone breaks it.

**Running the gazebo binary kills the whole distro.** Confirmed on this machine, 26 August. `gazebo --version` is enough to do it: the process is killed mid-command, `dmesg` shows `dxgk: dxgkio_query_adapter_info: Ioctl failed` followed by `Init has exited. Terminating distribution`, and every other shell in the distro dies with it. It took three install runs to find, because the symptom looks like a script bug rather than a crashed VM.

So no script here ever invokes `gazebo`. Step 03 and `verify.sh` both ask `dpkg-query` instead, which answers the only question they have. If you need the version at a prompt, use `dpkg-query -W -f='${Version}' gazebo`.

`gzserver`, the headless server PX4 SITL actually drives, is fine. Tested 26 August: it starts, stays up, survives the dxg ioctl failures in dmesg, and leaves the distro alone. Only the GUI-side binary is dangerous. So headless work is safe and the risk is confined to recording the demo video.

**GUI under WSLg.** If `verify.sh` reports no DISPLAY and no WAYLAND_DISPLAY, WSLg isn't giving you a display and Gazebo's GUI won't open. That isn't a blocker for the work; run SITL with `HEADLESS=1` and everything except the picture still happens. It does block recording the demo video, so it has to be fixed before 20 September.

**The XRCE agent tag in the PX4 docs no longer builds.** PX4 v1.15 names Micro XRCE-DDS Agent v2.4.2. Its superbuild clones Fast DDS at branch `2.12.x`, eProsima has deleted that branch, and cmake fails with `Failed to checkout tag: '2.12.x'` after building most of the dependency tree. Nothing about the error suggests the cause is upstream.

v2.4.3 pins Fast DDS `2.14.x` and Fast CDR `2.2.x`, both of which still exist, and that is what `00-common.sh` sets. Since those can vanish too, step 05 now reads the refs out of the agent's own CMakeLists and asks GitHub whether they are still there before starting the build, so a deleted branch fails in seconds naming the repo and ref.

If it ever does fail that way: list the tags with `git ls-remote --tags --refs https://github.com/eProsima/Micro-XRCE-DDS-Agent.git`, pick a newer one, set `XRCE_TAG`, rerun. The guard will tell you straight away whether the new pin is good.

**PX4's dependency script wants a lot of apt.** `--no-nuttx` is passed because nothing here targets real flight hardware. If you ever do want to flash a board, rerun it without that flag.
