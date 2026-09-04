"""Chunk 1.7: does the record writer refuse a record, and publish atomically.

Two claims are on trial, and they need different kinds of proof.

The first is that a complete record satisfies the contract the gate actually
enforces. Asserting that against my own idea of the format would be worthless,
so the record this writer produces is handed to the real
scripts/validate_record.py, with the exact `--require` expressions
`w1_runner` in scripts/gate.sh asks for. If the schema, the grammar or the
writer ever disagree, this fails.

The second is that nothing incomplete or unfinished reaches the disk. A record
missing one provenance field still looks like evidence, a `latest.jsonl` from a
crashed run satisfies a later gate, and a half written file at that path does
both for as long as it takes somebody to notice. Every one of those gets its
own case.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_run_record.py

Runs on a clean checkout with nothing built, with no ROS and no simulator.
test/conftest.py puts the package root on sys.path.
"""

import json
import os
import subprocess
import sys

import pytest

from uavx_sim import run_record
from uavx_sim.run_record import (RecordError, build_record, check_run_id,
                                 invalidate_latest, latest_paths,
                                 mint_run_id, publish_latest, read_versions,
                                 serialise, validate_record, write_record)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                    "..", "..", "..", ".."))
VALIDATE_RECORD = os.path.join(REPO, "scripts", "validate_record.py")
VERSIONS_LOCK = os.path.join(REPO, "stage-1", "setup", "versions.lock")

# Built per index rather than written out. scripts/check_seam.sh counts
# distinct vehicle endpoint literals per file and one file naming two vehicles
# is a violation, and that rule covers the tests as much as the source.
VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))

SCENARIO_PATH = "scenarios/harness_check.yaml"
RUN_ID = "harness_check_20260901T120000Z"
SCENARIO_SHA = "a" * 64
SOURCE_SHA = "b" * 64
GRAPH_SHA = "c" * 64
COMMIT_SHA = "d" * 40
STARTED = "2026-09-01T12:00:00Z"
ENDED = "2026-09-01T12:03:10Z"

# The gate's own list, copied from w1_runner in scripts/gate.sh. Changing a
# threshold here to make a test pass would be moving the goal, so these are
# read as the contract rather than as parameters.
GATE_REQUIRES = (
    "completion==complete",
    "clock_source==ros_sim_time",
    "pose_sample_count>=100",
    "vehicle_ids_observed==uav_1,uav_2,uav_3,uav_4",
    "injected_event_observed==true",
    "injected_event_count>=1",
    "resources.peak_rss_mib>0",
    "resources.peak_rss_mib<10500",
    "resources.swap_used_mib==0",
    "resources.samples>=10",
)


def versions():
    """The enforced pins, as the runner reads them off versions.lock."""
    return read_versions(VERSIONS_LOCK)


def fields(**overrides):
    """The keyword arguments of one complete run of harness_check."""
    base = dict(
        run_id=RUN_ID,
        scenario_path=SCENARIO_PATH,
        scenario_sha256=SCENARIO_SHA,
        seed=17,
        commit_sha=COMMIT_SHA,
        started_at=STARTED,
        ended_at=ENDED,
        completion="complete",
        vehicle_ids_observed=list(VEHICLES),
        pose_sample_count=1043,
        versions=versions(),
        metrics={"clock_messages": 601},
        app_packets_sent_by_node={},
        app_packets_delivered_by_node={},
        injected_events=[{"type": "kill", "target": VEHICLES[1],
                          "requested_t": 30.0, "observed_t": 32.2}],
        requested_duration_s=60.0,
        elapsed_sim_s=60.04,
        clock_source="ros_sim_time",
        source_tree_sha256=SOURCE_SHA,
        resources={"peak_rss_mib": 1832.5, "swap_used_mib": 0.0,
                   "samples": 60, "peak_at_s": 41.0},
        injected_event_observed=True,
        injected_event_count=1,
        graph_snapshot_sha256=GRAPH_SHA,
    )
    base.update(overrides)
    return base


