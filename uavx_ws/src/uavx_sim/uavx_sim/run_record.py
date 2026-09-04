"""Build, validate and publish the run record.

`scenarios/run-record.schema.json` is the contract and `scripts/validate_record.py`
is the consumer. This module writes what both accept, and refuses to write
anything else.

Three rules shape every function here.

  1. A field nobody measured is a failure, not a placeholder. `commit_sha`,
     `source_tree_sha256`, `scenario_sha256` and the `versions` block are read
     off the machine and the tree at run time. There is no default for any of
     them, and no argument here is optional because it was inconvenient to
     supply.
  2. Nothing reaches `latest.jsonl` except by atomic rename, and only after the
     run has finished and said `complete`. Round 2 finding 5: a stale green
     `latest.jsonl` survived a crashed simulation and satisfied a later gate.
     A partial write at that path would do the same thing for a shorter time
     and be harder to find.
  3. The same run written twice is the same bytes. The graph snapshot's
     sha256 goes into the record and the record's own reproducibility is what
     standing rule 4 asks for, so serialisation is sorted, separator fixed,
     ASCII escaped and written binary.

The validation below deliberately restates the schema rather than importing a
validator from `scripts/`. `graph_snapshot.py` made the same call for the same
reason: a ROS node has no business importing the gate's checker, and a package
that can only validate itself when the repository is laid out around it cannot
be installed. `test/test_run_record.py` closes the gap by running the real
`scripts/validate_record.py` over the bytes this module actually wrote, so the
two cannot drift without a test failing.

Nothing here imports rclpy, and nothing here reads a clock. Every time in the
record arrives as an argument. That is what lets the contract test run on a
clean checkout with nothing built.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# The two published names. scripts/gate.sh reads both and
# `uavx_invalidate_latest` deletes both, because round 6 finding 6 was a run
# certified against the previous scenario's graph.
LATEST_RECORD = "latest.jsonl"
LATEST_GRAPH = "latest-graph.json"

# architecture.md section 1b: "The run id matches [A-Za-z0-9_]+ and is unique
# within the runs directory." The schema adds minLength 8. A dash would break
# the grammar, so the stamp below uses none.
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")
RUN_ID_MIN = 8

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")

# jsonschema_mini enforces RFC 3339 here, and `bind_to_run` in
# scripts/seam_graph.py compares captured_at against this window as a plain
# string. Both halves therefore have to agree on one spelling, and this is
# graph_snapshot.STAMP_FORMAT.
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")

COMPLETIONS = ("complete", "crashed", "timeout", "killed")
EVENT_TYPES = ("kill", "comms_blackout", "gps_degrade")

# The only value the schema allows, and a claim about how every `_s` number in
# the record was produced. The runner passes what it actually used so a
# fallback to wall time cannot quietly keep the label.
CLOCK_SOURCE = "ros_sim_time"

# scenarios/run-record.schema.json, `required`.
REQUIRED_FIELDS = (
    "run_id", "scenario_path", "scenario_sha256", "seed", "commit_sha",
    "started_at", "ended_at", "completion", "vehicle_ids_observed",
    "pose_sample_count", "versions", "metrics", "app_packets_sent_by_node",
    "app_packets_delivered_by_node", "injected_events",
    "requested_duration_s", "elapsed_sim_s", "clock_source",
    "source_tree_sha256", "resources",
)

# The schema's `versions.required`, which is also every ENFORCED key in
# stage-1/setup/versions.lock. Round 4 finding 8: this block named four of them
# and a run could be attributed to a stack it did not come off. The lock's
# `observed_` keys are context for a human and are deliberately not here.
REQUIRED_VERSION_KEYS = (
    "ubuntu_version", "ros_distro", "ros_desktop_version", "ros_core_version",
    "ros_rclpy_version", "ros_rclcpp_version", "ros_rmw_fastrtps_cpp_version",
    "gazebo_package", "gazebo_version", "px4_sha", "xrce_agent_sha",
    "px4_msgs_sha", "px4_ros_com_sha",
)

# uavx_eval.check rejects a complete run shorter than this fraction of what the
# scenario asked for, and scripts/validate_record.py already enforces it. A
# record that would fail there must fail here first, or the runner publishes a
# file it knows the next gate refuses.
MIN_DURATION_FRACTION = 0.95

# Chunk 2.4. The four coverage fields a surveying run carries, all or none.
# scenarios/run-record.schema.json types each of them; what the schema cannot
# say is that they agree with each other, and validate_record does.
COVERAGE_FIELDS = ("coverage_fraction", "coverage_source",
                   "coverage_cells_total", "coverage_cells_seen")

TEMP_PREFIX = ".uavx-record-"
TEMP_SUFFIX = ".tmp"


class RecordError(ValueError):
    """The record does not satisfy the provenance contract.

    Raised instead of returning a thin record. Every caller of this module is
    about to publish evidence, and a record that is wrong is worse than one
    that is missing: the missing one fails a gate loudly.
    """


# ------------------------------------------------------------- measurements
def sha256_of_file(path) -> str:
    """The hash a record carries for a file it read."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_stamp(moment=None) -> str:
    """Wall time, for started_at and ended_at and for nothing else.

    Every other time in the record is simulated time. Mixing the two would make
    a delivery appear billions of seconds late and would make a seeded replay
    depend on when it was launched.
    """
    moment = datetime.now(timezone.utc) if moment is None else moment
    return moment.strftime(STAMP_FORMAT)


