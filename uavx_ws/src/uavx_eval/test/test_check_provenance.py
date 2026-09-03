"""Every way a run record can fail provenance, watched failing.

Round 4 of this project's reviews found two checkers that had never been shown
to fail, and stage-1/plan.md gives chunk 2.2 one question: does it reject a
record whose provenance does not hold. A checker that only accepts good input
has not been tested, so every test below starts from a baseline this file first
proves is accepted, breaks exactly one thing, and asserts that exactly one
reason comes back.

The exactly-one assertion is the point. A mutation that produces three
complaints has not shown which rule caught it, and a rule that fires on
everything catches nothing.

Nothing here starts a simulator. The baseline is
`scenarios/run-record.example.jsonl`, the committed example architecture.md
section 1b calls the checked one, with its three placeholder provenance fields
filled in with the truth about this tree.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from uavx_eval import check

SCENARIO = "scenarios/harness_check.yaml"
ABSENT_COMMIT = "0" * 40
ABSENT_TREE = "0" * 64


MOVED = ("the working tree changed while this suite was running, so the "
         "baseline's source hash went stale between being written and being "
         "checked. That is the checker working, not failing: it reads the tree "
         "live on every call. Run it again on a quiet tree.")


def reasons(repo, path, expect=SCENARIO, requires=()):
    return check.check_record(path, expect, requires, repo=repo)


def explain(problems):
    """Name the one failure mode that is about the clock rather than the code."""
    if any("source tree hashes to" in p for p in problems):
        return f"{problems}\n\n{MOVED}"
    return str(problems)


def only(problems):
    """The single reason, asserting there is exactly one."""
    assert len(problems) == 1, f"expected one reason, got {explain(problems)}"
    return problems[0]


def none(problems):
    assert problems == [], f"expected no reason, got {explain(problems)}"


# ------------------------------------------------------- the baseline holds
def test_the_baseline_is_accepted(repo, baseline, write_record):
    """Without this every rejection below proves nothing.

    A suite whose baseline is already broken watches its checker refuse
    everything and calls that strictness.
    """
    none(reasons(repo, write_record(baseline)))


def test_the_committed_records_still_hold(repo, write_record):
    """The real thing the runner wrote, not only the example.

    A synthetic baseline proves the checker accepts what a test author thinks a
    record looks like. These are the records chunk 1.7 actually produced, with
    only `source_tree_sha256` refreshed, because the tree has moved on since
    they were made and that is the one field that is supposed to go stale.
    """
    records = sorted(p for p in (repo / "runs").glob("*.jsonl")
                     if p.name != "latest.jsonl")
    if not records:
        pytest.skip("no committed run records under runs/ to check")
    for source in records:
        record = json.loads(source.read_text(encoding="utf-8"))
        # Taken inside the loop, not once above it. The digest is read from the
        # live tree on every call, so hoisting it out makes a long loop
        # disagree with itself the moment anything under the hashed paths
        # changes while the loop is running.
        record["source_tree_sha256"] = check.tree_digest(repo)
        problems = reasons(repo, write_record(record),
                           expect=record["scenario_path"])
        assert problems == [], f"{source.name} was rejected: {explain(problems)}"


# ----------------------------------------------------------- the rejections
def test_rejects_a_source_hash_that_is_not_the_tree(repo, baseline,
                                                    write_record):
    """The gate's own mutation, and the reason this module exists.

    scripts/gate.sh w2_eval_tests rewrites this field to sixty-four zeroes and
    requires the check to fail. A run whose code is no longer in the tree is a
    number nobody can reproduce, and standing rule 3 says that is not evidence.
    """
    baseline["source_tree_sha256"] = ABSENT_TREE
    assert "source tree" in only(reasons(repo, write_record(baseline)))


def test_rejects_a_record_of_another_scenario(repo, baseline, write_record):
    """A previous scenario's record must not satisfy this gate."""
    problem = only(reasons(repo, write_record(baseline),
                           expect="scenarios/survey_baseline.yaml"))
    assert "survey_baseline" in problem and "harness_check" in problem