def record(**overrides):
    return build_record(**fields(**overrides))


# --------------------------------------------------- the record the gate reads
def test_a_complete_record_satisfies_the_real_checker(tmp_path):
    """Not my opinion of the schema. The checker the gate runs, on real bytes."""
    path = tmp_path / f"{RUN_ID}.jsonl"
    write_record(path, record())

    arguments = [sys.executable, VALIDATE_RECORD, str(path)]
    for expression in GATE_REQUIRES:
        arguments += ["--require", expression]
    done = subprocess.run(arguments, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    # Every requirement reported, not merely a zero exit. A checker that
    # silently skipped an expression would also exit zero.
    for expression in GATE_REQUIRES:
        assert f"ok    {expression}" in done.stdout


def test_the_record_is_one_utf8_json_line():
    payload = serialise(record())
    assert payload.endswith(b"\n")
    assert payload.count(b"\n") == 1
    # A carriage return here would change the sha256 the seam pass and the
    # final package both recompute from the file.
    assert b"\r" not in payload
    assert json.loads(payload.decode("utf-8"))["run_id"] == RUN_ID


def test_the_same_run_written_twice_is_byte_identical(tmp_path):
    """Standing rule 4: a result nobody can reproduce is not evidence."""
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    write_record(first, record())
    write_record(second, record())
    assert first.read_bytes() == second.read_bytes()

    # And the order the fields were assembled in must not show up in the file.
    shuffled = dict(reversed(list(record().items())))
    assert serialise(shuffled) == serialise(record())


# ------------------------------------------------------------- what it refuses
@pytest.mark.parametrize("field", run_record.REQUIRED_FIELDS)
def test_a_missing_required_field_is_refused(field):
    incomplete = record()
    del incomplete[field]
    with pytest.raises(RecordError) as caught:
        validate_record(incomplete)
    # Reported as missing, not as blank. Found by mutation: deleting the
    # required-field loop changed nothing that a test could see, because every
    # field is also checked individually further down and each of those names
    # it too. What is lost is the diagnosis. "run_id is None" sends whoever
    # reads it looking at the value; "the record is missing run_id" does not.
    assert f"missing {field}" in str(caught.value)


def test_a_missing_field_never_reaches_the_disk(tmp_path):
    incomplete = record()
    del incomplete["source_tree_sha256"]
    with pytest.raises(RecordError):
        write_record(tmp_path / "run.jsonl", incomplete)
    # Not a partial file, and not a temporary one either. Serialisation and
    # validation both happen before the temporary file exists.
    assert list(tmp_path.iterdir()) == []


def test_a_string_true_is_not_a_flag():
    """Chunk 1.7. The field was untyped until this chunk.

    A record carrying the string "true" passed every schema pass, and the
    --require grammar compares a flag only against a flag, so the gate's
    `injected_event_observed==true` failed against it with a message about
    types. Refuse it where it is written instead.
    """
    with pytest.raises(RecordError) as caught:
        record(injected_event_observed="true")
    assert "boolean" in str(caught.value)


def test_a_count_carrying_a_flag_is_refused():
    """bool is a subclass of int, so True would otherwise validate as 1."""
    with pytest.raises(RecordError):
        record(injected_event_count=True)
    with pytest.raises(RecordError):
        record(pose_sample_count=True)
    with pytest.raises(RecordError):
        record(seed=True)


def test_a_short_run_cannot_claim_completion():
    """uavx_eval.check rejects this later. Refusing here means it is never written."""
    with pytest.raises(RecordError) as caught:
        record(elapsed_sim_s=40.0)
    assert "95 percent" in str(caught.value)
    # The same run honestly labelled is a record worth keeping.
    assert record(elapsed_sim_s=40.0, completion="timeout")["elapsed_sim_s"] == 40.0


def test_an_event_outside_the_run_is_refused():
    late = [{"type": "kill", "target": VEHICLES[1], "requested_t": 90.0,
             "observed_t": None}]
    with pytest.raises(RecordError) as caught:
        record(injected_events=late)
    assert "outside" in str(caught.value)

    impossible = [{"type": "kill", "target": VEHICLES[1], "requested_t": 30.0,
                   "observed_t": 400.0}]
    with pytest.raises(RecordError) as caught:
        record(injected_events=impossible)
    assert "after" in str(caught.value)


def test_an_unobserved_event_is_written_as_null():
    """Null is the honest answer, and it keeps the event out of the count."""
    never = [{"type": "kill", "target": VEHICLES[1], "requested_t": 30.0,
              "observed_t": None}]
    written = record(injected_events=never, injected_event_observed=False,
                     injected_event_count=0)
    assert written["injected_events"][0]["observed_t"] is None
    assert b'"observed_t":null' in serialise(written)


@pytest.mark.parametrize("field", ("run_id", "scenario_path", "scenario_sha256",
                                  "commit_sha", "source_tree_sha256",
                                  "started_at", "ended_at"))
@pytest.mark.parametrize("blank", ("", "   "))
def test_a_blank_provenance_field_is_refused(field, blank):
    """Blank is worse than absent, because it looks filled in.

    Found by mutation: making the text check return early on an empty value
    left every test in this file passing, because they all supply a wrong
    value rather than no value.
    """
    with pytest.raises(RecordError) as caught:
        record(**{field: blank})
    assert field in str(caught.value)


def test_a_provenance_hash_of_the_wrong_shape_is_refused():
    for field in ("scenario_sha256", "source_tree_sha256",
                  "graph_snapshot_sha256"):
        with pytest.raises(RecordError):
            record(**{field: "not a hash"})
        with pytest.raises(RecordError):
            record(**{field: "A" * 64})       # uppercase is not [0-9a-f]
    with pytest.raises(RecordError):
        record(commit_sha="0" * 39)


def test_one_vehicle_is_not_a_swarm():
    with pytest.raises(RecordError):
        record(vehicle_ids_observed=[VEHICLES[0]])
    with pytest.raises(RecordError) as caught:
        record(vehicle_ids_observed=[VEHICLES[0], VEHICLES[0]])
    assert "duplicate" in str(caught.value)


def test_pose_sample_count_of_zero_is_refused():
    """Zero samples and no separation violations read identically."""
    with pytest.raises(RecordError):
        record(pose_sample_count=0)


def test_the_clock_label_is_not_decorative():
    with pytest.raises(RecordError) as caught:
        record(clock_source="wall")
    assert "simulated time" in str(caught.value)


def test_ended_before_started_is_refused():
    with pytest.raises(RecordError):
        record(started_at=ENDED, ended_at=STARTED)


def test_a_timestamp_without_a_zone_is_refused():
    """RFC 3339, because provenance with an ambiguous wall clock is not provenance."""
    with pytest.raises(RecordError):
        record(started_at="2026-09-01 12:00:00")
    with pytest.raises(RecordError):
        record(ended_at="2026-09-01T12:03:10")


# ------------------------------------------------------------------ versions
def test_the_lock_supplies_every_enforced_pin():
    """Read from the real stage-1/setup/versions.lock, not from a fixture.

    Round 4 finding 8: the versions block named four keys, so a run could be
    labelled with an incomplete version object and attributed to a stack it did
    not come off. If a pin is ever added to the lock and the schema, this is
    what notices the record cannot fill it.
    """
    block = versions()
    for key in run_record.REQUIRED_VERSION_KEYS:
        assert block.get(key), key
    # The lock's observed_ entries are context for a human and must not be
    # mistaken for pins.
    assert not [key for key in block if key.startswith("observed_")]


def test_a_missing_pin_is_a_failure_not_a_blank(tmp_path):
    lock = tmp_path / "versions.lock"
    lines = [f"{key}=x" for key in run_record.REQUIRED_VERSION_KEYS[:-1]]
    lock.write_text("# a comment\n" + "\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(RecordError) as caught:
        read_versions(lock)
    assert run_record.REQUIRED_VERSION_KEYS[-1] in str(caught.value)


def test_a_blank_pin_in_the_record_is_refused():
    thin = dict(versions())
    thin["px4_sha"] = ""
    with pytest.raises(RecordError) as caught:
        record(versions=thin)
    assert "px4_sha" in str(caught.value)


# -------------------------------------------------------------- the run id
def test_the_run_id_grammar_is_the_frozen_one():
    check_run_id(RUN_ID)
    for bad in ("harness-check-1", "harness check 1", "runs/../id_1234",
                "short_1", ""):
        with pytest.raises(RecordError):
            check_run_id(bad)


def test_minting_never_reuses_an_id(tmp_path):
    first = mint_run_id("harness_check", tmp_path)
    (tmp_path / f"{first}.jsonl").write_text("{}", encoding="utf-8")
    second = mint_run_id("harness_check", tmp_path)
    assert second != first
    check_run_id(second)


# ---------------------------------------------------------------- publishing
def test_a_run_that_did_not_complete_publishes_no_latest(tmp_path):
    """Round 2 finding 5: a stale green latest.jsonl survived a crashed run."""
    for outcome in ("crashed", "timeout", "killed"):
        unfinished = record(completion=outcome, elapsed_sim_s=45.0)
        with pytest.raises(RecordError) as caught:
            publish_latest(tmp_path, unfinished)
        assert outcome in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_publishing_replaces_the_previous_latest(tmp_path):
    latest_record, latest_graph = latest_paths(tmp_path)
    latest_record.write_text("{\"run_id\": \"an older run\"}\n", encoding="utf-8")
    latest_graph.write_text("{}\n", encoding="utf-8")

    published = publish_latest(tmp_path, record())
    assert published == latest_record
    assert json.loads(latest_record.read_text(encoding="utf-8"))["run_id"] == RUN_ID
    assert latest_record.read_bytes() == serialise(record())


def test_the_publish_is_a_rename_of_a_sibling_file(tmp_path, monkeypatch):
    """"By atomic rename" is the contract sentence, so assert the rename.

    Found by mutation: replacing the temporary file and rename with a plain
    write left every test here passing, because a plain write produces exactly
    the same bytes when nothing goes wrong. What it loses is what happens when
    something does.
    """
    seen = []
    real_replace = run_record.os.replace

    def watched(source, destination):
        seen.append((str(source), str(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(run_record.os, "replace", watched)
    publish_latest(tmp_path, record())

    assert len(seen) == 1, "the record reached its path other than by a rename"
    source, destination = seen[0]
    assert destination == str(latest_paths(tmp_path)[0])
    # The same directory. A rename across filesystems is a copy, and a copy is
    # not atomic.
    assert os.path.dirname(source) == str(tmp_path)
    assert os.path.basename(source).startswith(run_record.TEMP_PREFIX)


def test_a_failed_publish_leaves_no_partial_file(tmp_path, monkeypatch):
    """The rename is the publish. A reader sees the old file or the new one."""
    latest_record, _ = latest_paths(tmp_path)
    previous = serialise(record(run_id="an_earlier_run_1200"))
    latest_record.write_bytes(previous)

    def refuse(source, destination):
        raise OSError("the rename failed")

    monkeypatch.setattr(run_record.os, "replace", refuse)
    with pytest.raises(OSError):
        publish_latest(tmp_path, record())

    assert latest_record.read_bytes() == previous
    leftovers = [p.name for p in tmp_path.iterdir() if p != latest_record]
    assert leftovers == [], leftovers


def test_invalidate_removes_both_published_files(tmp_path):
    """Round 6 finding 6: deleting only the record left the previous graph."""
    latest_record, latest_graph = latest_paths(tmp_path)
    latest_record.write_text("{}\n", encoding="utf-8")
    latest_graph.write_text("{}\n", encoding="utf-8")
    invalidate_latest(tmp_path)
    assert not latest_record.exists()
    assert not latest_graph.exists()
    # Twice, because the runner clears both before every launch and a run that
    # is the first in a clean directory must not fail for want of a file.
    invalidate_latest(tmp_path)


def test_the_record_and_graph_paths_are_derived_from_the_id(tmp_path):
    assert run_record.record_path(tmp_path, RUN_ID).name == f"{RUN_ID}.jsonl"
    assert run_record.graph_path(tmp_path, RUN_ID).name == f"{RUN_ID}-graph.json"
    with pytest.raises(RecordError):
        run_record.record_path(tmp_path, "../escape")


# ------------------------------------------------------------- measurements
def test_provenance_that_cannot_be_measured_raises(tmp_path):
    """A field nobody could fill in is a failure, never a placeholder."""
    with pytest.raises(RecordError):
        run_record.commit_sha(tmp_path)
    with pytest.raises(RecordError) as caught:
        run_record.source_tree_sha256(tmp_path)
    assert "source_tree_hash.py" in str(caught.value)


def test_the_scenario_hash_is_of_the_file_as_read(tmp_path):
    scenario = tmp_path / "harness_check.yaml"
    scenario.write_bytes(b"name: harness_check\n")
    first = run_record.sha256_of_file(scenario)
    assert len(first) == 64
    scenario.write_bytes(b"name: harness_check\nseed: 17\n")
    assert run_record.sha256_of_file(scenario) != first


# ------------------------------------------------------ chunk 2.4, coverage
def coverage(**overrides):
    base = {"coverage_fraction": 0.9725, "coverage_source": "pose_samples",
            "coverage_cells_total": 400, "coverage_cells_seen": 389}
    base.update(overrides)
    return base


def test_a_surveying_run_carries_its_coverage_at_the_top_level():
    rec = record(coverage=coverage())
    for key, value in coverage().items():
        assert rec[key] == value


def test_a_run_that_surveys_nothing_carries_no_coverage_fields():
    rec = record()
    for key in run_record.COVERAGE_FIELDS:
        assert key not in rec


def test_a_fraction_that_is_not_its_counts_is_refused():
    with pytest.raises(RecordError, match="not 389 over 400"):
        record(coverage=coverage(coverage_fraction=0.99))


def test_a_partial_coverage_block_is_refused():
    with pytest.raises(RecordError, match="all four"):
        record(coverage={"coverage_fraction": 0.9725,
                         "coverage_source": "pose_samples"})


def test_a_fraction_outside_the_unit_interval_is_refused():
    with pytest.raises(RecordError, match="coverage_fraction"):
        record(coverage=coverage(coverage_fraction=1.2,
                                 coverage_cells_seen=480))


def test_more_cells_seen_than_exist_is_refused():
    with pytest.raises(RecordError, match="exceeds"):
        record(coverage=coverage(coverage_cells_seen=401,
                                 coverage_fraction=1.0))


def test_a_blank_coverage_label_is_refused():
    with pytest.raises(RecordError, match="coverage_source"):
        record(coverage=coverage(coverage_source=""))


def test_the_real_checker_reads_coverage_with_the_w2_expressions(tmp_path):
    """The two expressions w2_survey asserts, against the bytes on disk."""
    path = tmp_path / f"{RUN_ID}.jsonl"
    write_record(path, record(coverage=coverage()))
    expressions = ("coverage_fraction>=0.95", "coverage_source==pose_samples")
    arguments = [sys.executable, VALIDATE_RECORD, str(path)]
    for expression in expressions:
        arguments += ["--require", expression]
    done = subprocess.run(arguments, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    for expression in expressions:
        assert f"ok    {expression}" in done.stdout


def test_the_schema_refuses_a_coverage_label_it_does_not_know(tmp_path):
    """enum in the schema is enforced, not decoration."""
    rec = record(coverage=coverage(coverage_source="planned_path"))
    path = tmp_path / f"{RUN_ID}.jsonl"
    write_record(path, rec)
    done = subprocess.run([sys.executable, VALIDATE_RECORD, str(path)],
                         capture_output=True, text=True)
    assert done.returncode != 0, done.stdout + done.stderr
