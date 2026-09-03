# The UAV-X stack on the Baramati cluster

Week 4 wants a sweep: swarm sizes, failure timings, range thresholds, topologies. That is embarrassingly parallel and the plan has always meant to run it on Baramati. The cluster has no ROS, no Gazebo, no PX4, no cmake and no g++, so a container was the only route in, and nobody had checked whether that route works. This is that check, run on 3 September 2026.

Everything below came off a command in a Slurm job. Job numbers are given so you can go and read the output; the files are in `~/uavx-hpc` on the login node.

## The short answer

It works. The pinned stack builds into a rootless podman image on a compute node, and inside that image, with no display anywhere near it, `gzserver` comes up headless and `scripts/sitl_multi.sh` brings 4 PX4 SITL vehicles to "Ready for takeoff" with all 4 visible to ROS 2 under their own namespaces.

| | |
| --- | --- |
| Image | 8.66 GB, `podman images`, job 996 |
| Build | 838 s on 32 cores, job 996, plus a 47 s base image pull measured in job 990 |
| Run | 148 s for the whole 3 phase smoke, job 1013, exit 0 |
| Version fidelity | 6 of 6 apt pins and 4 of 4 git SHAs match `versions.lock` |

4 separate things had to be got past first, and 3 of them are properties of this cluster rather than of the stack. They are written up below because a week 4 rebuild will hit every one of them again.

## What is on the cluster, and what is not

Login node `aicoeserver01`, Rocky Linux 9.8. Compute nodes `aicoeserver03`, `04` and `05`, Rocky Linux 9.7, 256 CPUs and 1000 GB of RAM each. `free -m` on `aicoeserver05` reported 1031178 MiB of memory and 65535 MiB of swap with 0 of it in use, which matters because `sitl_multi.sh` refuses to run on a box with more than 512 MiB of swap in play. One partition, `gpu`, no time limit. Slurm 24.11.7.

There is no ROS, no Gazebo, no PX4, no cmake and no g++. There is also no apptainer and no singularity. What there is, is rootless podman 5.6.0, `crun`, `slirp4netns`, `pasta`, `fuse-overlayfs` and `newuidmap`. SELinux is disabled. Node local `/tmp` on `aicoeserver04` had 683 GB free.

`srun` is still broken here, and I tested it once so the answer is written down:

```
$ srun --partition=gpu --nodes=1 --ntasks=1 --cpus-per-task=2 --mem=4G \
       --time=00:02:00 --nodelist=aicoeserver05 hostname
srun: error: Task launch for StepId=989.0 failed on node aicoeserver05: Job credential expired
srun: error: Application launch failed: Job credential expired
srun: Job step aborted
```

So everything here goes through `sbatch`. Same failure earlier projects recorded on an older Slurm, still there on 24.11.7.

## The 4 things that had to be got past

### 1. Rootless podman could not unpack the image at all

The user has no `/etc/subuid` entry. `grep -c` on that file returns 0. A rootless container namespace therefore maps exactly one UID, and unpacking any image holding a file owned by a non-zero group fails. `osrf/ros:humble-desktop` holds `/etc/gshadow`, group 42. Job 988 spent 446 seconds pulling and then died:

```
Error: unable to copy from source docker://osrf/ros:humble-desktop: writing blob:
adding layer with blob "sha256:d544298c..." : unpacking failed
(error: exit status 1; output: potentially insufficient UIDs or GIDs available in
user namespace (requested 0:42 for /etc/gshadow): Check /etc/subuid and /etc/subgid
if configured locally and run "podman system migrate":
lchown /etc/gshadow: invalid argument)
```

Worth knowing that an earlier probe had pulled and run `alpine:3.20` on the same node without trouble, which is why this did not show up sooner. Every file in an alpine image is owned by 0:0.

The fix is one line in `storage.conf`:

```
[storage.options.overlay]
ignore_chown_errors = "true"
```

Job 990 pulled the same image in 47 seconds with that set. Ownership inside the image is flattened onto the single mapped UID, which costs nothing here because the container runs as that UID and no part of this stack wants a second one.

### 2. `podman build` could not talk to systemd

`podman run` notices there is no systemd user session and says so, then falls back to cgroupfs on its own. `podman build` prints the identical warning and hands `crun` a systemd cgroup regardless, so it died in job 990 before the first RUN step:

