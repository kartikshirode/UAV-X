#!/usr/bin/env bash
# Freeze the source being submitted, and write the manifest everything else
# binds to.
#
# Round 3 finding 3: receipts were compared against a moving HEAD, so writing
# the sent receipt and committing it invalidated the receipt. The archive is the
# thing being submitted, not the working tree, so the archive is what everything
# binds to.
#
# Round 4 finding 5: the manifest recorded an archive hash that nothing ever
# recomputed. Two invented matching strings satisfied the whole freeze. So this
# script builds the archive with `git archive` from the frozen commit, and
# check_submission.py rehashes the file on disk and rechecks every entry in it
# against that commit's tree.
#
# Usage:  bash scripts/freeze_source.sh
#
# Refuses to run on a dirty tree, because a freeze of uncommitted work is not
# a freeze.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gate-env.sh
source "${HERE}/gate-env.sh"

cd "$UAVX_REPO"

# These, and nothing else. `git archive HEAD` with no paths would sweep in
# submission/, meaning the archive would carry the demo video and the proposal
# and then be attached beside them.
ARCHIVE_PATHS=(uavx_ws scenarios scripts stage-1 LICENSE THIRD-PARTY.md)

if [ -n "$(git status --porcelain)" ]; then
  gdie "the working tree is dirty. Commit first: a freeze of uncommitted work binds nothing."
fi

C="$(git rev-parse HEAD)"
mkdir -p submission
ARCHIVE="submission/uavx-source.zip"

gsay "freezing at ${C}"
rm -f "$ARCHIVE"
git archive --format=zip --prefix=uavx-source/ -o "$ARCHIVE" "$C" -- "${ARCHIVE_PATHS[@]}" \
  || gdie "git archive failed"

SHA="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
TREE="$(python3 "${HERE}/source_tree_hash.py" --ref "$C")"

python3 - "$C" "$ARCHIVE" "$SHA" "$TREE" <<'PY'
import json, sys, pathlib
commit, archive, sha, tree = sys.argv[1:5]
pathlib.Path("submission/source-manifest.json").write_text(json.dumps({
    "commit_sha": commit,
    "archive": pathlib.Path(archive).name,
    "archive_sha256": sha,
    "source_tree_sha256": tree,
    "built_by": "scripts/freeze_source.sh",
}, indent=2) + "\n", encoding="utf-8")
PY

gsay "frozen"
printf '  commit      %s\n' "$C"
printf '  archive     %s\n' "$ARCHIVE"
printf '  archive     sha256 %s\n' "$SHA"
printf '  source tree sha256 %s\n' "$TREE"
printf '\nNext: run the fresh install against this archive, then package.\n'
