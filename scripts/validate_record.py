#!/usr/bin/env python3
"""Validate a run record against the schema, with nothing else in the way.

W1.3 needs to prove the record writer produces something the provenance
contract accepts. `uavx_eval.check` does that too, and more, but it does not
exist until W2, so the first week that writes a record would have had nothing
to check it with and the chunk would have been a promise.

This reads the schema and says yes or no. It deliberately does not look at a
single metric: metrics are meaningless until provenance holds, and provenance
is all a record writer is responsible for.

    python3 scripts/validate_record.py runs/latest.jsonl

Exit 0 valid, 1 invalid, 2 unreadable.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from jsonschema_mini import validate                        # noqa: E402

SCHEMA = REPO / "scenarios" / "run-record.schema.json"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_record.py <record.jsonl>")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"  FAIL  no record at {path}")
        return 2
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  FAIL  {path.name} is not valid JSON: {exc}")
        return 2
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  FAIL  cannot read {SCHEMA}: {exc}")
        return 2

    errors = validate(record, schema)
    if errors:
        print(f"  FAIL  {path.name} does not satisfy the run record contract:")
        for e in errors[:12]:
            print(f"          {e}")
        if len(errors) > 12:
            print(f"          and {len(errors) - 12} more")
        return 1

    print(f"  ok    {path.name} satisfies scenarios/run-record.schema.json, "
          f"run {record.get('run_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