def test_rejects_a_scenario_hash_that_is_not_the_file(repo, baseline,
                                                      write_record):
    """An edited scenario cannot reuse an old result."""
    digest = baseline["scenario_sha256"]
    baseline["scenario_sha256"] = ("f" if digest[0] != "f" else "a") + digest[1:]
    assert "hashes to" in only(reasons(repo, write_record(baseline)))


def test_rejects_a_scenario_that_is_not_in_the_tree(repo, baseline,
                                                    write_record):
    """A record naming a file nobody has cannot be confirmed against anything."""
    missing = "scenarios/no_such_scenario.yaml"
    assert not (repo / missing).exists()
    baseline["scenario_path"] = missing
    problem = only(reasons(repo, write_record(baseline), expect=missing))
    assert "no such file" in problem


def test_rejects_a_commit_that_is_not_real(repo, baseline, write_record):
    """The record has to name code somebody can check out."""
    assert not check.commit_is_real(repo, ABSENT_COMMIT)
    baseline["commit_sha"] = ABSENT_COMMIT
    assert "no commit" in only(reasons(repo, write_record(baseline)))


def test_rejects_a_record_missing_a_required_block(repo, baseline,
                                                   write_record):
    """The schema is the contract, and a record that fails it is not read."""
    del baseline["resources"]
    problems = reasons(repo, write_record(baseline))
    assert problems and any("resources" in p for p in problems)


def test_rejects_a_pose_sample_count_of_zero(repo, baseline, write_record):
    """Zero samples and a clean run read identically until something says so."""
    baseline["pose_sample_count"] = 0
    problems = reasons(repo, write_record(baseline))
    assert problems and any("pose_sample_count" in p for p in problems)


def test_rejects_a_run_that_did_not_complete(repo, baseline, write_record):
    """A crashed run still has metrics in it, which is the danger."""
    baseline["completion"] = "crashed"
    assert "completion" in only(reasons(repo, write_record(baseline)))


def test_rejects_an_injected_event_that_never_fired(repo, baseline,
                                                    write_record):
    """The fault the run claims to survive has to have actually happened."""
    baseline["injected_events"][0]["observed_t"] = None
    assert "never took effect" in only(reasons(repo, write_record(baseline)))


def test_rejects_a_zero_denominator(repo, baseline, write_record):
    """Nothing over nothing is not a delivery ratio of one."""
    baseline["app_packets_sent_by_node"]["uav_4"] = 0
    problem = only(reasons(repo, write_record(baseline)))
    assert "zero" in problem and "uav_4" in problem


def test_rejects_a_run_shorter_than_it_asked_for(repo, baseline, write_record):
    """A 20 second run can meet a packet minimum and claim completion."""
    baseline["elapsed_sim_s"] = baseline["requested_duration_s"] / 2
    problems = reasons(repo, write_record(baseline))
    assert problems and any("percent duration" in p for p in problems)


def test_rejects_a_record_older_than_its_own_run(repo, baseline, write_record):
    """Round 2 finding 5, in file times: a stale green record from last week."""
    path = write_record(baseline)
    started = check._parse_stamp(baseline["started_at"]).timestamp()
    stale = started - 3600
    os.utime(path, (stale, stale))
    assert "predate their own launch" in only(reasons(repo, path))


def test_accepts_a_record_written_after_its_run(repo, baseline, write_record):
    """The other direction, so the rule above is not simply always true.

    A record is written when the run ends and a checkout or a copy moves its
    mtime later still, so later is the normal case and must not be refused.
    """
    path = write_record(baseline)
    ended = check._parse_stamp(baseline["ended_at"]).timestamp()
    os.utime(path, (ended + 86400, ended + 86400))
    none(reasons(repo, path))