def _git(repo, *args) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecordError(f"git {' '.join(args)} could not be run: {exc}") from exc
    if done.returncode != 0:
        raise RecordError(
            f"git {' '.join(args)} exited {done.returncode}: "
            f"{done.stderr.strip()[:200]}")
    return done.stdout.strip()


def commit_sha(repo) -> str:
    """Repo HEAD at launch, read from git rather than remembered."""
    value = _git(repo, "rev-parse", "HEAD")
    if not SHA1_RE.fullmatch(value):
        raise RecordError(
            f"git rev-parse HEAD returned {value!r}, which is not a commit id. "
            f"The record cannot name the code it ran.")
    return value


def source_tree_sha256(repo) -> str:
    """The hash scripts/source_tree_hash.py computes, from that script.

    Never reimplemented here. The final package check compares this against
    submission/source-manifest.json, so the runner and the checker have to
    agree byte for byte, and two implementations of one hash is how they stop
    agreeing.
    """
    script = Path(repo) / "scripts" / "source_tree_hash.py"
    if not script.is_file():
        raise RecordError(
            f"no {script}. source_tree_sha256 is what ties a run to the code "
            f"that produced it and there is nothing to compute it with.")
    try:
        done = subprocess.run(["python3", str(script)], capture_output=True,
                              text=True, timeout=300, cwd=str(repo))
    except (OSError, subprocess.SubprocessError) as exc:
        raise RecordError(f"source_tree_hash.py could not be run: {exc}") from exc
    if done.returncode != 0:
        raise RecordError(
            f"source_tree_hash.py exited {done.returncode}: "
            f"{done.stderr.strip()[:200]}")
    value = done.stdout.strip().splitlines()[-1].strip() if done.stdout.strip() else ""
    if not SHA256_RE.fullmatch(value):
        raise RecordError(
            f"source_tree_hash.py printed {value!r}, which is not a sha256.")
    return value


