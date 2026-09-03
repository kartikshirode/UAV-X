"""The provenance gate every run record passes before a number is read out of it.

stage-1/architecture.md section 7: a metric can only be trusted after its
provenance is. This module is where that sentence is enforced. The gate calls
it through `check_run`, and `scripts/check_submission.py` calls it again in
chunk 4.8 over all nine records the proposal cites, so a record that gets past
here is a record a judge is handed.

    python3 -m uavx_eval.check runs/latest.jsonl \
        --expect-scenario scenarios/harness_check.yaml
    python3 -m uavx_eval.check runs/latest.jsonl \
        --expect-scenario scenarios/survey_baseline.yaml \
        --require "coverage_fraction>=0.95"

What provenance means here, and why each part is separate. Every one of these
is a way for a file to look like evidence without being evidence, and every one
of them has its own test in test/test_check_provenance.py:

  the record satisfies the contract  scenarios/run-record.schema.json, plus the
                                     semantic rules JSON Schema cannot state
  the source hash matches the tree   scripts/source_tree_hash.py over the
                                     working tree. A number produced by code
                                     that is no longer here is a number nobody
                                     can reproduce
  the scenario is the expected one   the gate asked for a scenario and the
                                     record has to be a run of that one, or a
                                     previous scenario's record satisfies this
                                     gate
  the scenario file has not moved    sha256 of the file on disk against
                                     scenario_sha256, so an edited scenario
                                     cannot reuse an old result
  the commit is real                 git can find commit_sha in this repository
  the run finished                   completion is complete
  every injected event fired         an event with a null observed_t never took
                                     effect, so the run did not test what it
                                     claims
  no denominator is zero             a zero in app_packets_sent_by_node makes
                                     its ratio meaningless, and nothing over
                                     nothing reads as a perfect score
  the file is not older than the run bytes written before the run they describe
                                     are a previous run's bytes

Only after all of that does `--require` get evaluated. Round 4 of this
project's reviews found two checkers that had never been shown to fail, so the
tests here drive every rejection above on its own, from a baseline they first
prove is accepted.

The `--require` grammar is not defined here. It is defined once, in
scripts/validate_record.py, and imported. Two implementations of one grammar is
how a gate and its checker stop agreeing about what a gate asserts, which is
round 7 finding 7. scripts/test_require_grammar.py holds that definition to its
cases and this module inherits whatever it says.

Exit 0 the record holds, 1 the record is rejected, 2 the check could not be
carried out at all.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The three files this module cannot work without, and therefore the marker
# that says a directory is the UAV-X repository rather than some parent of it.
SCHEMA_REL = "scenarios/run-record.schema.json"
HASH_SCRIPT_REL = "scripts/source_tree_hash.py"
GRAMMAR_REL = "scripts/validate_record.py"
REPO_MARKERS = (SCHEMA_REL, HASH_SCRIPT_REL, GRAMMAR_REL)

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_UNREADABLE = 2

# The record is written when the run ends, so its mtime is normally later than
# started_at by the whole duration of the run, and a copy, a checkout or a
# restore only ever moves it later still. Earlier means the bytes existed
# before the run they describe, which is the stale latest.jsonl of round 2
# finding 5. The tolerance is for filesystem timestamp granularity and for
# nothing else: seconds, against a staleness measured in runs.
LAUNCH_MTIME_TOLERANCE_S = 2.0


class Unreadable(Exception):
    """The check could not be carried out, which is not the same as a rejection.

    A provenance gate that cannot reach git, the schema or the hashing script
    has learned nothing about the record. Saying so is the only honest answer,
    and it exits 2 rather than 0 so nothing downstream reads silence as
    approval.
    """


# ----------------------------------------------------------------- the repo
def _is_repo(candidate: Path) -> bool:
    return all((candidate / rel).is_file() for rel in REPO_MARKERS)


def _walk_up(start: Path):
    start = Path(start)
    if start.is_file():
        start = start.parent
    yield start
    yield from start.parents


def find_repo(override=None, record_path=None) -> Path:
    """Locate the repository this record has to be checked against.

    Order matters. The gate exports UAVX_REPO, chunk 4.8 runs this with the
    repository as its working directory, and a developer runs it from anywhere
    with the package installed. Resolving __file__ works under the symlink
    install the gate builds, because the link points back into uavx_ws/src.
    """
    tried = []
    starts = []
    if override:
        starts.append(Path(override))
    env = os.environ.get("UAVX_REPO")
    if env:
        starts.append(Path(env))
    starts.append(Path(__file__).resolve())
    starts.append(Path.cwd())
    if record_path:
        starts.append(Path(record_path).resolve())

    for start in starts:
        for candidate in _walk_up(start):
            if candidate in tried:
                continue
            tried.append(candidate)
            if _is_repo(candidate):
                return candidate
    raise Unreadable(
        "cannot find the UAV-X repository. Looked upward from "
        + ", ".join(str(s) for s in starts)
        + " for " + ", ".join(REPO_MARKERS)
        + ". Set UAVX_REPO or pass --repo.")


def _script_module(repo: Path, name: str):
    """Import one of the repository's own scripts.

    Deliberate, and the opposite of what uavx_sim does. That package writes
    records and has to run on a machine laid out any way at all, so it restates
    the schema rather than importing the gate's checker. This module reads
    records against the working tree, so it already cannot do its job without
    the repository. Importing the one definition of the grammar therefore costs
    nothing and stops a second definition existing.
    """
    scripts = str(repo / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise Unreadable(
            f"cannot import scripts/{name}.py from {repo}: {exc}") from exc


# ------------------------------------------------------------ measurements
def sha256_of_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(repo: Path) -> str:
    """What scripts/source_tree_hash.py says the working tree hashes to.

    Never reimplemented. The runner records this number by running the same
    script and the final package compares it against the frozen source, so
    three readers have to agree byte for byte. Two implementations of one hash
    is how they stop agreeing.
    """
    script = Path(repo) / HASH_SCRIPT_REL
    if not script.is_file():
        raise Unreadable(f"no {script}, so there is nothing to hash the tree with")
    try:
        done = subprocess.run([sys.executable, str(script)], cwd=str(repo),
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unreadable(f"{HASH_SCRIPT_REL} could not be run: {exc}") from exc
    if done.returncode != 0:
        raise Unreadable(f"{HASH_SCRIPT_REL} exited {done.returncode}: "
                         f"{done.stderr.strip()[:200]}")
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if not lines:
        raise Unreadable(f"{HASH_SCRIPT_REL} printed nothing")
    return lines[-1]


def commit_is_real(repo: Path, sha: str) -> bool:
    """Does git in this repository know that commit.

    Existence, not ancestry. A run made on a branch that was later rebased away
    still came off code that existed, and refusing it would reject honest
    evidence. A commit id git has never heard of came from somewhere else.
    """
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", str(sha) + "^{commit}"],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unreadable(f"git could not be run against {repo}: {exc}") from exc
    return done.returncode == 0


def normalise_scenario(path_text, repo=None) -> str:
    """One spelling for a scenario path, so a separator cannot fail a good run."""
    text = str(path_text or "").replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    if repo is not None and text:
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                text = candidate.resolve().relative_to(
                    Path(repo).resolve()).as_posix()
            except ValueError:
                pass
    return text.rstrip("/")


def _parse_stamp(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


# -------------------------------------------------------------- the stages
def schema_errors(repo: Path, record: dict) -> list:
    """The record against scenarios/run-record.schema.json and its semantics."""
    grammar = _script_module(repo, "validate_record")
    try:
        schema = json.loads(
            (Path(repo) / SCHEMA_REL).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(
            f"cannot read {Path(repo) / SCHEMA_REL}: {exc}") from exc
    return (list(grammar.validate(record, schema))
            + list(grammar.semantic_errors(record)))


def provenance_errors(repo: Path, record: dict, record_path: Path,
                      expect_scenario) -> list:
    """Everything that has to hold before a metric in this file means anything."""
    problems = []

    want = normalise_scenario(expect_scenario, repo)
    if not want:
        raise Unreadable(
            "--expect-scenario is empty. Without it the record is not tied to "
            "the scenario the gate asked for, which is the whole reason a "
            "previous run's file cannot pass this one.")
    got = normalise_scenario(record.get("scenario_path"), repo)
    if got != want:
        problems.append(
            f"this is a run of {got or 'no scenario at all'} and the gate asked "
            f"for {want}. A record from another scenario is not evidence for "
            f"this one however good its numbers are.")

    if got:
        scenario_file = Path(repo) / got
        if not scenario_file.is_file():
            problems.append(
                f"the record names {got} and no such file is in the tree, so "
                f"nothing can confirm what was actually run.")
        else:
            on_disk = sha256_of_file(scenario_file)
            claimed = str(record.get("scenario_sha256") or "")
            if on_disk != claimed:
                problems.append(
                    f"{got} hashes to {on_disk[:12]} and the record was made "
                    f"from {claimed[:12] or 'nothing'}. The scenario has been "
                    f"edited since, so this result describes a different run.")

    commit = str(record.get("commit_sha") or "")
    if not commit_is_real(repo, commit):
        problems.append(
            f"git has no commit {commit[:12] or '(none)'} in this repository. "
            f"The record names code nobody can check out.")

    claimed_tree = str(record.get("source_tree_sha256") or "")
    actual_tree = tree_digest(repo)
    if claimed_tree != actual_tree:
        problems.append(
            f"the source tree hashes to {actual_tree[:12]} and this run was "
            f"made from {claimed_tree[:12] or 'nothing'}. The code that "
            f"produced these numbers is not the code that is here.")

    completion = record.get("completion")
    if completion != "complete":
        problems.append(
            f"completion is {completion!r}. Only a complete run is evidence; a "
            f"run that crashed, timed out or was killed still has metrics in it.")

    unfired = [event for event in (record.get("injected_events") or [])
               if isinstance(event, dict) and event.get("observed_t") is None]
    if unfired:
        named = ", ".join(f"{e.get('type')} on {e.get('target')}"
                          for e in unfired[:3])
        problems.append(
            f"{len(unfired)} injected event(s) never took effect ({named}). The "
            f"fault this run claims to survive did not happen.")

    sent = record.get("app_packets_sent_by_node")
    if isinstance(sent, dict):
        zeroes = sorted(node for node, count in sent.items() if count == 0)
        if zeroes:
            problems.append(
                f"app_packets_sent_by_node is zero for {', '.join(zeroes)}. A "
                f"zero denominator makes that node's delivery ratio "
                f"meaningless, and nothing over nothing reads as a perfect "
                f"score.")

    started = _parse_stamp(record.get("started_at"))
    if started is not None:
        try:
            written = Path(record_path).stat().st_mtime
        except OSError as exc:
            raise Unreadable(f"cannot stat {record_path}: {exc}") from exc
        if written + LAUNCH_MTIME_TOLERANCE_S < started.timestamp():
            problems.append(
                f"{Path(record_path).name} was last written "
                f"{started.timestamp() - written:.0f}s before the run it "
                f"describes started. These bytes predate their own launch.")

    return problems


def requirement_errors(repo: Path, record: dict, requires) -> list:
    """The gate's expressions, read through the one definition of the grammar."""
    grammar = _script_module(repo, "validate_record")
    problems = []
    for expr in requires:
        why = grammar.check_require(record, expr)
        if why:
            problems.append(f"{expr}: {why}")
    return problems