```
error running container: from /usr/bin/crun creating container for
[/bin/sh -c echo ...]: sd-bus call: Interactive authentication required.:
Permission denied
```

Saying `--cgroup-manager=cgroupfs` on the command line gets past it, and `containers.conf` gets `events_logger = "file"` for the same reason, since there is no user journal to write to either. Job 991 then built a test layer in 13 seconds. Container networking works in both the rootless default namespace and under `--network=host`; both returned 200 from `archive.ubuntu.com`.

### 3. The `SHELL` instruction is ignored, silently enough to matter

Building in podman's default OCI format prints `SHELL is not supported for OCI image format ... Must use "docker" format` and then runs every RUN step under `/bin/sh` anyway. Job 995 built the same 2 step probe twice to pin it down:

| | default OCI | `--format docker` |
| --- | --- | --- |
| `$0` | `/bin/sh` | `/bin/bash` |
| `[[ -n ... ]]` | `[[: not found` | `BASH_VERSION=5.1.16(1)-release` |
| `type source` | no source builtin | source builtin available |
| `(false \| true)` | pipefail NOT active | pipefail active |

This is the quiet one. `[[` fails loudly, but a missing `pipefail` does not: a step that ends `uavx-report | tee report.txt` reports the exit status of `tee`, so an image whose SHAs did not match would have been written to the report, tagged, and called good. The Containerfile calls `bash -eo pipefail -c` by name in the 2 steps that need it, which works whichever format you build in.

### 4. The PX4 clone is not reliable over this link

Job 994 got 11 minutes into cloning PX4 with its submodules and lost the connection:

```
error: RPC failed; curl 56 GnuTLS recv error (-54): Error in the pull function.
error: 3038 bytes of body are still expected
fetch-pack: unexpected disconnect while reading sideband packet
fatal: early EOF
fatal: fetch-pack: invalid index-pack output
```

Job 996 ran the same clone straight through in 456 seconds. One failure in two attempts on the longest single download in the build. Whatever runs this in week 4 should retry rather than treat the first exit 128 as a verdict.

## The image

Base is `docker.io/osrf/ros:humble-desktop`, and picking it turned out to be worth more than the build time it saved. The ROS versions baked into that image are the exact ones in `versions.lock`, down to the build date stamp:

```
ok  ros-humble-desktop           locked 0.10.0-1jammy.20260804.223343  installed 0.10.0-1jammy.20260804.223343
ok  ros-humble-ros-core          locked 0.10.0-1jammy.20260726.123022  installed 0.10.0-1jammy.20260726.123022
ok  ros-humble-rclpy             locked 3.3.21-1jammy.20260724.022150  installed 3.3.21-1jammy.20260724.022150
ok  ros-humble-rclcpp            locked 16.0.19-1jammy.20260724.021311 installed 16.0.19-1jammy.20260724.021311
ok  ros-humble-rmw-fastrtps-cpp  locked 6.2.10-1jammy.20260724.002510  installed 6.2.10-1jammy.20260724.002510
```

On top of that go Gazebo Classic 11.10.2+dfsg-1 from jammy universe, PX4 at the locked SHA built for SITL, the uXRCE-DDS agent, and a colcon workspace with `px4_msgs` and `px4_ros_com`. All 4 git checkouts came out on the locked commit and all 6 apt pins matched. Nothing in the Containerfile types a version; it copies `stage-1/setup/versions.lock` into the build context and reads keys out of it, so there is still one place a version lives.

One deliberate departure from `stage-1/setup/04-px4.sh`, and it protects the step before it. Read `Tools/setup/ubuntu.sh` at the pinned PX4 commit, around lines 220 to 240: on Ubuntu 22.04 its simulation branch announces "Gazebo (Garden) will be installed. Earlier versions will be removed", adds `packages.osrfoundation.org` to the apt sources and installs `gz-garden`. That is the exact sequence `03-gazebo-classic.sh` exists to undo, and running it would have taken the Classic binaries back out of an image that had just installed them. So the image runs `ubuntu.sh --no-nuttx --no-sim-tools` and installs that branch's simulation dependencies by hand instead, minus the Garden packages and minus `ant` and `openjdk`, which are there for jmavsim and nothing in this repo uses jmavsim. `report.sh` then checks that `gz-garden` is absent and that no osrfoundation source file exists, the same 2 checks `verify.sh` makes.

