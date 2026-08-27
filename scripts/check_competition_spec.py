#!/usr/bin/env python3
"""Check the published competition record still says what we built against.

From the rules, verbatim:

    The organizers reserve the right to modify, postpone, or cancel the Grand
    Challenge or any stage, with changes communicated through official channels.

context.md ends with "Rerun it before submitting" and nothing did. Every
deliverable, the deadline, the judging weights and the submission address are
copied into this repository from a capture taken on 26 August. If any of them
moves and nobody looks, five weeks of work lands against the wrong spec, and the
failure mode is silent: the plan keeps passing its own gates all the way to a
submission that does not match.

techfest.org serves the real record as JSON at /api/compis/, so this is one
request and a diff.

    python3 scripts/check_competition_spec.py                # must reach the API
    python3 scripts/check_competition_spec.py --allow-offline

Exit 0 unchanged, 1 changed or unreachable, 2 changed in a way that carries an
obligation.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAVED = REPO / "research" / "techfest-uav-x.json"
API = "https://techfest.org/api/compis/"
COMPI_ID = "uav-x"

# Fields that carry an obligation. A change in any of these can invalidate work
# already done, so it stops the gate.
BINDING = [
    "name", "desc", "about", "structure", "timeline", "rules", "faq",
    "contact", "prize", "max_team_size", "probStatement", "walink",
]

# Fields that move on their own and mean nothing to us. reg_count is the field
# that proves the feed is live rather than cached.
VOLATILE = ["reg_count", "compiImg", "sponsorImg", "sponsorLink", "rounds", "id"]


def fetch() -> list:
    # Bytes, then decode UTF-8 explicitly. `text=True` decodes with the locale
    # encoding, which on this machine is cp1252, and the organisers write their
    # stage headings with an en dash. That turned one character into mojibake
    # and reported "the rules have changed" when nothing had. A false alarm here
    # is worse than useless: it arrives during W5 and it looks exactly like the
    # thing you must not ignore.
    out = subprocess.run(
        ["curl", "-sSL", "--max-time", "60", "--retry", "3",
         "--retry-delay", "3", API],
        capture_output=True, timeout=300)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(out.stderr.decode("utf-8", "replace").strip()
                           or "empty response")
    data = json.loads(out.stdout.decode("utf-8"))
    return data if isinstance(data, list) else data.get("results", data.get("data", []))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-offline", action="store_true",
                    help="warn instead of failing when the API cannot be reached. "
                         "Never pass this in W5: the check exists to be run "
                         "against the live record before submitting.")
    a = ap.parse_args()

    saved = json.loads(SAVED.read_text(encoding="utf-8"))

    try:
        rows = fetch()
    except Exception as exc:  # noqa: BLE001
        msg = f"could not reach {API}: {exc}"
        if a.allow_offline:
            print(f"  WARN  {msg}")
            print("        Running offline. The published record has NOT been "
                  "checked this run.")
            return 0
        print(f"  FAIL  {msg}")
        return 1

    live = [x for x in rows if x.get("compi_id") == COMPI_ID]
    if len(live) != 1:
        print(f"  FAIL  the feed carries {len(live)} entries for {COMPI_ID}. "
              f"If the challenge has been withdrawn, that is the finding.")
        return 2
    live = live[0]

    binding_changes, other_changes = [], []
    for key in sorted(set(saved) | set(live)):
        if key in VOLATILE:
            continue
        if saved.get(key) != live.get(key):
            (binding_changes if key in BINDING else other_changes).append(key)

    print(f"  ok    reached the live record, {len(rows)} competitions listed")
    if saved.get("reg_count") != live.get("reg_count"):
        print(f"  note  registrations {saved.get('reg_count')} at capture, "
              f"{live.get('reg_count')} now. Roughly 15 qualify from Stage 1.")

    if other_changes:
        print(f"  note  changed, no obligation attached: {', '.join(other_changes)}")

    if binding_changes:
        print(f"\n  FAIL  {len(binding_changes)} binding field(s) changed since "
              f"the capture in research/:")
        for key in binding_changes:
            print(f"\n  --- {key}")
            print(f"    was: {str(saved.get(key))[:500]}")
            print(f"    now: {str(live.get(key))[:500]}")
        print("\n  Read the change before doing anything else. Update context.md "
              "and research/techfest-uav-x.json together, and say in the commit "
              "what moved. Do not re-capture the file to make this pass.")
        return 2

    print("  ok    every binding field matches the capture in research/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
