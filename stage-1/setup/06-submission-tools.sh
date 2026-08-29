#!/usr/bin/env bash
# The tools W3 and W5 need to read what they are about to send. Run last.
#
# This step exists because of round 6 finding 2, and the shape of that bug is
# worth keeping written down. The packages were added to 01-base.sh, which is
# correct for a machine that has never been set up. Every machine that already
# had a `base` stamp skipped them forever, and this one did. Then verify.sh
# checked ros2, colcon, gazebo and the agent, said the stack was fine, and the
# submission fixture failed four days later with "cannot read the PDF".
#
# A stamp is a promise that a named set of commands ran. Adding commands under
# an old stamp breaks that promise silently, so new work gets a new stamp.

. "$(dirname "$0")/00-common.sh"
require_jammy

if already_did submission-tools; then say "submission tools already done, skipping"; exit 0; fi

say "poppler and ffmpeg"
# check_submission.py reads the proposal with pdftotext and pdfinfo, and proves
# the demo decodes with ffprobe and ffmpeg. Without these four W5 cannot check
# the two deliverables a judge actually opens.
sudo apt-get update
sudo apt-get install -y --no-install-recommends poppler-utils ffmpeg

for t in pdftotext pdfinfo ffmpeg ffprobe; do
  command -v "$t" >/dev/null 2>&1 || die "${t} is still missing after the install"
done

done_with submission-tools
say "submission tools done"