That is a finding about the desk install too, and somebody who owns `stage-1/setup` should look at it. `setup-all.sh` runs 03 and then 04, and 04 calls `ubuntu.sh --no-nuttx` with the simulation branch live. Whether that machine escaped because apt refused the conflict, or because a later rerun of 03 repaired it, I cannot tell from here and did not try to, since I am not allowed to touch that tree and the local simulator belongs to another worker this session.

### What it cost

| Stage | Seconds | Job |
| --- | --- | --- |
| Base image pull, 3.54 GB | 47 | 990 |
| Base tools layer | 25 | 994 |
| Gazebo Classic | 30 | 994 |
| PX4 clone with submodules | 456 | 996 |
| PX4 dependency script | 51 | 996 |
| PX4 SITL build, 1024 ninja edges | 16 | 996 |
| gazebo-classic plugins | 24 | 996 |
| uXRCE-DDS agent | 79 | 996 |
| px4_msgs and px4_ros_com | 153 | 996 |

Job 996 reported 838 seconds of wall clock with the base and Gazebo layers replayed from cache. Add those and the pull and a clean build is roughly 16 minutes on 32 cores, of which the PX4 clone is 456 seconds, or over half. The compile is not the expensive part here and that surprised me: PX4 SITL went from cmake configure to a linked `bin/px4` in 16 seconds, against the hour or two `stage-1/setup/README.md` budgets on the desk machine.

Image size is 8.66 GB by `podman images`. The whole graphroot, base image layers included, was 8.4 GB on disk by `du -sh`.

## The run

Job 1013, `smoke-sitl.slurm`, 148 seconds, exit 0. Container facts first, since 2 of them matter later: `DISPLAY` was unset, `nproc` inside the container reported 256 even though the job asked Slurm for 16 and podman accepted `--cpus=16`, and only 4 PIDs were visible, which is the container's own PID namespace.

Phase 1 was `gzserver` alone on PX4's `empty.world`. It came up and stayed up, held port 11345, and its log says exactly what you want a headless run to say:

```
[Msg] Connected to gazebo master @ http://127.0.0.1:11345
[Err] [RenderEngine.cc:749] Can't open display:
[Wrn] [RenderEngine.cc:89] Unable to create X window. Rendering will be disabled
[Msg] Loading world file [/root/PX4-Autopilot/Tools/simulation/gazebo-classic/
      sitl_gazebo-classic/worlds/empty.world]
```

Phase 2 ran `scripts/sitl_multi.sh --vehicles 1 --hold 10`, and phase 3 ran it with 4 vehicles and a 30 second hold. Phase 3, in full:

```
--- spawning 4 x iris
  instance 0  ns=uav_1  sys_id=1  y=-7.500
  instance 1  ns=uav_2  sys_id=2  y=-2.500
  instance 2  ns=uav_3  sys_id=3  y=2.500
  instance 3  ns=uav_4  sys_id=4  y=7.500
--- checking what is actually running
  px4 processes      4
  gzserver           up
  gzclient           absent, as required
  agent              up
--- checking every vehicle is standing level
  instance 0 tilt     0.08 deg
  instance 1 tilt     0.08 deg
  instance 2 tilt     0.07 deg
  instance 3 tilt     0.06 deg
  uav_1 topics       43
  uav_2 topics       43
  uav_3 topics       43
  uav_4 topics       43
--- 4 vehicles up and healthy, holding 30s
--- still healthy after the hold
  px4 processes      4
```

All 4 stand level, all 4 are individually visible to ROS 2, and PX4 itself agrees. From instance 0's boot log:

```
INFO  [uxrce_dds_client] init UDP agent IP:127.0.0.1, port:8888
INFO  [commander] Ready for takeoff!
INFO  [uxrce_dds_client] successfully created rt/uav_1/fmu/out/battery_status data writer, topic id: 19
```

Afterwards the host had 0 stray `px4`, 0 stray `gzserver`, no listener on 11345 and 0 MiB of swap in use.

Nothing flew. `--hold` holds the stack up and tears it down; no mission ran, no scenario ran, and `run_scenario.sh` was not ported. That was the scope edge and I stopped at it.

### The one thing that failed, and why it is good news

