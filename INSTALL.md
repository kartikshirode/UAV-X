# Install and smoke test UAV-X

This guide starts from the source archive sent with the Stage 1 package. Run it inside Ubuntu 22.04 under WSL2. It installs the pinned ROS 2, Gazebo Classic, PX4 and XRCE dependencies, builds the UAV-X workspace and flies the four-vehicle smoke test headless.

Allow about 15 GB of free disk space. The first setup can take an hour or two because PX4 and the bridge are built from source. Keep the machine online for that first pass. Do not launch the Gazebo GUI on this stack. The supported path uses `gzserver` only.

## 1. Open the extracted archive

Extract `uavx-source.zip`, open an Ubuntu shell and change into the directory containing this file. The remaining commands assume that is the current directory.

## 2. Install the pinned stack

The setup scripts are safe to resume. A completed step writes a stamp under the current home directory. If a download or build stops, run the same command again.

```bash
bash stage-1/setup/setup-all.sh
```

Load the environment in the same shell. The dependency workspace is created by the setup command above.

```bash
set +u
source /opt/ros/humble/setup.bash
source "$HOME/ws_uavx/install/setup.bash"
set -u
```

## 3. Build the project

`UAVX_INSTALL_ROOT` keeps build output away from the extracted source. The clean-install rehearsal sets it to its disposable target. A judge running this guide directly gets a separate directory under `/tmp`.

```bash
UAVX_INSTALL_ROOT="${UAVX_INSTALL_ROOT:-/tmp/uavx-judge-install}"
mkdir -p "$UAVX_INSTALL_ROOT/build" "$UAVX_INSTALL_ROOT/install" "$UAVX_INSTALL_ROOT/runs"
colcon build --base-paths uavx_ws \
  --build-base "$UAVX_INSTALL_ROOT/build" \
  --install-base "$UAVX_INSTALL_ROOT/install" \
  --symlink-install
set +u
source "$UAVX_INSTALL_ROOT/install/setup.bash"
set -u
```

## 4. Check the environment

The verifier checks executable files and pinned versions. It does not open a simulator window.

```bash
bash stage-1/setup/verify.sh
```

Every row should say `ok`, and the command should exit zero. A failed row names the missing tool or mismatched pin.

## 5. Fly the smoke test

The smoke test launches four PX4 instances, takes each vehicle off, holds and lands. It writes its record below the disposable install root.

```bash
bash scripts/run_smoke.sh --vehicles 4 --runs-dir "$UAVX_INSTALL_ROOT/runs"
```

A successful run leaves `latest.jsonl` in the runs directory and no `px4`, `gzserver` or `gzclient` process behind. The Stage 1 evidence scenarios use `scripts/run_scenario.sh`; their exact inputs and acceptance checks are in `stage-1/architecture.md` and `scripts/gate.sh`.

## 6. Reproduce a recorded run

The demo video is a capture of this software running, and this is the command that produces one. It flies a scenario and records it at the same time.

```bash
bash scripts/run_scenario.sh scenarios/relay_required.yaml \
  --record /tmp/uavx-demo.mp4 --record-seconds 60 \
  --overlay-text "$(date -u +demo_%Y%m%dT%H%M%SZ)" \
  --run-id "$(date -u +demo_%Y%m%dT%H%M%SZ)" \
  --runs-dir "$UAVX_INSTALL_ROOT/runs"
```

Three things about it are worth knowing before it is run.

The frames come from a camera sensor inside the world, not from a screen. `gzclient` is never launched: the GUI binary takes this WSL distribution down, and there is no window to record. A recording run loads `worlds/uavx_record.world`, which is the ordinary world with one static camera added, and every other run loads `worlds/uavx_empty.world` so nothing pays for rendering it does not use.

The run id is burned into every frame and the clip's sha256 goes into the run record, so a clip and the run it claims to be of can be checked against each other. `scripts/check_dryruns.py` is what does that checking.

It needs about 700 MB of free space in `/tmp` for a 60 second capture, and the frames are deleted as soon as the clip is encoded. `ffmpeg` and `ffprobe` are installed by `stage-1/setup/06-submission-tools.sh` and confirmed by `verify.sh`.

If setup fails, read [stage-1/setup/README.md](stage-1/setup/README.md). It lists the failures already seen on this machine, including the Gazebo package conflict, the PX4 build command and the XRCE dependency pin.
