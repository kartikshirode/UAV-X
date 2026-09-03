"""Make the uavx_eval tests runnable by plain pytest as well as by colcon.

The gate builds the overlay before it tests, so the installed `uavx_eval` is
normally importable. This falls back to the source tree when it is not, so
`pytest uavx_ws/src/uavx_eval/test/` works from a clean checkout with nothing
built. uavx_sim's conftest does the same thing for the same reason.

It also puts the repository's `scripts/` on the path. The tests hold this
package to numbers that are frozen elsewhere: the safety floor in
`check_geometry.py`, and the run record contract in the schema. Week 1 audit
finding 2 is what happens when a test writes a frozen number out again instead
of importing it, so the sys.path juggling lives here and the tests import.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
REPO = PACKAGE_ROOT.parent.parent.parent

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

REPO_SCRIPTS = REPO / "scripts"
if REPO_SCRIPTS.is_dir() and str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))

# The canonical record. architecture.md section 1b calls it the checked
# one-line example, and it carries placeholder provenance on purpose: an all
# zero scenario hash, a commit of ones and a source hash of twos. Filling those
# three in with the truth is what turns it into a baseline this checker has to
# accept, and it means the baseline is a committed file rather than a shape a
# test author imagined.
EXAMPLE_REL = "scenarios/run-record.example.jsonl"


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def head_commit() -> str:
    """A commit this repository really has, for the records that should pass."""
    done = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if done.returncode != 0:
        pytest.skip(f"no git in {REPO}: {done.stderr.strip()}")
    return done.stdout.strip()


@pytest.fixture
def tree_sha() -> str:
    """The working tree digest, taken fresh for each test that needs one.

    Per test rather than once per session, and the reason is worth stating.
    `check.tree_digest` reads the tree again inside every call, so a baseline
    built from a digest taken minutes earlier is stale the moment anything under
    uavx_ws, scenarios, scripts or stage-1/setup changes, and the whole suite
    then fails on a rejection that is correct. Taking it immediately before use
    narrows that window from the length of the suite to the length of one
    check. It cannot close it: this checker compares a record against a live
    tree by design, and editing source while it runs is editing the thing being
    measured.
    """
    from uavx_eval import check
    return check.tree_digest(REPO)


@pytest.fixture
def baseline(repo, head_commit, tree_sha, tmp_path):
    """A record that holds up, written where a test can then break one field."""
    record = json.loads((repo / EXAMPLE_REL).read_text(encoding="utf-8"))
    from uavx_eval import check
    record["scenario_sha256"] = check.sha256_of_file(
        repo / record["scenario_path"])
    record["commit_sha"] = head_commit
    record["source_tree_sha256"] = tree_sha
    return record


@pytest.fixture
def write_record(tmp_path):
    """Put a record on disk and hand back its path."""
    counter = {"n": 0}

    def write(record, name=None):
        counter["n"] += 1
        path = tmp_path / (name or f"record-{counter['n']}.jsonl")
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        return path

    return write
