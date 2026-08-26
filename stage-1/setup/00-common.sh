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

require_jammy() {
  . /etc/os-release
  [ "${VERSION_CODENAME:-}" = "jammy" ] || die "expected Ubuntu 22.04 (jammy), found ${VERSION_CODENAME:-unknown}"
}