def read_versions(lock_path) -> dict:
    """Every ENFORCED key from stage-1/setup/versions.lock, read at run time.

    Parsed the way stage-1/setup/verify.sh reads it: `key=value` lines, and a
    key beginning with `observed_` is context for a human rather than a pin.
    verify.sh has already compared each of these against the machine during
    preflight, so what goes in the record is the set of pins this run was
    allowed to start under.
    """
    path = Path(lock_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RecordError(
            f"cannot read {path}: {exc}. The versions block cannot be filled "
            f"in from memory.") from exc

    versions = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or key.startswith("observed_"):
            continue
        versions[key] = value

    missing = [k for k in REQUIRED_VERSION_KEYS
               if not versions.get(k)]
    if missing:
        raise RecordError(
            f"{path} carries no value for {', '.join(missing)}. The schema "
            f"requires every enforced pin, because a run labelled with an "
            f"incomplete version block is attributed to a stack it did not "
            f"come off.")
    return versions


# -------------------------------------------------------------- the run id
def mint_run_id(scenario_name, runs_dir, moment=None) -> str:
    """A fresh id for this run, unique in `runs_dir`.

    Minted before the run starts, never after it. scripts/rehearse_recording.sh
    burns the id into every frame of its capture and then requires the record to
    carry it back, so an id invented at the end could not be in the picture.
    """
    stamp = (datetime.now(timezone.utc) if moment is None
             else moment).strftime("%Y%m%dT%H%M%SZ")
    stem = re.sub(r"[^A-Za-z0-9_]", "_", str(scenario_name)) or "run"
    candidate = f"{stem}_{stamp}"
    directory = Path(runs_dir)
    suffix = 0
    # Two runs inside one second is the only way here, and appending is
    # honest where overwriting a previous run's record would not be.
    while (directory / f"{candidate}.jsonl").exists():
        suffix += 1
        candidate = f"{stem}_{stamp}_{suffix}"
        if suffix > 999:
            raise RecordError(
                f"cannot mint a run id in {directory}: a thousand ids for "
                f"{stem} at {stamp} are already taken.")
    check_run_id(candidate)
    return candidate


def check_run_id(run_id) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise RecordError(
            f"run id {run_id!r} is not [A-Za-z0-9_]+. architecture.md section "
            f"1b freezes the grammar, and the id is also a filename.")
    if len(run_id) < RUN_ID_MIN:
        raise RecordError(
            f"run id {run_id!r} is shorter than {RUN_ID_MIN} characters, which "
            f"the record schema refuses.")
    return run_id


def record_path(runs_dir, run_id) -> Path:
    """<runs-dir>/<run_id>.jsonl, the file the record is written to."""
    return Path(runs_dir) / f"{check_run_id(run_id)}.jsonl"


def graph_path(runs_dir, run_id) -> Path:
    """The per run graph snapshot, published as latest-graph.json on success."""
    return Path(runs_dir) / f"{check_run_id(run_id)}-graph.json"


# ------------------------------------------------------------- validation
def _is_number(value) -> bool:
    """A real JSON number. `bool` is a subclass of `int`, so it is excluded.

    Chunk 1.4 found the same trap in the --require grammar: without this,
    `injected_event_count` carrying False would validate as the number zero.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text_problem(record, key, pattern=None, minimum_length=0):
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        return f"{key} is {value!r}, and a blank provenance field is worse than a missing one"
    if len(value) < minimum_length:
        return f"{key} is {value!r}, shorter than {minimum_length} characters"
    if pattern is not None and not pattern.fullmatch(value):
        return f"{key} is {value!r}, which does not match {pattern.pattern}"
    return None


def _coverage_problems(record) -> list:
    """All four coverage fields, agreeing with each other, or none of them.

    A record with a fraction and no counts cannot be checked against
    anything, and one whose fraction is not its counts divided is a record
    somebody edited. The gate reads only the fraction and the label, which is
    exactly why the writer refuses the rest being wrong.
    """
    present = [key for key in COVERAGE_FIELDS if key in record]
    if not present:
        return []
    if len(present) != len(COVERAGE_FIELDS):
        missing = [key for key in COVERAGE_FIELDS if key not in record]
        return [f"the record carries {', '.join(present)} without "
                f"{', '.join(missing)}; the coverage block is all four "
                f"fields or none"]
    problems = []
    fraction = record.get("coverage_fraction")
    source = record.get("coverage_source")
    total = record.get("coverage_cells_total")
    seen = record.get("coverage_cells_seen")
    if not _is_number(fraction) or not 0.0 <= fraction <= 1.0:
        problems.append(f"coverage_fraction is {fraction!r}, not a number in "
                        f"[0, 1]")
    if not isinstance(source, str) or not source.strip():
        problems.append(f"coverage_source is {source!r}; the label says "
                        f"whether the figure came off flown poses")
    if not _is_integer(total) or total < 1:
        problems.append(f"coverage_cells_total is {total!r}, not a positive "
                        f"count")
    if not _is_integer(seen) or seen < 0:
        problems.append(f"coverage_cells_seen is {seen!r}, not a count")
    if not problems:
        if seen > total:
            problems.append(f"coverage_cells_seen {seen} exceeds the "
                            f"{total} cells in the box")
        elif abs(fraction - seen / total) > 1e-9:
            problems.append(f"coverage_fraction {fraction} is not "
                            f"{seen} over {total}")
    return problems


def validate_record(record) -> dict:
    """Every rule scenarios/run-record.schema.json states, plus the semantic
    ones scripts/validate_record.py adds. Raises RecordError listing all of
    them, because fixing one field at a time across a three minute run is how
    an afternoon goes.
    """
    if not isinstance(record, dict):
        raise RecordError(
            f"a run record is a JSON object, got {type(record).__name__}")

    problems = []
    for key in REQUIRED_FIELDS:
        if key not in record:
            problems.append(f"the record is missing {key}")
    if problems:
        raise RecordError("; ".join(problems))

    for key, pattern, least in (("run_id", RUN_ID_RE, RUN_ID_MIN),
                                ("scenario_path", None, 1),
                                ("scenario_sha256", SHA256_RE, 64),
                                ("commit_sha", SHA1_RE, 40),
                                ("source_tree_sha256", SHA256_RE, 64),
                                ("started_at", STAMP_RE, 20),
                                ("ended_at", STAMP_RE, 20)):
        problem = _text_problem(record, key, pattern, least)
        if problem:
            problems.append(problem)

    if "graph_snapshot_sha256" in record:
        problem = _text_problem(record, "graph_snapshot_sha256", SHA256_RE, 64)
        if problem:
            problems.append(problem)

    if not _is_integer(record.get("seed")):
        problems.append(
            f"seed is {record.get('seed')!r} and must be an integer. The link "
            f"model is seeded from it and a run nobody can replay is not evidence")

    if record.get("completion") not in COMPLETIONS:
        problems.append(f"completion is {record.get('completion')!r}, not one "
                        f"of {', '.join(COMPLETIONS)}")

    if record.get("clock_source") != CLOCK_SOURCE:
        problems.append(
            f"clock_source is {record.get('clock_source')!r}. Every `_s` value "
            f"in this record claims to be ROS simulated time, and the label is "
            f"the claim")

    vehicles = record.get("vehicle_ids_observed")
    if not isinstance(vehicles, list) or len(vehicles) < 2:
        problems.append(
            f"vehicle_ids_observed is {vehicles!r}; a run that saw fewer than "
            f"two vehicles is not the run any gate asked for")
    elif any(not isinstance(v, str) or not v for v in vehicles):
        problems.append("vehicle_ids_observed holds a non-string id")
    elif len(set(vehicles)) != len(vehicles):
        problems.append("vehicle_ids_observed contains duplicate ids")

    count = record.get("pose_sample_count")
    if not _is_integer(count) or count < 1:
        problems.append(
            f"pose_sample_count is {count!r}. Zero means nothing was ever "
            f"observed, which is how 'no samples' masquerades as 'no violations'")

    problems += _version_problems(record.get("versions"))
    if not isinstance(record.get("metrics"), dict):
        problems.append(f"metrics is {record.get('metrics')!r}, not an object")

    for key in ("app_packets_sent_by_node", "app_packets_delivered_by_node"):
        block = record.get(key)
        if not isinstance(block, dict):
            problems.append(f"{key} is {block!r}, not an object")
            continue
        for node, value in block.items():
            if not _is_integer(value) or value < 0:
                problems.append(f"{key}[{node!r}] is {value!r}, not a count")

    requested = record.get("requested_duration_s")
    elapsed = record.get("elapsed_sim_s")
    if not _is_number(requested) or requested < 1:
        problems.append(
            f"requested_duration_s is {requested!r}. Without it a 20 second run "
            f"can claim completion instead of running the frozen duration")
    if not _is_number(elapsed) or elapsed < 1:
        problems.append(f"elapsed_sim_s is {elapsed!r}")
    if (_is_number(requested) and _is_number(elapsed)
            and record.get("completion") == "complete"
            and elapsed < MIN_DURATION_FRACTION * requested):
        problems.append(
            f"a complete run covered {elapsed}s of the requested {requested}s, "
            f"below the accepted {int(MIN_DURATION_FRACTION * 100)} percent")

    problems += _event_problems(record, requested, elapsed)
    problems += _resource_problems(record.get("resources"))

    observed = record.get("injected_event_observed")
    if observed is not None and not isinstance(observed, bool):
        # Chunk 1.7. The field was untyped until this chunk, so a record could
        # carry the string "true" and no schema pass would notice, while the
        # --require grammar compares a flag only against a flag and fails.
        problems.append(
            f"injected_event_observed is {observed!r}, which is not a JSON "
            f"boolean. The gate compares a flag against a flag")
    event_count = record.get("injected_event_count")
    if event_count is not None and (not _is_integer(event_count) or event_count < 0):
        problems.append(f"injected_event_count is {event_count!r}, not a count")

    started, ended = record.get("started_at"), record.get("ended_at")
    if isinstance(started, str) and isinstance(ended, str) and ended < started:
        problems.append("ended_at is earlier than started_at")

    problems += _coverage_problems(record)

    if problems:
        raise RecordError("; ".join(problems))
    return record


def _version_problems(versions) -> list:
    if not isinstance(versions, dict):
        return [f"versions is {versions!r}, not an object"]
    problems = []
    for key in REQUIRED_VERSION_KEYS:
        value = versions.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(
                f"versions.{key} is {value!r}. Every enforced pin in "
                f"versions.lock goes in, or the run is attributed to a stack "
                f"nobody can identify")
    return problems


def _event_problems(record, requested, elapsed) -> list:
    events = record.get("injected_events")
    if not isinstance(events, list):
        return [f"injected_events is {events!r}, not a list"]

    problems = []
    for index, event in enumerate(events):
        where = f"injected_events[{index}]"
        if not isinstance(event, dict):
            problems.append(f"{where} is {event!r}, not an object")
            continue
        for key in ("type", "target", "requested_t", "observed_t"):
            if key not in event:
                problems.append(f"{where} has no {key}")
        if event.get("type") not in EVENT_TYPES:
            problems.append(f"{where}.type is {event.get('type')!r}, not one of "
                            f"{', '.join(EVENT_TYPES)}")
        if not isinstance(event.get("target"), str) or not event.get("target"):
            problems.append(f"{where}.target is {event.get('target')!r}")

        asked = event.get("requested_t")
        if not _is_number(asked) or asked < 0:
            problems.append(f"{where}.requested_t is {asked!r}")
        elif _is_number(requested) and asked >= requested:
            problems.append(
                f"{where}.requested_t is {asked}, outside the {requested}s the "
                f"scenario asked for")

        seen = event.get("observed_t")
        if seen is None:
            # Null is the honest answer for an effect nobody saw, and it is
            # what keeps the event out of injected_event_count.
            continue
        if not _is_number(seen) or seen < 0:
            problems.append(f"{where}.observed_t is {seen!r}")
        elif _is_number(elapsed) and seen > elapsed:
            problems.append(
                f"{where}.observed_t is {seen}, after the {elapsed}s that "
                f"elapsed")
    return problems


def _resource_problems(resources) -> list:
    if not isinstance(resources, dict):
        return [f"resources is {resources!r}, not an object"]
    problems = []
    for key in ("peak_rss_mib", "swap_used_mib"):
        value = resources.get(key)
        if not _is_number(value) or value < 0:
            problems.append(f"resources.{key} is {value!r}")
    samples = resources.get("samples")
    if not _is_integer(samples) or samples < 0:
        problems.append(f"resources.samples is {samples!r}")
    if "peak_at_s" in resources:
        value = resources["peak_at_s"]
        if not _is_number(value) or value < 0:
            problems.append(f"resources.peak_at_s is {value!r}")
    return problems


# ---------------------------------------------------------------- building
def build_record(*, run_id, scenario_path, scenario_sha256, seed, commit_sha,
                 started_at, ended_at, completion, vehicle_ids_observed,
                 pose_sample_count, versions, metrics,
                 app_packets_sent_by_node, app_packets_delivered_by_node,
                 injected_events, requested_duration_s, elapsed_sim_s,
                 clock_source, source_tree_sha256, resources,
                 injected_event_observed, injected_event_count,
                 graph_snapshot_sha256=None, coverage=None):
    """Assemble one record and validate it before anybody can write it.

    Every argument is keyword only and every one of them is required. A
    positional slot invites passing the scenario hash where the source hash
    goes, and both are 64 hex characters, so nothing downstream would notice.

    `coverage` is the one optional block with content: the four fields
    uavx_sim.survey.coverage_from_payload returns for a run that surveyed,
    or None for one that did not. They land at the top level of the record
    because that is where the gate reads them.
    """
    record = {
        "run_id": run_id,
        "scenario_path": scenario_path,
        "scenario_sha256": scenario_sha256,
        "seed": seed,
        "commit_sha": commit_sha,
        "started_at": started_at,
        "ended_at": ended_at,
        "completion": completion,
        "vehicle_ids_observed": list(vehicle_ids_observed),
        "pose_sample_count": pose_sample_count,
        "versions": dict(versions),
        "metrics": dict(metrics),
        "app_packets_sent_by_node": dict(app_packets_sent_by_node),
        "app_packets_delivered_by_node": dict(app_packets_delivered_by_node),
        "injected_events": [dict(event) for event in injected_events],
        "requested_duration_s": requested_duration_s,
        "elapsed_sim_s": elapsed_sim_s,
        "clock_source": clock_source,
        "source_tree_sha256": source_tree_sha256,
        "resources": dict(resources),
        "injected_event_observed": injected_event_observed,
        "injected_event_count": injected_event_count,
    }
    if graph_snapshot_sha256 is not None:
        record["graph_snapshot_sha256"] = graph_snapshot_sha256
    if coverage is not None:
        if not isinstance(coverage, dict):
            raise RecordError(f"coverage is {coverage!r}, not the four field "
                              f"block the survey module produces")
        # Only the keys given. Filling the missing ones with None would
        # turn 'the collector reported three of four fields' into 'the
        # collector reported a null cell count', and the second reads
        # like a measurement.
        for key in COVERAGE_FIELDS:
            if key in coverage:
                record[key] = coverage[key]
    return validate_record(record)


# ----------------------------------------------------------------- writing
def serialise(record) -> bytes:
    """The exact bytes of the record: one JSON object, one line, UTF-8.

    Sorted keys and fixed separators so the same run written twice is byte
    identical, which is what standing rule 4 means by replaying exactly. ASCII
    escaped and written binary below, so no platform gets to add a carriage
    return to a file the gate hashes.
    """
    text = json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    return (text + "\n").encode("utf-8")


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _atomic_write(path, payload) -> Path:
    """Write `payload` at `path` through a temporary file and a rename.

    The same shape as graph_snapshot.write_snapshot, deliberately. Two atomic
    writers in one package would be two sets of behaviour under a crash.
    """
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    handle_fd, temp = tempfile.mkstemp(dir=str(directory), prefix=TEMP_PREFIX,
                                       suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            # fsync before the rename, or a crash can leave the directory entry
            # pointing at a file whose contents never reached the disk.
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        _discard(temp)
        raise
    return path


def write_record(path, record) -> Path:
    """Validate, then publish the record at `path` by atomic rename.

    Validation and serialisation both happen before the temporary file exists,
    so a record that fails leaves the directory exactly as it was.
    """
    validate_record(record)
    return _atomic_write(path, serialise(record))


def publish_latest(runs_dir, record) -> Path:
    """Publish `latest.jsonl`, and refuse for anything but a complete run.

    Called last, after every process has exited. Round 2 finding 5: nothing
    tied `latest.jsonl` to the scenario that had just run, so a stale green
    file survived a crashed simulation and a metrics writer that never saw a
    vehicle could satisfy a gate.
    """
    validate_record(record)
    if record["completion"] != "complete":
        raise RecordError(
            f"this run ended {record['completion']!r}, so it publishes no "
            f"latest.jsonl. Only a complete run may leave a file a later gate "
            f"will read as current.")
    return _atomic_write(Path(runs_dir) / LATEST_RECORD, serialise(record))


def latest_paths(runs_dir):
    """Both files `uavx_invalidate_latest` deletes, in one place."""
    directory = Path(runs_dir)
    return directory / LATEST_RECORD, directory / LATEST_GRAPH


def invalidate_latest(runs_dir):
    """Clear both published files before a run starts.

    Both, never only the record. Round 6 finding 6: a runner that wrote a new
    record and missed its graph capture left the previous scenario's
    latest-graph.json in place, and the seam pass certified the new run against
    the old graph.
    """
    for path in latest_paths(runs_dir):
        _discard(path)
