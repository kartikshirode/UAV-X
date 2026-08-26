#!/usr/bin/env bash
# Shared settings and helpers. Sourced by every numbered script, not run on its own.

set -euo pipefail

ROS_DISTRO_NAME="${ROS_DISTRO_NAME:-humble}"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
PX4_BRANCH_GLOB="${PX4_BRANCH_GLOB:-v1.15.*}"
WS_DIR="${WS_DIR:-$HOME/ws_uavx}"
# v2.4.2 is what the PX4 v1.15 docs name, and it no longer builds: its
# superbuild checks out Fast DDS branch 2.12.x, which eProsima has deleted, and
# cmake dies with "Failed to checkout tag: 2.12.x" after building half the
# dependency tree. v2.4.3 pins Fast DDS 2.14.x and Fast CDR 2.2.x, both of which
# still exist. Checked 26 August 2026.
XRCE_TAG="${XRCE_TAG:-v2.4.3}"
STAMP_DIR="${STAMP_DIR:-$HOME/.uavx-setup}"

mkdir -p "$STAMP_DIR"

say()  { printf '\n=== %s\n' "$*"; }
warn() { printf '\n!!! %s\n' "$*" >&2; }
die()  { printf '\nFAILED: %s\n' "$*" >&2; exit 1; }

# Steps mark themselves done so a rerun after a failure skips what already worked.
done_with()  { touch "$STAMP_DIR/$1"; }
already_did() { [ -f "$STAMP_DIR/$1" ]; }

# WSL loses DNS for a few seconds whenever the NAT is reconfigured, which
# Docker Desktop starting or stopping will do. Every network call retries.
CURL="curl -fsSL --retry 6 --retry-delay 5 --retry-all-errors --connect-timeout 20"

wait_for_net() {
  local host="${1:-github.com}" i
  for i in $(seq 1 12); do
    getent hosts "$host" >/dev/null 2>&1 && return 0
    warn "no DNS for ${host} yet, retry ${i}/12"
    sleep 5
  done
  die "cannot resolve ${host}. If Docker Desktop just started or stopped, that is the usual cause; wsl --shutdown and try again."
}

# Always source ROS setup files through this. They read unbound variables by
# design (AMENT_TRACE_SETUP_FILES and friends), so sourcing one under `set -u`
# kills the script on the spot. Bit both 05-ros2-bridge.sh and verify.sh.
source_ros_file() {
  local f="$1"
  [ -f "$f" ] || die "expected a ROS setup file at ${f}"
  local had_u=0
  case "$-" in *u*) had_u=1 ;; esac
  set +u
  # shellcheck disable=SC1090
  source "$f"
  [ "$had_u" -eq 1 ] && set -u
  return 0
}

# Round 3 finding 7: the installers resolved "newest matching tag" and tracked
# moving branches, so a fresh install could spend an hour building the wrong
# commits and only fail afterwards when verify.sh compared them. Read the lock,
# check out exactly what it names, and fail before building if a ref is gone.
LOCK_FILE="${LOCK_FILE:-$(dirname "${BASH_SOURCE[0]}")/versions.lock}"

lock() {
  local key="$1"
  [ -f "$LOCK_FILE" ] || die "no versions.lock at ${LOCK_FILE}"
  local v
  v="$(grep -E "^${key}=" "$LOCK_FILE" | head -1 | cut -d= -f2-)"
  [ -n "$v" ] || die "versions.lock has no ${key}"
  printf '%s' "$v"
}

# Fetch a repo and put it on an exact commit. No branch tracking, no fallback.
checkout_locked() {
  local url="$1" dir="$2" sha="$3" label="$4"
  if [ ! -d "$dir/.git" ]; then
    say "cloning ${label}"
    git clone "$url" "$dir" || die "clone failed for ${label}"
  fi
  git -C "$dir" fetch --all --tags --quiet || warn "fetch failed for ${label}, trying the local object anyway"
  git -C "$dir" cat-file -e "${sha}^{commit}" 2>/dev/null     || die "${label} commit ${sha} does not exist upstream any more. versions.lock needs a deliberate update, not a silent fallback."
  git -C "$dir" checkout --quiet --force "$sha" || die "checkout failed for ${label}"
  git -C "$dir" submodule update --init --recursive --quiet || die "submodules failed for ${label}"
  say "${label} at $(git -C "$dir" rev-parse --short HEAD)"
}

require_jammy() {
  . /etc/os-release
  [ "${VERSION_CODENAME:-}" = "jammy" ] || die "expected Ubuntu 22.04 (jammy), found ${VERSION_CODENAME:-unknown}"
}
