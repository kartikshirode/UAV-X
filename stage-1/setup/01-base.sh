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

# W5 runs inside this distro and check_submission.py cannot read the proposal
# without pdftotext or decode the video without ffmpeg. Both were missing here
# and the failure would have arrived in the last four days, reported as
# "cannot read the PDF" rather than as "you never installed poppler".
say "the tools the submission check needs"
sudo apt-get install -y --no-install-recommends poppler-utils ffmpeg

say "locale"
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

say "universe repo"
sudo add-apt-repository -y universe

done_with base
say "base done"
