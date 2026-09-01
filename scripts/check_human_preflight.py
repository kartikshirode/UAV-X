#!/usr/bin/env python3
"""Validate submission/human-preflight.json against its schema.

Called by `gate.sh preflight`, which will not run a week without it, and again
by check_submission.py in chunk 4.8, which needs the delivery budget out of it.

Round 4 finding 7: preflight read three fields out of this file and accepted any
truthy object for the rest, so a half-written receipt passed. Registration with
no email, an eligibility declaration with no date, a delivery budget with no
route. The point of the file is that a human went and did the thing, and a
field nobody checks records nothing.

    python3 scripts/check_human_preflight.py

Exit 0 valid, 1 invalid or missing.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonschema_mini import validate  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RECEIPT = REPO / "submission" / "human-preflight.json"
SCHEMA = REPO / "submission" / "human-preflight.schema.json"


def load() -> dict:
    """Returns the receipt, or raises SystemExit with a usable message."""
    if not RECEIPT.is_file():
        print(f"  FAIL  no {RECEIPT.relative_to(REPO).as_posix()}")
        print("        Registration, eligibility, the clarification channel, the")
        print("        organiser questions and the delivery method are human steps.")
        print("        None of them can be done by an agent. See")
        print("        stage-1/human-preflight.md, which has the file to write.")
        raise SystemExit(1)
    try:
        return json.loads(RECEIPT.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  FAIL  {RECEIPT.name} is not valid JSON: {exc}")
        raise SystemExit(1) from exc


def main() -> int:
    receipt = load()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    errors = validate(receipt, schema)
    if errors:
        print(f"  FAIL  human preflight receipt is incomplete, "
              f"{len(errors)} problem(s):")
        for e in errors:
            print(f"          {e}")
        print("\n        stage-1/human-preflight.md says what each field means and")
        print("        why it blocks. Do not fill one in to get past this.")
        return 1

    d = receipt["delivery"]
    print(f"  ok    registered {receipt['registered']['done']} as "
          f"{receipt['registered']['email']}, "
          f"{receipt['registered']['competition_id']}")
    print(f"  ok    eligibility declared {receipt['eligibility']['done']}")
    print(f"  ok    clarification channel joined "
          f"{receipt['clarification_channel']['done']}")
    print(f"  ok    organiser email sent {receipt['organiser_email']['sent']}, "
          f"answers {receipt['organiser_email']['answers']}")
    print(f"  ok    delivery budget {d['attachment_limit_mb']} MB by {d['route']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
