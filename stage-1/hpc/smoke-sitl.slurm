#!/bin/bash
# Run the image: gzserver headless, then PX4 SITL, on a compute node.
#
# Depends on build-image.slurm having finished on the same node, because the
# image lives in that node's local /tmp and nowhere else. The --nodelist below
# is what ties the two together and it is not optional.
#
#SBATCH --job-name=uavx-smoke
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodelist=aicoeserver04
#SBATCH --output=/home/kartikshirode/uavx-hpc/smoke-%j.out

set -u

JOBID="${SLURM_JOB_ID:-nojob}"
HPC_DIR="${HPC_DIR:-$HOME/uavx-hpc}"
IMAGE="${IMAGE:-localhost/uavx-sim:1}"
VEHICLES="${VEHICLES:-4}"
HOLD="${HOLD:-30}"
OUT="${HPC_DIR}/smoke-${JOBID}"

POD_ROOT=/tmp/uavx-podman
export XDG_RUNTIME_DIR="/tmp/uavx-xdg-${JOBID}"
export TMPDIR="${POD_ROOT}/tmp"
# Both written by build-image.slurm on this node. Its comments explain why each
# line in them exists; nothing here should differ from what built the image.
export CONTAINERS_STORAGE_CONF="${POD_ROOT}/storage.conf"
export CONTAINERS_CONF="${POD_ROOT}/containers.conf"
mkdir -p "$XDG_RUNTIME_DIR" "$OUT"
chmod 700 "$XDG_RUNTIME_DIR"

echo "===== where"
echo "  node          $(hostname)"
echo "  job           ${JOBID}"
echo "  cpus asked    ${SLURM_CPUS_PER_TASK:-?}"
echo "  image         ${IMAGE}"
echo "  vehicles      ${VEHICLES}"
echo "  output dir    ${OUT}"
echo "  started       $(date -Is)"

if [ ! -f "$CONTAINERS_STORAGE_CONF" ]; then
  echo "FAILED: no podman storage config at ${CONTAINERS_STORAGE_CONF}."
  echo "That means build-image.slurm has not run on $(hostname), and the image"
  echo "only ever exists on the node that built it. Rebuild here or change the"
  echo "--nodelist in both job files to match."
  exit 1
fi

if ! podman image exists "$IMAGE" 2>/dev/null; then
  echo "FAILED: ${IMAGE} is not in this node's local podman storage."
  podman images 2>&1 | grep -v '^time=' | sed 's/^/  /'
  exit 1
fi

echo
echo "===== host side, before anything starts"
echo "  host swap used  $(free -m | awk '/^Swap:/ {print $3}') MiB"
echo "  host mem avail  $(free -m | awk '/^Mem:/ {print $7}') MiB"
echo "  host 11345      $(ss -lnt 2>/dev/null | grep -c ':11345' || true) listener(s)"

# The container gets its own network and PID namespaces, which is what makes a
# sweep possible later: gazebo always wants port 11345 and sitl_multi.sh always
# pkills by process name, so two runs of this on one node would fight over both
# if they shared the host's namespaces. They do not. --network is left at the
# rootless default rather than host for exactly that reason.
#
# --shm-size because the default 64 MB is not enough for the Fast DDS shared
# memory transport once several PX4 instances and the agent are all up.
echo "  selinux         $(getenforce 2>/dev/null || echo absent)"

# There is no systemd user session on these nodes, so podman falls back to
# --cgroup-manager=cgroupfs and rootless cgroup v2 delegation may not be there.
# --cpus needs that delegation. Ask for it, and if podman refuses, say so and
# run without rather than losing the whole smoke test to a resource knob.
CPUS_ARG=(--cpus="${SLURM_CPUS_PER_TASK:-16}")
if ! podman run --rm "${CPUS_ARG[@]}" "$IMAGE" true > /dev/null 2>&1; then
  echo "  cpu limit       podman refused --cpus here, running unrestricted"
  CPUS_ARG=()
else
  echo "  cpu limit       --cpus=${SLURM_CPUS_PER_TASK:-16} accepted"
fi

echo
echo "===== run"
t0=$(date +%s)
podman run --rm \
  --shm-size=2g \
  --security-opt label=disable \
  "${CPUS_ARG[@]}" \
  --env "VEHICLES=${VEHICLES}" \
  --env "HOLD=${HOLD}" \
  --volume "${HPC_DIR}/repo:/work/repo:ro" \
  --volume "${OUT}:/work/out:rw" \
  "$IMAGE" \
  bash /work/repo/stage-1/hpc/in-container-smoke.sh 2>&1 | grep -v '^time=.*level=warning'
rc="${PIPESTATUS[0]}"
t1=$(date +%s)

echo
echo "===== result"
echo "  exit code     ${rc}"
echo "  wall seconds  $((t1 - t0))"
echo "  artifacts     ${OUT}"
ls -la "$OUT" | sed 's/^/  /'

echo
echo "===== host side, after"
echo "  host swap used  $(free -m | awk '/^Swap:/ {print $3}') MiB"
echo "  host 11345      $(ss -lnt 2>/dev/null | grep -c ':11345' || true) listener(s)"
echo "  stray px4       $(pgrep -xc px4 || true)"
echo "  stray gzserver  $(pgrep -xc gzserver || true)"

echo
echo "===== finished $(date -Is)"
rm -rf "$XDG_RUNTIME_DIR"
exit "$rc"
