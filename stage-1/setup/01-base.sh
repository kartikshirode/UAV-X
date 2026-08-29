#!/usr/bin/env bash
# Base packages and locale. Run first.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did base; then say "base already done, skipping"; exit 0; fi

say "apt update and base tooling"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential ca-certificates cmake curl git gnupg lsb-release \
  locales ninja-build pkg-config python3-pip python3-venv software-properties-common \
  unzip wget

# poppler-utils and ffmpeg used to be installed here. They moved to
# 06-submission-tools.sh, because adding them under this step's existing stamp
# meant every machine already set up skipped them and nothing noticed.

say "locale"
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

say "universe repo"
sudo add-apt-repository -y universe

done_with base
say "base done"
