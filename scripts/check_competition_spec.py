#!/usr/bin/env python3
"""Check the published competition record still says what we built against.

From the rules, verbatim:

    The organizers reserve the right to modify, postpone, or cancel the Grand
    Challenge or any stage, with changes communicated through official channels.

Every deliverable, the deadline, the judging weights and the submission address
are copied into this repository from a capture. techfest.org serves the real
record as JSON at /api/compis/, so this is one request and a diff.

It has already earned its place: on its first real run it caught VJTI Mumbai
being dropped from the published collaborator list inside 24 hours of the
capture, which is the sentence the eligibility rule points at.

Round 5 finding 8 found three ways past it, all now closed.

  - It listed what was binding, so any field nobody had thought about could
    change silently. `live: false` printed a note and exited 0, and that is the
    cancellation case the rules warn about. Everything is binding now unless it
    is named as noise.
  - `probStatement` was compared as a URL string, so replacing the PDF at the
    same address was invisible. The bytes get hashed.
  - `--allow-offline` returned 0, turning "not checked" into "checked and
    fine". Offline has its own exit code and is only tolerated if the record
    was actually reached recently.

    python3 scripts/check_competition_spec.py                # W5: the real one
    python3 scripts/check_competition_spec.py --allow-offline --skip-pdf

Exit 0 verified online, 1 unusable or too long since a real check, 2 the record
changed, 3 offline but verified recently enough to keep working.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAVED = REPO / "research" / "techfest-uav-x.json"
RECEIPT = REPO / "research" / "spec-check-receipt.json"
BASELINE = REPO / "research" / "spec-baseline.json"
API = "https://techfest.org/api/compis/"
COMPI_ID = "uav-x"

# Fields that move on their own and mean nothing to us. Everything else is
# binding, including fields that do not exist yet. reg_count is also the field
# that proves the feed is live rather than cached.
VOLATILE = ["reg_count", "compiImg", "sponsorImg", "sponsorLink", "rounds",
            "id", "order", "created_at"]

# Fields whose value is a promise about the challenge existing at all.
MUST_HOLD = {"live": True}

MAX_RECEIPT_AGE_DAYS = 7


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


def receipt_age_days():
    if not RECEIPT.is_file():
        return None
    try:
        d = json.loads(RECEIPT.read_text(encoding="utf-8"))
        when = datetime.fromisoformat(d["checked_at"].replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    return (datetime.now(timezone.utc) - when).total_seconds() / 86400


def write_receipt(live: dict) -> None:
    RECEIPT.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": API,
        "reg_count": live.get("reg_count"),
        "problem_statement_url": live.get("probStatement"),
    }, indent=2) + "\n", encoding="utf-8", newline="\n")


def fetch_pdf(url: str):
    """Download the problem statement, and be sure it arrived whole.

    Round 6, the conclusion pass. This fired for real on 30 August, reporting
    that the PDF had been replaced at the same URL. It had not: five downloads
    in a row matched the baseline. One transfer had come back short, and
    hashing whatever curl left on disk turned a truncated download into "the
    rules have changed".

    That is the loudest false alarm this repo can produce. It arrives during
    W5, it looks exactly like the thing you must not ignore, and the second
    time it cries wolf nobody reads it. So the body has to be complete before
    its hash means anything: a 200, the right content type, a PDF header, the
    %%EOF trailer, and the byte count the server said it was sending.

    Returns (bytes, final_url), or (None, None) after printing why.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        tmp = Path(fh.name)
    try:
        # Not `-o /dev/null` plus a second GET: /dev/null is not a path curl can
        # open on Windows, so the HEAD failed there and reported the PDF as
        # missing. A checker that cries wolf on the wrong platform gets ignored
        # on the right one.
        res = subprocess.run(
            ["curl", "-sSL", "--fail", "--max-time", "180", "--retry", "3",
             "--retry-delay", "2", "--retry-all-errors", "-o", str(tmp),
             "-w", "%{content_type} %{http_code} %{size_download} "
                   "%{size_header} %{url_effective}", url],
            capture_output=True, text=True, timeout=300)
        parts = res.stdout.strip().split(" ")
        while len(parts) < 5:
            parts.append("")
        ctype, code, size_dl, _hdr, final = parts[:5]
        final = final or url
        if res.returncode != 0 or code != "200":
            print(f"  FAIL  the problem statement URL answered {code or '?'}: "
                  f"{url}")
            return None, None
        if "pdf" not in ctype.lower():
            print(f"  FAIL  the problem statement URL serves {ctype!r}, not a PDF")
            return None, None
        body = tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)

    if not body.startswith(b"%PDF"):
        print(f"  FAIL  what {final} served does not begin with a PDF header")
        return None, None
    # A PDF ends with %%EOF. A transfer cut short almost never does, and this is
    # the cheapest way to tell a short read from a real edit.
    if b"%%EOF" not in body[-2048:]:
        print(f"  FAIL  the download from {final} has no %%EOF trailer, so it "
              f"arrived truncated. Nothing can be concluded from its hash.")
        return None, None
    try:
        if int(size_dl) != len(body):
            print(f"  FAIL  curl reported {size_dl} bytes and {len(body)} "
                  f"landed on disk")
            return None, None
    except ValueError:
        pass
    return body, final


