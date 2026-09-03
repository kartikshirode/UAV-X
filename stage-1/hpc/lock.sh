#!/usr/bin/env bash
# Read one key out of versions.lock, and fail if it is not there.
#
# Same contract as the lock() helper in stage-1/setup/00-common.sh. It lives in
# its own file rather than inline in the Containerfile because a shell function
# defined in one RUN does not survive into the next one, and a key typo that
# resolved to the empty string would build a stack nobody pinned.

set -euo pipefail

LOCK_FILE="${LOCK_FILE:-/opt/uavx/versions.lock}"

key="${1:?usage: uavx-lock <key>}"

[ -f "$LOCK_FILE" ] || { echo "FAILED: no versions.lock at $LOCK_FILE" >&2; exit 1; }

value="$(grep -E "^${key}=" "$LOCK_FILE" | head -1 | cut -d= -f2-)"
[ -n "$value" ] || { echo "FAILED: versions.lock has no $key" >&2; exit 1; }

printf '%s' "$value"
