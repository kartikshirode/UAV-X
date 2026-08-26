#!/usr/bin/env bash
# Shared settings and helpers. Sourced by every numbered script, not run on its own.

set -euo pipefail

ROS_DISTRO_NAME="${ROS_DISTRO_NAME:-humble}"
PX4_DIR="${PX4_DIR:-$HOME/PX4-Autopilot}"
PX4_BRANCH_GLOB="${PX4_BRANCH_GLOB:-v1.15.*}"
WS_DIR="${WS_DIR:-$HOME/ws_uavx}"
XRCE_TAG="${XRCE_TAG:-v2.4.2}"
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

require_jammy() {
  . /etc/os-release
  [ "${VERSION_CODENAME:-}" = "jammy" ] || die "expected Ubuntu 22.04 (jammy), found ${VERSION_CODENAME:-unknown}"
}