# --------------------------------------------------------------- the check
def load_record(path) -> dict:
    """One JSON object, whether it arrived as a .jsonl line or a pretty file."""
    path = Path(path)
    if not path.is_file():
        raise Unreadable(f"no record at {path}")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(f"{path.name} is not readable JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise Unreadable(f"{path.name} holds a {type(record).__name__}, and a "
                         f"run record is one JSON object")
    return record


def check_record(record_path, expect_scenario, requires=(), repo=None,
                 report=None) -> list:
    """Return every reason this record is not evidence. Empty means it is.

    The order is the argument. Provenance is settled first and the `--require`
    expressions are not looked at until it holds, because reading a metric out
    of a file whose provenance failed is reading a number off an unknown run.
    """
    say = report if report is not None else (lambda line: None)
    root = Path(repo) if repo else find_repo(record_path=record_path)
    path = Path(record_path)
    record = load_record(path)

    problems = schema_errors(root, record)
    if problems:
        say(f"  FAIL  {path.name} does not satisfy the run record contract")
        return problems
    say(f"  ok    {path.name} satisfies {SCHEMA_REL}, "
        f"run {record.get('run_id')}")

    problems = provenance_errors(root, record, path, expect_scenario)
    if problems:
        say(f"  FAIL  provenance does not hold, so no metric in {path.name} "
            f"was read")
        return problems
    say(f"  ok    provenance holds against {root}")

    problems = requirement_errors(root, record, requires)
    for expr in requires:
        hit = [p for p in problems if p.startswith(str(expr) + ":")]
        say(("  FAIL  " + hit[0]) if hit else f"  ok    {expr}")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="uavx_eval.check",
        description="Validate a run record's provenance before any metric in "
                    "it is read.")
    parser.add_argument("record", help="the run record, runs/<run_id>.jsonl")
    parser.add_argument("--expect-scenario", required=True,
                        help="the scenario the caller asked to be run, "
                             "repository relative")
    parser.add_argument("--require", action="append", default=[],
                        metavar="EXPR",
                        help="an expression the record must satisfy. The "
                             "grammar is the one in scripts/validate_record.py")
    parser.add_argument("--repo", default=None,
                        help="the repository to check against. Found from "
                             "UAVX_REPO, from this file or from the working "
                             "directory when it is not given")
    args = parser.parse_args(argv)

    try:
        problems = check_record(args.record, args.expect_scenario,
                                args.require, repo=args.repo, report=print)
    except Unreadable as exc:
        print(f"  FAIL  {exc}")
        return EXIT_UNREADABLE

    if problems:
        for problem in problems[:12]:
            print(f"          {problem}")
        if len(problems) > 12:
            print(f"          and {len(problems) - 12} more")
        return EXIT_REJECTED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