The first attempt, job 1009, passed phase 1 and failed both launcher phases on the same line:

```
GATE FAILED: gazebo port 11345 is still held. Another gzserver is running,
probably from a launcher in another shell.
```

My harness caused it. Phase 1's `gzserver` took 3 SIGTERMs, one from my teardown and two from the launcher's own cleanup, and was still holding the port when the launcher checked. So the guard in `sitl_multi.sh` fired on the exact case it was written for, in an environment nobody wrote it for, and refused to start a run on top of a simulator it did not own. `in-container-smoke.sh` now waits for the port to be free and escalates to SIGKILL, and job 1013 reported `port 11345 free after 9s` before phase 3.

## What week 4 still has to build on this

The image lives in the building node's local `/tmp` and nowhere else, so `smoke-sitl.slurm` pins the same `--nodelist` as `build-image.slurm`. Three compute nodes means either 3 builds of about 16 minutes, or one `podman save` and 2 loads. Neither is hard, neither exists yet.

Then there is the harness. Nothing here turns a scenario into a job, collects a run record, or gets results out of the container beyond a bind mounted directory named after the job id. That is the week 4 build and this was only ever meant to prove the floor under it.

Two smaller ones. Concurrency on a single node is untested: I ran one container per node. Port 11345 and the `pkill -x gzserver` inside `sitl_multi.sh` are both scoped to the container's own network and PID namespaces, so several at once should not collide, but that is read off the namespace design and not off a measurement, and a sweep is exactly the thing that would find out the hard way. And `nproc` lies inside the container, so any step that sizes itself off the core count needs pinning the way the Containerfile pins `j=` and `MAKEFLAGS`.

## Reproducing it

```bash
bash stage-1/hpc/push-to-cluster.sh                        # copies context and launcher to ~/uavx-hpc
ssh baramati 'cd ~/uavx-hpc && sbatch build-image.slurm'   # about 16 minutes, retry a clone failure
ssh baramati 'cd ~/uavx-hpc && sbatch smoke-sitl.slurm'    # about 3 minutes
ssh baramati 'cd ~/uavx-hpc && sbatch --nodelist=aicoeserver04 cleanup-node.slurm'
```

`push-to-cluster.sh` strips carriage returns on the way out and counts the bytes again on arrival, because `sbatch` rejects a job file containing CR and says nothing about line endings when it does.

### Leaving the cluster as it was found

Job 1015 cleared `aicoeserver04`: the image, the 8.4 GB podman graphroot and 623 MB of blob staging, taking `/tmp` from 56 GB used back to 47 GB. Job 1016 could not finish the same work on `aicoeserver05`, and the reason is the same `ignore_chown_errors` from problem 1. A plain `rm -rf` reported `Permission denied` on 21 directories, every top level directory of the base image, because the calling user cannot traverse them from outside the user namespace they were unpacked in. Job 1018 removed the lot with `podman unshare rm -rf`, which does the deletion inside that namespace, and `cleanup-node.slurm` now does that automatically. Both nodes report 0 leftover paths.

One thing I did not remove. `aicoeserver03` holds an empty 128 KB podman store at `/tmp/uavx-podman` plus a `/tmp/uavx-xdg-1014`, both named the way these scripts name things. No job I submitted ever ran on that node, `sacct` says job 1014 was `conda_probe.sh` and belongs to another project, and the store has no image layers in it. Deleting somebody else's scratch is worse than leaving 128 KB, so it is still there.

The job scripts and output files stay under `~/uavx-hpc` as the evidence for everything above. Rebuilding the image costs one `sbatch`.

## Files here

| File | Does |
| --- | --- |
| `Containerfile` | The image. Reads `stage-1/setup/versions.lock`, never types a version |
| `lock.sh` | Pulls one key out of the lock file, dies if it is missing |
| `report.sh` | Compares the built image against the lock. Fatal on a SHA, recorded on an apt version |
| `build-image.slurm` | Builds on a compute node. Holds the podman settings this cluster needs |
| `smoke-sitl.slurm` | Runs the image on a compute node and collects the logs |
| `in-container-smoke.sh` | The 3 phases, run inside the container |
| `push-to-cluster.sh` | Assembles the build context and copies it over |
| `cleanup-node.slurm` | Takes one node's local disk back to how it was found |