# ------------------------------------- provenance comes before every metric
def test_no_metric_is_read_while_provenance_fails(repo, baseline,
                                                  write_record):
    """The plan's sentence, as behaviour rather than as a comment.

    The requirement below is false of this record. If it appears in the output
    then a metric was read out of a file whose provenance had already failed,
    which is the thing architecture.md section 7 forbids.
    """
    baseline["source_tree_sha256"] = ABSENT_TREE
    problems = reasons(repo, write_record(baseline),
                       requires=["pose_sample_count>=100000"])
    assert "source tree" in only(problems)
    assert not any("pose_sample_count" in p for p in problems)


def test_a_requirement_is_read_once_provenance_holds(repo, baseline,
                                                     write_record):
    path = write_record(baseline)
    none(reasons(repo, path, requires=["pose_sample_count>=100"]))
    failed = only(reasons(repo, path, requires=["pose_sample_count>=100000"]))
    assert failed.startswith("pose_sample_count>=100000:")


def test_the_requirement_grammar_is_the_repository_definition(repo, baseline,
                                                             write_record):
    """Not a second grammar that happens to agree today.

    A boolean flag is the case a hand-written comparison gets wrong in both
    directions: `str(True)` is "True" so `==true` fails, and an ordered
    operator on a flag quietly succeeds. scripts/validate_record.py handles
    both and scripts/test_require_grammar.py holds it to them, so passing the
    first and rejecting the second is evidence that this module is using it.
    """
    path = write_record(baseline)
    assert baseline["injected_event_observed"] is True
    none(reasons(repo, path, requires=["injected_event_observed==true"]))
    ordered = only(reasons(repo, path, requires=["injected_event_observed>=1"]))
    assert "ordered comparison" in ordered


# ------------------------------------------------- it says when it cannot tell
def test_a_missing_record_is_unreadable_not_acceptable(repo, tmp_path):
    with pytest.raises(check.Unreadable):
        reasons(repo, tmp_path / "nothing.jsonl")


def test_a_record_that_is_not_json_is_unreadable(repo, tmp_path):
    path = tmp_path / "broken.jsonl"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(check.Unreadable):
        reasons(repo, path)


def test_an_empty_expect_scenario_is_refused(repo, baseline, write_record):
    """Without it the record is tied to no scenario at all."""
    with pytest.raises(check.Unreadable):
        reasons(repo, write_record(baseline), expect="")


def test_a_tree_that_is_not_the_repository_is_unreadable(tmp_path, baseline,
                                                         write_record):
    with pytest.raises(check.Unreadable):
        check.check_record(write_record(baseline), SCENARIO, repo=tmp_path)


# --------------------------------------------- the gate's own invocation
def run_cli(repo, path, expect, *extra):
    """Exactly the shape scripts/gate.sh w2_eval_tests uses."""
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [package_root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    return subprocess.run(
        [sys.executable, "-m", "uavx_eval.check", str(path),
         "--expect-scenario", expect, *extra],
        cwd=str(repo), env=env, capture_output=True, text=True)


def test_the_command_line_accepts_a_good_record(repo, baseline, write_record):
    done = run_cli(repo, write_record(baseline), SCENARIO)
    assert done.returncode == check.EXIT_OK, done.stdout + done.stderr + MOVED
    assert "provenance holds" in done.stdout


def test_the_command_line_rejects_the_gates_own_mutation(repo, baseline,
                                                         write_record):
    """The gate writes sixty-four zeroes into this field and requires a refusal."""
    baseline["source_tree_sha256"] = ABSENT_TREE
    done = run_cli(repo, write_record(baseline), SCENARIO)
    assert done.returncode == check.EXIT_REJECTED, done.stdout + done.stderr
    assert "source tree" in done.stdout


def test_the_command_line_refuses_to_run_without_a_scenario(repo, baseline,
                                                            write_record):
    """--expect-scenario is not optional, so no caller can drop the binding."""
    path = write_record(baseline)
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [package_root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    done = subprocess.run(
        [sys.executable, "-m", "uavx_eval.check", str(path)],
        cwd=str(repo), env=env, capture_output=True, text=True)
    assert done.returncode != check.EXIT_OK