def check_problem_statement(url: str, skip: bool) -> bool:
    """The bytes, not the address.

    The organisers can replace the PDF at the same URL and nothing in the JSON
    record would move. What the problem statement says is the thing that binds.
    """
    if not url:
        print("  FAIL  the record no longer links a problem statement")
        return False
    if skip:
        print("  note  problem statement not fetched this run (--skip-pdf)")
        return True

    body, final = fetch_pdf(url)
    if body is None:
        return False
    got = hashlib.sha256(body).hexdigest()

    base = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.is_file() else {}
    want = base.get("problem_statement_sha256")
    want_url = base.get("problem_statement_url")

    # Round 6, the conclusion pass. On 30 August this reported "replaced at the
    # same URL" when the organisers had in fact published a new file at a NEW
    # url: Django appended _1 because the old name was taken. The old url still
    # served the old bytes, so following the message led straight to the wrong
    # conclusion, that the checker was crying wolf. Say which of the two
    # happened, because they are investigated differently.
    if want_url and url != want_url:
        print(f"\n  FAIL  the record links a different problem statement than "
              f"the one this repo was built against."
              f"\n        was {want_url}"
              f"\n        now {url}"
              f"\n        The old url may still serve the old file, so compare "
              f"the two documents rather than re-reading either one alone.")
        return False
    if want is None:
        base["problem_statement_sha256"] = got
        base["problem_statement_url"] = url
        base["_why"] = ("Round 5 finding 8: comparing the URL string cannot see "
                        "a PDF replaced at the same address.")
        BASELINE.write_text(json.dumps(base, indent=2) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"  note  first capture of the problem statement, {got[:16]}")
        return True
    if got != want:
        # Confirm before crying wolf. A genuine replacement hashes the same on
        # the second fetch; a short read does not.
        again, _ = fetch_pdf(url)
        if again is None:
            print("  FAIL  the problem statement hash moved and the confirming "
                  "download did not complete. Run this again on a better "
                  "connection before concluding anything.")
            return False
        confirm = hashlib.sha256(again).hexdigest()
        if confirm != got:
            print(f"  FAIL  two downloads of the problem statement produced "
                  f"two different hashes, {got[:16]} and {confirm[:16]}. The "
                  f"file is not being served consistently, so this check "
                  f"cannot say whether it changed.")
            return False
        print(f"\n  FAIL  the problem statement PDF has been replaced at the "
              f"same URL, confirmed by two downloads."
              f"\n        was {want}\n        now {got}\n"
              f"        Read the new one before doing any more work.")
        return False
    print(f"  ok    problem statement bytes unchanged, {got[:16]}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-pdf", action="store_true",
                    help="do not download the linked problem statement. For the "
                         "weekly preflight; W5 fetches it.")
    ap.add_argument("--allow-offline", action="store_true",
                    help="tolerate an unreachable API, but only if a real check "
                         "happened recently. Never pass this in W5.")
    a = ap.parse_args()

    saved = json.loads(SAVED.read_text(encoding="utf-8"))

    try:
        rows = fetch()
    except Exception as exc:  # noqa: BLE001
        msg = f"could not reach {API}: {exc}"
        if not a.allow_offline:
            print(f"  FAIL  {msg}")
            return 1
        # Offline is not a pass. It gets its own code, and it is only tolerated
        # while a genuine check is recent enough to still mean something.
        print(f"  WARN  {msg}")
        age = receipt_age_days()
        if age is None:
            print("        No successful online check has ever been recorded, "
                  "so nothing has ever verified the published record.")
            return 1
        if age > MAX_RECEIPT_AGE_DAYS:
            print(f"        The last successful online check was {age:.0f} days "
                  f"ago, past the {MAX_RECEIPT_AGE_DAYS} day limit. Get online "
                  f"before building more against a record nobody has read.")
            return 1
        print(f"        Last verified online {age:.1f} days ago, inside the "
              f"{MAX_RECEIPT_AGE_DAYS} day limit. Proceeding.")
        return 3

    live = [x for x in rows if x.get("compi_id") == COMPI_ID]
    if len(live) != 1:
        print(f"  FAIL  the feed carries {len(live)} entries for {COMPI_ID}. "
              f"If the challenge has been withdrawn, that is the finding.")
        return 2
    live = live[0]

    print(f"  ok    reached the live record, {len(rows)} competitions listed")
    if saved.get("reg_count") != live.get("reg_count"):
        print(f"  note  registrations {saved.get('reg_count')} at capture, "
              f"{live.get('reg_count')} now. Roughly 15 qualify from Stage 1.")

    for key, want in MUST_HOLD.items():
        if live.get(key) != want:
            print(f"\n  FAIL  the record says {key}={live.get(key)!r}, not "
                  f"{want!r}. A withdrawn or hidden challenge is a finding, "
                  f"not a note.")
            return 2

    if not check_problem_statement(live.get("probStatement"), a.skip_pdf):
        return 2

    changed = [k for k in sorted(set(saved) | set(live))
               if k not in VOLATILE and saved.get(k) != live.get(k)]
    if changed:
        print(f"\n  FAIL  {len(changed)} field(s) changed since the capture in "
              f"research/:")
        for key in changed:
            print(f"\n  --- {key}")
            print(f"    was: {str(saved.get(key))[:500]}")
            print(f"    now: {str(live.get(key))[:500]}")
        print("\n  Read the change before doing anything else. Update context.md "
              "and research/techfest-uav-x.json together, and say in the commit "
              "what moved. Do not re-capture the file to make this pass.")
        return 2

    print("  ok    every field matches the capture in research/, "
          f"{len(set(saved) | set(live)) - len(VOLATILE)} compared")
    write_receipt(live)
    return 0


if __name__ == "__main__":
    sys.exit(main())
