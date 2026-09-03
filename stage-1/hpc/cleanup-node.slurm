#!/bin/bash
# Take one compute node's local disk back to how it was found.
#
# The image and every layer under it live in that node's own /tmp and nowhere
# else, so this has to run on the node that holds them. Submit it once per node
# you built or ran on:
#
#   sbatch --nodelist=aicoeserver04 cleanup-node.slurm
#
# Nothing here touches $HOME, so the job scripts and the output files stay put.
# Rebuilding the image afterwards costs one sbatch and about a quarter of an
# hour; the numbers are in README.md.
#
#SBATCH --job-name=uavx-clean
#SBATCH --partition=gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --output=/home/kartikshirode/uavx-hpc/cleanup-%j.out

set -u

export XDG_RUNTIME_DIR="/tmp/uavx-xdg-clean-${SLURM_JOB_ID:-0}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

# Every scratch root this feasibility work ever created on a compute node.
ROOTS="/tmp/uavx-podman /tmp/uavx-podman-shelltest"

echo "===== before"
echo "  node $(hostname)"
du -sh $ROOTS /tmp/container_images_storage* 2>/dev/null | sed 's/^/  /'
df -h /tmp | tail -1 | sed 's/^/  /'

for r in $ROOTS; do
  [ -f "${r}/storage.conf" ] || continue
  echo
  echo "===== images in ${r}"
  export CONTAINERS_STORAGE_CONF="${r}/storage.conf"
  if [ -f "${r}/containers.conf" ]; then
    export CONTAINERS_CONF="${r}/containers.conf"
  else
    unset CONTAINERS_CONF
  fi
  podman images --format '  {{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null
  podman rmi -a -f > /dev/null 2>&1
  podman system reset -f > /dev/null 2>&1
done

echo
echo "===== removing scratch"
# A plain rm cannot finish the job. Layers unpacked with ignore_chown_errors
# leave directories the calling user cannot traverse from outside the user
# namespace, and rm reports Permission denied on every top level directory of
# the base image. podman unshare runs the removal inside that namespace, where
# those IDs exist. Measured on aicoeserver05: rm left 21 undeletable
# directories behind and podman unshare took the same tree out completely.
for r in $ROOTS; do
  [ -e "$r" ] || continue
  rm -rf "$r" 2>/dev/null
  [ -e "$r" ] && podman unshare rm -rf "$r" 2>&1 | tail -3 | sed 's/^/  /'
done
rm -rf /tmp/container_images_storage*
rm -rf /tmp/uavx-xdg-*
echo "  done"

echo
echo "===== after"
df -h /tmp | tail -1 | sed 's/^/  /'
left="$(ls -d /tmp/uavx-* 2>/dev/null | grep -v "uavx-xdg-clean-${SLURM_JOB_ID:-0}" | wc -l)"
echo "  leftover uavx paths: ${left}"
ls -d /tmp/uavx-* 2>/dev/null | sed 's/^/    /'
rm -rf "$XDG_RUNTIME_DIR"
echo "===== done $(date -Is)"
