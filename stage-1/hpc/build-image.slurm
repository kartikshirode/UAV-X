#!/bin/bash
# Build the UAV-X simulation image on a Baramati compute node.
#
# Submit from the login node with sbatch. Never with srun: srun on this cluster
# still dies with "Job credential expired" before the task launches, measured
# 3 September 2026 and recorded in stage-1/hpc/README.md.
#
# Why a compute node and not the login node. The login node has no podman on it
# worth using and the cores are shared; the compute nodes have podman 5.6.0,
# 256 CPUs, 1000 GB of RAM, and 680 GB of free node-local disk under /tmp.
#
#SBATCH --job-name=uavx-image
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=160G
#SBATCH --nodelist=aicoeserver04
#SBATCH --output=/home/kartikshirode/uavx-hpc/build-%j.out

# Deliberately not set -e. A failed step here is the finding, and the accounting
# that follows it is what makes the failure readable.
set -u

JOBID="${SLURM_JOB_ID:-nojob}"
HPC_DIR="${HPC_DIR:-$HOME/uavx-hpc}"
CTX="${HPC_DIR}/context"
IMAGE="${IMAGE:-localhost/uavx-sim:1}"
BUILD_JOBS="${SLURM_CPUS_PER_TASK:-8}"

# --- podman, made to work for a user with no /etc/subuid entry ---------------
#
# Three things are load bearing here and each was found the hard way.
#
# 1. XDG_RUNTIME_DIR. There is no systemd user session on the compute nodes, so
#    /run/user/$UID does not exist and podman has nowhere to put its pause
#    process. Pointing it at a job-local path under /tmp is what makes rootless
#    podman run at all.
# 2. graphroot on node-local /tmp. The default is under $HOME, which is a
#    network filesystem here; podman detects that and forces overlay
#    force_mask=700, and every layer write then crosses the network. /tmp is a
#    local disk with 680 GB free.
# 3. TMPDIR. Podman stages pulled blobs in TMPDIR before committing them to the
#    graphroot. Left at the default that is still /tmp on this node, but it is
#    named explicitly so a site change to TMPDIR cannot quietly send several GB
#    of blob traffic over NFS.
POD_ROOT=/tmp/uavx-podman
export XDG_RUNTIME_DIR="/tmp/uavx-xdg-${JOBID}"
export TMPDIR="${POD_ROOT}/tmp"
mkdir -p "$XDG_RUNTIME_DIR" "$POD_ROOT" "$TMPDIR"
chmod 700 "$XDG_RUNTIME_DIR"

# ignore_chown_errors is the line without which none of this works. This user
# has no /etc/subuid entry, so a rootless container namespace maps exactly one
# UID. Unpacking any image that contains a file owned by a non-zero GID then
# fails, and osrf/ros:humble-desktop contains /etc/gshadow owned by group 42.
# Measured on 3 September 2026: without this the pull dies with
#
#   potentially insufficient UIDs or GIDs available in user namespace
#   (requested 0:42 for /etc/gshadow) ... lchown /etc/gshadow: invalid argument
#
# and with it the same pull succeeds in 47 seconds. The cost is that every file
# in the image ends up owned by the single mapped UID, which is fine here
# because the container runs as that UID and nothing in this stack needs a
# second one.
cat > "${POD_ROOT}/storage.conf" <<CONF
[storage]
driver = "overlay"
runroot = "${POD_ROOT}/run"
graphroot = "${POD_ROOT}/storage"

[storage.options.overlay]
ignore_chown_errors = "true"
CONF
export CONTAINERS_STORAGE_CONF="${POD_ROOT}/storage.conf"

# podman run notices there is no systemd user session and falls back to
# cgroupfs on its own. podman build prints the same warning and then hands crun
# a systemd cgroup anyway, and crun dies with
#
#   sd-bus call: Interactive authentication required.: Permission denied
#
# before the first RUN step. Saying cgroupfs here, and again on the command
# line, is what gets past it. events_logger is moved off journald for the same
# reason: there is no user journal to write to.
cat > "${POD_ROOT}/containers.conf" <<CONF
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
CONF
export CONTAINERS_CONF="${POD_ROOT}/containers.conf"

echo "===== where"
echo "  node          $(hostname)"
echo "  job           ${JOBID}"
echo "  cpus asked    ${BUILD_JOBS}"
echo "  nproc on host $(nproc)"
echo "  image         ${IMAGE}"
echo "  context       ${CTX}"
df -h /tmp | tail -1 | sed 's/^/  tmp free      /'
echo "  started       $(date -Is)"

echo
echo "===== context contents"
ls -la "$CTX" | sed 's/^/  /'
for f in Containerfile versions.lock lock.sh report.sh; do
  [ -f "${CTX}/${f}" ] || { echo "FAILED: ${CTX}/${f} is missing. Run push-to-cluster.sh first."; exit 1; }
done
echo "  versions.lock sha256 $(sha256sum "${CTX}/versions.lock" | cut -d' ' -f1)"

# How fast this node actually pulls from the outside world. Every number in the
# README about build time is mostly this number, so measure it rather than
# guessing at it afterwards.
echo
echo "===== outbound throughput from this node, measured not assumed"
for u in \
  https://packages.ros.org/ros2/ubuntu/dists/jammy/main/binary-amd64/Packages.gz \
  http://archive.ubuntu.com/ubuntu/dists/jammy/main/binary-amd64/Packages.gz ; do
  curl -s -o /dev/null --max-time 180 \
    -w "  %{speed_download} B/s  %{size_download} bytes  ${u}\n" "$u" \
    || echo "  no answer from ${u}"
done

echo
echo "===== build"
BUILD_LOG="${HPC_DIR}/build-${JOBID}.podman.log"
t0=$(date +%s)
podman build \
  --cgroup-manager=cgroupfs \
  --network=host \
  --build-arg "BUILD_JOBS=${BUILD_JOBS}" \
  --tag "$IMAGE" \
  --file "${CTX}/Containerfile" \
  "$CTX" > "$BUILD_LOG" 2>&1
build_rc=$?
t1=$(date +%s)
echo "  rc            ${build_rc}"
echo "  wall seconds  $((t1 - t0))"
echo "  wall minutes  $(( (t1 - t0) / 60 ))"
echo "  full log      ${BUILD_LOG}"

echo
echo "===== stage timings out of the build log"
grep -E '^\[stage ' "$BUILD_LOG" | sed 's/^/  /'

if [ "$build_rc" -ne 0 ]; then
  echo
  echo "===== last 60 lines of the build log, since it failed"
  tail -60 "$BUILD_LOG" | sed 's/^/  /'
  echo
  echo "===== leaving the storage in place so the next job reuses the cached layers"
  du -sh "${POD_ROOT}/storage" 2>/dev/null | sed 's/^/  /'
  rm -rf "$XDG_RUNTIME_DIR"
  exit "$build_rc"
fi

echo
echo "===== image size"
podman images --format '  {{.Repository}}:{{.Tag}}  {{.Size}}  created {{.CreatedSince}}' 2>/dev/null
echo "  graphroot on disk (all layers, including the base image):"
du -sh "${POD_ROOT}/storage" 2>/dev/null | sed 's/^/    /'

echo
echo "===== what landed, read from inside the image"
podman run --rm "$IMAGE" uavx-report 2>&1 | grep -v '^time=' | sed 's/^/  /'
echo "  report exit   ${PIPESTATUS[0]}"

echo
echo "===== finished $(date -Is)"
rm -rf "$XDG_RUNTIME_DIR"
