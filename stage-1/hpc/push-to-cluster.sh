#!/usr/bin/env bash
# Copy everything the cluster needs out of this repo and into ~/uavx-hpc on
# Baramati. Run it from a machine that can reach the cluster over ssh.
#
# This exists so the build context is written down rather than remembered. Two
# files come from outside stage-1/hpc and both are read only as far as this
# directory is concerned:
#
#   stage-1/setup/versions.lock   the one place a version is allowed to live
#   scripts/sitl_multi.sh         the launcher, plus the gate-env.sh it sources
#
# Nothing here edits anything under scripts/ or stage-1/setup/. It copies.
#
# Line endings matter more than they look. sbatch refuses a job file with CRLF
# in it, and the failure message does not mention line endings, so every file
# is converted on the way out and checked on the way in.

set -euo pipefail

HOST="${HOST:-baramati}"
REMOTE="${REMOTE:-uavx-hpc}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "${HERE}/../.." && pwd)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "${STAGE}/context" "${STAGE}/repo/scripts" "${STAGE}/repo/stage-1/hpc"

# The build context. Small on purpose: podman sends the whole directory to the
# builder, and a context holding the repo would ship every run record with it.
cp "${HERE}/Containerfile"                 "${STAGE}/context/Containerfile"
cp "${HERE}/lock.sh"                       "${STAGE}/context/lock.sh"
cp "${HERE}/report.sh"                     "${STAGE}/context/report.sh"
cp "${REPO}/stage-1/setup/versions.lock"   "${STAGE}/context/versions.lock"

# What the smoke run mounts into the container.
cp "${REPO}/scripts/sitl_multi.sh"         "${STAGE}/repo/scripts/sitl_multi.sh"
cp "${REPO}/scripts/gate-env.sh"           "${STAGE}/repo/scripts/gate-env.sh"
cp "${HERE}/in-container-smoke.sh"         "${STAGE}/repo/stage-1/hpc/in-container-smoke.sh"

# The job files.
cp "${HERE}/build-image.slurm"             "${STAGE}/build-image.slurm"
cp "${HERE}/smoke-sitl.slurm"              "${STAGE}/smoke-sitl.slurm"

# Strip carriage returns from everything. A repo checked out on Windows hands you
# CRLF in the working tree whatever .gitattributes stores, and sbatch rejects a
# job file containing CR with an error that never mentions line endings.
#
# The check counts bytes rather than grepping for a pattern. A grep for a control
# character has to survive whichever shell is quoting this script, and one that
# quietly turns into a different pattern reports every file as dirty or, worse,
# every file as clean. Byte counts cannot be misread that way.
find "$STAGE" -type f -exec sed -i 's/\r$//' {} +

dirty=0
while IFS= read -r f; do
  before="$(wc -c < "$f")"
  after="$(tr -d '\015' < "$f" | wc -c)"
  if [ "$before" -ne "$after" ]; then
    echo "still has $((before - after)) CR bytes: $f"
    dirty=$((dirty + 1))
  fi
done < <(find "$STAGE" -type f)
[ "$dirty" -eq 0 ] || { echo "FAILED: $dirty staged files still contain CR"; exit 1; }

echo "staged for ${HOST}:~/${REMOTE}"
find "$STAGE" -type f | sed "s|${STAGE}|  .|"

ssh "$HOST" "mkdir -p ~/${REMOTE}"
scp -q -r "${STAGE}/." "${HOST}:~/${REMOTE}/"

echo
echo "on the cluster now:"
ssh "$HOST" "cd ~/${REMOTE} && find . -type f -newermt '-5 minutes' | sort | sed 's/^/  /'"
echo
echo "next:  ssh ${HOST} 'cd ~/${REMOTE} && sbatch build-image.slurm'"
