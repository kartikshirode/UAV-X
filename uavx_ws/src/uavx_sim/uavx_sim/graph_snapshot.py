"""Capture the ROS graph during a scenario and publish it for the seam check.

scripts/seam_graph.py is the consumer. Its `read_snapshot()` is the contract
this module is written against, and it refuses far more than it accepts, for
reasons the repo learned one round at a time:

  Round 4 finding 4   message types were thrown away at parse time, so the rule
                      banning SwarmPacket outside tx and rx could not be
                      enforced at all. Every endpoint carries a type now.
  Round 5 finding 7   a snapshot has to say where it came from, so `_meta`
                      carries captured_at, scenario, run_id and
                      source_tree_sha256, and every node names the `ros2 node
                      info` call that produced it.
  Round 6 finding 6   those four provenance strings were read, confirmed
                      non-empty and then compared with nothing, so a graph left
                      behind by the previous scenario satisfied the check.
                      `bind_to_run` ties the file to one run record by id,
                      source hash, capture time and its own sha256.

The rule here follows from that. If the capture came back incomplete, nothing
gets written. A missing latest-graph.json fails the gate loudly. A thin one
passes it quietly, and that is the failure this repo keeps rediscovering.

The module splits in two on purpose. Everything above `capture_nodes()` is pure
and needs no ROS, which is what lets the contract test run on a clean checkout
with nothing built. rclpy and subprocess are touched inside the capture
functions only, never at import time.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

# Mirrors seam_graph.KINDS. That module is the source of truth and lives in
# scripts/, off the package path, so the four names are repeated rather than
# imported: a ROS node has no business importing the gate's checker.
KINDS = ("publishers", "subscribers", "services", "actions")

META_KEY = "_meta"

# seam_graph.MANIFEST_META, and seam_manifests.json snapshot_required_meta.
META_FIELDS = ("captured_at", "scenario", "run_id", "source_tree_sha256")

# bind_to_run compares captured_at against the run record's started_at and
# ended_at as plain strings, so the capture and the record have to agree on a
# format before they can agree on an ordering. This is the one the runner and
# scripts/rehearse_recording.sh use, `date -u +%Y-%m-%dT%H:%M:%SZ`.
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STAMP_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

TEMP_PREFIX = ".uavx-graph-"
TEMP_SUFFIX = ".tmp"


class IncompleteSnapshot(ValueError):
    """The graph is not complete enough to be evidence of anything."""


class CaptureFailed(RuntimeError):
    """The live graph could not be read at all."""


# --------------------------------------------------------------- validation
def _required_text(value, what):
    if not isinstance(value, str) or not value.strip():
        raise IncompleteSnapshot(
            f"{what} is {value!r}. seam_graph.read_snapshot rejects a snapshot "
            f"whose provenance is blank, and it is right to.")
    return value.strip()


def _check_endpoint(node, kind, index, endpoint):
    """One publisher, subscriber, service or action of one node.

    Both halves matter. Rule 6 in stage-1/architecture.md section 1 is enforced
    from the message type, so an endpoint that names a topic and no type is a
    row the seam pass cannot check. It reads as evidence and carries none.
    """
    where = f"{node} {kind}[{index}]"
    if not isinstance(endpoint, dict):
        raise IncompleteSnapshot(
            f"{where} is {endpoint!r}, not an endpoint object. Every endpoint "
            f"is a dict with a topic and a type.")
    for field in ("topic", "type"):
        if field not in endpoint:
            raise IncompleteSnapshot(
                f"{where} has no {field}. An endpoint without a message type "
                f"cannot be checked against the seam rules, and one without a "
                f"topic names nothing.")
        value = endpoint[field]
        if not isinstance(value, str) or not value.strip():
            raise IncompleteSnapshot(
                f"{where} has a blank {field} ({value!r}). Blank is worse than "
                f"absent here, because it looks filled in.")
    return {"topic": endpoint["topic"].strip(), "type": endpoint["type"].strip()}


def _check_command(node, command, label):
    if not isinstance(command, dict):
        raise IncompleteSnapshot(
            f"_meta.commands has no {label} result for {node}. A node in the "
            f"graph with nothing saying how it was read is a node somebody "
            f"typed.")
    if "returncode" not in command:
        raise IncompleteSnapshot(
            f"_meta.commands[{node!r}] records no returncode. A discovery "
            f"failure becomes an empty endpoint list, which reads exactly like "
            f"a clean node.")
    if command["returncode"] != 0:
        raise IncompleteSnapshot(
            f"`{label} {node}` exited {command['returncode']}. Publishing that "
            f"capture would publish a hole.")
    return dict(command)


def validate_snapshot(document):
    """Refuse anything seam_graph.read_snapshot would refuse, and a little more.

    Called by write_snapshot as well as by build_snapshot, so a hand-assembled
    document cannot skip the gate by going straight to the writer.
    """
    if not isinstance(document, dict):
        raise IncompleteSnapshot(f"a snapshot is a JSON object, got {type(document).__name__}")

    meta = document.get(META_KEY)
    if not isinstance(meta, dict):
        raise IncompleteSnapshot(
            "the snapshot has no _meta block, so there is nothing tying it to "
            "a run.")
    for field in META_FIELDS:
        _required_text(meta.get(field), f"_meta.{field}")
    if not STAMP_SHAPE.match(meta["captured_at"]):
        raise IncompleteSnapshot(
            f"_meta.captured_at is {meta['captured_at']!r}, which is not an "
            f"ISO 8601 timestamp. bind_to_run compares it with the run window "
            f"as a string, so a stamp in another shape sorts anywhere.")

    commands = meta.get("commands")
    if not isinstance(commands, dict):
        raise IncompleteSnapshot(
            "_meta.commands is missing. Without it an empty capture and a "
            "clean swarm write the same file.")
    _check_command("node_list", commands.get("node_list"), "ros2 node list")

    nodes = [k for k in document if k != META_KEY]
    if not nodes:
        raise IncompleteSnapshot(
            "the capture found no nodes. A scenario with nothing running is "
            "not a scenario that ran.")

    for node in sorted(nodes):
        if not isinstance(node, str) or not node.startswith("/"):
            raise IncompleteSnapshot(
                f"{node!r} is not a ROS node name. Without the leading slash it "
                f"matches no manifest row and the seam pass reports it as an "
                f"unaccounted process.")
        # The command result first, so a node info that exited non-zero says so
        # instead of being reported as an unexplained hole.
        _check_command(node, commands.get(node), "ros2 node info")
        detail = document[node]
        if not isinstance(detail, dict) or not any(k in detail for k in KINDS):
            raise IncompleteSnapshot(
                f"{node} is in the graph with no endpoint detail behind it. "
                f"That is a hole, not a quiet node.")
        held = 0
        for kind in KINDS:
            rows = detail.get(kind, [])
            if not isinstance(rows, list):
                raise IncompleteSnapshot(
                    f"{node} {kind} is {rows!r}, not a list of endpoints.")
            for index, endpoint in enumerate(rows):
                _check_endpoint(node, kind, index, endpoint)
            held += len(rows)
        if held == 0:
            # Round 5 finding 7: thirteen nodes with every endpoint list empty
            # passed the seam check clean. Any live node holds /rosout and its
            # parameter endpoints at the very least, so zero means the info
            # call came back unparsed.
            raise IncompleteSnapshot(
                f"{node} holds no endpoints in any of {', '.join(KINDS)}. A "
                f"list of processes is not a graph.")

    return document


# ------------------------------------------------------------------ building
def build_snapshot(nodes, run_id, scenario, captured_at, *,
                   source_tree_sha256, commands):
    """Turn raw per node endpoint data into the document seam_graph reads.

    `nodes` maps a node name to a dict of the four endpoint kinds. `commands`
    maps "node_list" and every node name to the result of the call that read
    it, each with a returncode. Both extras are keyword only because they are
    not optional and a positional slot invites passing the wrong one.

    Raises IncompleteSnapshot rather than returning a thin document. That is
    the whole job.
    """
    run_id = _required_text(run_id, "run_id")
    scenario = _required_text(scenario, "scenario")
    captured_at = _required_text(captured_at, "captured_at")
    source_tree_sha256 = _required_text(source_tree_sha256, "source_tree_sha256")

    if not isinstance(nodes, dict) or not nodes:
        raise IncompleteSnapshot(
            "the capture returned no nodes, so there is no graph to publish.")
    if not isinstance(commands, dict):
        raise IncompleteSnapshot(
            "no command results were recorded, so nothing says how this graph "
            "was read.")

    recorded = {"node_list": _check_command("node_list",
                                            commands.get("node_list"),
                                            "ros2 node list")}
    document = {META_KEY: {
        "captured_at": captured_at,
        "scenario": scenario,
        "run_id": run_id,
        "source_tree_sha256": source_tree_sha256,
        "commands": recorded,
    }}

    # Sorted so a failure names the same node whichever order discovery
    # happened to return, which matters when the message is the only thing a
    # gate log carries.
    for node in sorted(nodes):
        if node == META_KEY:
            raise IncompleteSnapshot(
                f"a node called {META_KEY!r} would overwrite the provenance "
                f"block.")
        # The command result first, so a node info that exited non-zero says so
        # instead of being reported as an unexplained hole.
        recorded[node] = _check_command(node, commands.get(node),
                                        "ros2 node info")
        detail = nodes[node]
        if not isinstance(detail, dict) or not any(k in detail for k in KINDS):
            raise IncompleteSnapshot(
                f"{node} was listed by `ros2 node list` and has no endpoint "
                f"detail behind it. That is a hole, not a quiet node.")
        entry = {}
        for kind in KINDS:
            rows = detail.get(kind) or []
            if not isinstance(rows, (list, tuple)):
                raise IncompleteSnapshot(
                    f"{node} {kind} is {rows!r}, not a list of endpoints.")
            entry[kind] = [_check_endpoint(node, kind, i, e)
                           for i, e in enumerate(rows)]
        document[node] = entry

    return validate_snapshot(document)


# ------------------------------------------------------------------- writing
def serialise(document):
    """The exact bytes of the snapshot.

    The run record carries graph_snapshot_sha256 and both seam_graph and the
    submission checker recompute it from the file, so two captures of the same
    graph have to produce the same bytes. Sorted keys, fixed separators, ASCII
    escaping and a single trailing LF, written binary so no platform gets to
    add a carriage return.
    """
    text = json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    return (text + "\n").encode("utf-8")


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def write_snapshot(path, document):
    """Publish the snapshot by atomic rename. Never a partial file at `path`.

    Serialisation and validation both happen before the temporary file exists,
    so a bad document leaves the directory exactly as it was. The rename is the
    publish: a gate that reads latest-graph.json sees the previous capture or
    the new one, never half of either.
    """
    path = Path(path)
    validate_snapshot(document)
    payload = serialise(document)

    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(directory), prefix=TEMP_PREFIX,
                                suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
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


def sha256_of(path):
    """What the runner writes into the record's graph_snapshot_sha256."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def utc_stamp():
    """A captured_at the run record's window can be compared against."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime(STAMP_FORMAT)


# ------------------------------------------------------------------ capturing
def parse_node_info(text):
    """Parse `ros2 node info` the way seam_graph.read_live does.

    Deliberately the same parser. If the capture and the live pass disagree
    about what an endpoint is, the two halves of the seam check stop being
    comparable and the snapshot pass proves nothing about the live one.
    """
    current = None
    detail = {kind: [] for kind in KINDS}
    for line in text.splitlines():
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("subscribers:"):
            current = "subscribers"
            continue
        if low.startswith("publishers:"):
            current = "publishers"
            continue
        if low.startswith(("service servers:", "service clients:")):
            current = "services"
            continue
        if low.startswith(("action servers:", "action clients:")):
            current = "actions"
            continue
        if current and stripped.startswith("/"):
            topic, separator, kind = stripped.rpartition(":")
            if not separator:
                # `ros2 node info` prints "<topic>: <type>". On a row with no
                # colon, rpartition returns the whole line as the third field,
                # so seam_graph.read_live ends up storing the topic as the
                # message type and its own non-blank check passes. Record the
                # blank instead and let the validator refuse the capture. This
                # is the one place the parser is stricter than read_live, and
                # it is stricter in the direction that refuses.
                topic, kind = stripped, ""
            detail[current].append({"topic": topic.strip(),
                                    "type": kind.strip()})
    return detail


def capture_nodes(timeout=60):
    """Read the live graph. Returns (nodes, commands).

    Nothing is judged here beyond whether the commands could be run at all.
    Return codes go into `commands` as they came back and build_snapshot
    refuses on them, so there is one place that decides what counts as
    complete.
    """
    import subprocess

    def run(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CaptureFailed(f"{' '.join(argv)} could not be run: {exc}") from exc

    listing = run(["ros2", "node", "list"])
    commands = {"node_list": {"cmd": "ros2 node list",
                              "returncode": listing.returncode,
                              "stderr": listing.stderr.strip()[:200]}}
    if listing.returncode != 0:
        raise CaptureFailed(
            f"ros2 node list exited {listing.returncode}: "
            f"{listing.stderr.strip()[:200]}")

    names = listing.stdout.split()
    if not names:
        raise CaptureFailed(
            "no ROS nodes are running, so there is no graph to capture.")

    nodes = {}
    for name in names:
        info = run(["ros2", "node", "info", name])
        commands[name] = {"cmd": f"ros2 node info {name}",
                          "returncode": info.returncode,
                          "stderr": info.stderr.strip()[:200]}
        if info.returncode != 0:
            nodes[name] = {}
            continue
        if not any(header in info.stdout
                   for header in ("Publishers:", "Subscribers:")):
            # Unparseable output and a node with no endpoints look identical
            # once parsed, so mark the command failed rather than record an
            # empty node. build_snapshot then refuses the whole capture.
            commands[name]["returncode"] = 1
            commands[name]["stderr"] = "ros2 node info returned nothing parseable"
            nodes[name] = {}
            continue
        nodes[name] = parse_node_info(info.stdout)
    return nodes, commands


def capture_snapshot(run_id, scenario, *, source_tree_sha256, captured_at=None,
                     timeout=60):
    """Read the live graph and build the document, or raise."""
    nodes, commands = capture_nodes(timeout=timeout)
    return build_snapshot(nodes, run_id, scenario,
                          captured_at or utc_stamp(),
                          source_tree_sha256=source_tree_sha256,
                          commands=commands)


def capture_to(path, run_id, scenario, *, source_tree_sha256,
               captured_at=None, timeout=60):
    """Capture, publish and hand back the digest for the run record."""
    document = capture_snapshot(run_id, scenario,
                                source_tree_sha256=source_tree_sha256,
                                captured_at=captured_at, timeout=timeout)
    write_snapshot(path, document)
    return sha256_of(path)
