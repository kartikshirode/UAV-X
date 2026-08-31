"""Chunk 1.5: does the graph capture reject holes and publish atomically.

Two claims are on trial here and they need different kinds of proof.

The first is that a complete capture produces a file scripts/seam_graph.py
accepts. Asserting that against my own idea of the format would be worthless,
so the real seam_graph is loaded from scripts/ and its read_snapshot is called
on the bytes the writer actually wrote. There is a second pass through the
command line entry point, because that is how scripts/check_seam.sh reaches it.

The second is that an incomplete capture never reaches the disk. A snapshot
missing message types passes nothing downstream and still looks like evidence,
which is the shape of defect this repo keeps finding, so every hole gets its
own case.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_graph_snapshot.py

Runs on a clean checkout with nothing built and no ROS up. test/conftest.py
puts the package root on sys.path.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

from uavx_sim import graph_snapshot
from uavx_sim.graph_snapshot import (
    IncompleteSnapshot,
    build_snapshot,
    sha256_of,
    validate_snapshot,
    write_snapshot,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                    "..", "..", "..", ".."))
SEAM_GRAPH = os.path.join(REPO, "scripts", "seam_graph.py")

# relay_required is the W3 snapshot pass in seam_manifests.json, and its
# process list is the smallest one: router and mission_executor per vehicle,
# plus the GCS.
SCENARIO = "relay_required"
VEHICLES = ("uav_1", "uav_2", "uav_3", "uav_4")

# Built per id rather than written out. check_seam.sh counts distinct
# /uavx/<id>/ literals per file and one file naming two vehicles is a
# violation, and that rule covers the tests as much as the source.
SEAM_NS = "/uavx"
PACKET = "uavx_msgs/msg/SwarmPacket"
LOG = "rcl_interfaces/msg/Log"
PARAM = "rcl_interfaces/msg/ParameterEvent"
POSE = "px4_msgs/msg/VehicleLocalPosition"
SETPOINT = "px4_msgs/msg/TrajectorySetpoint"

RUN_ID = "w1_graph_fixture"
SOURCE_SHA = "0" * 64
STARTED = "2026-09-20T09:00:00Z"
CAPTURED = "2026-09-20T10:00:00Z"
ENDED = "2026-09-20T11:00:00Z"


def tx(vid):
    return f"{SEAM_NS}/{vid}/tx"


def rx(vid):
    return f"{SEAM_NS}/{vid}/rx"


def ep(topic, kind):
    return {"topic": topic, "type": kind}


def raw_nodes(vehicles=VEHICLES):
    """The per node endpoint data a capture of a healthy W3 swarm returns."""
    nodes = {}
    for vid in vehicles:
        for proc in ("router", "mission_executor"):
            name = f"/{vid}/{proc}"
            pubs = [ep(tx(vid), PACKET), ep("/rosout", LOG)]
            subs = [ep(rx(vid), PACKET),
                    ep(f"/{vid}/fmu/out/vehicle_local_position", POSE),
                    ep("/parameter_events", PARAM)]
            if proc == "mission_executor":
                pubs.append(ep(f"/{vid}/fmu/in/trajectory_setpoint", SETPOINT))
            nodes[name] = {
                "publishers": pubs,
                "subscribers": subs,
                "services": [ep(f"{name}/get_parameters",
                                "rcl_interfaces/srv/GetParameters")],
                "actions": [],
            }
    nodes["/gcs/gcs_node"] = {
        "publishers": [ep(tx("gcs"), PACKET), ep("/rosout", LOG)],
        "subscribers": [ep(rx("gcs"), PACKET), ep("/parameter_events", PARAM)],
        "services": [],
        "actions": [],
    }
    return nodes


def raw_commands(nodes):
    commands = {"node_list": {"cmd": "ros2 node list", "returncode": 0}}
    for name in nodes:
        commands[name] = {"cmd": f"ros2 node info {name}", "returncode": 0}
    return commands


def build(nodes=None, run_id=RUN_ID, scenario=SCENARIO, captured_at=CAPTURED,
          source_tree_sha256=SOURCE_SHA, commands=None):
    nodes = raw_nodes() if nodes is None else nodes
    if commands is None:
        commands = raw_commands(nodes)
    return build_snapshot(nodes, run_id, scenario, captured_at,
                          source_tree_sha256=source_tree_sha256,
                          commands=commands)


def write_run_record(path, document, snapshot):
    """The record bind_to_run checks the snapshot against.

    Round 6 finding 6: a snapshot on its own is four editable strings, so
    seam_graph refuses --snapshot without --expect-run. The window brackets
    CAPTURED, and the digest is read back off the file the writer produced,
    which is also a check that sha256_of agrees with what seam_graph computes.
    """
    meta = document["_meta"]
    record = {
        "run_id": meta["run_id"],
        "scenario_path": f"scenarios/{meta['scenario']}.yaml",
        "source_tree_sha256": meta["source_tree_sha256"],
        "started_at": STARTED,
        "ended_at": ENDED,
        "graph_snapshot_sha256": sha256_of(snapshot),
    }
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle)
    return path


def load_seam_graph():
    assert os.path.isfile(SEAM_GRAPH), f"no seam checker at {SEAM_GRAPH}"
    spec = importlib.util.spec_from_file_location("uavx_seam_graph_under_test",
                                                  SEAM_GRAPH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish(tmp_path):
    """Write a complete snapshot and its run record. Returns both paths."""
    document = build()
    snapshot = tmp_path / "latest-graph.json"
    write_snapshot(snapshot, document)
    record = write_run_record(tmp_path / "latest.jsonl", document, snapshot)
    return snapshot, record


# ------------------------------------------- the consumer accepts the output
def test_seam_graph_read_snapshot_accepts_a_complete_capture(tmp_path, capsys):
    snapshot, record = publish(tmp_path)
    seam = load_seam_graph()

    try:
        graph = seam.read_snapshot(snapshot, SCENARIO, record)
    except SystemExit as exc:
        said = capsys.readouterr()
        pytest.fail(f"seam_graph.read_snapshot rejected the written snapshot "
                    f"(exit {exc.code}): {said.err.strip()}")

    assert set(graph) == set(raw_nodes())
    for node, detail in graph.items():
        assert detail["publishers"], f"{node} came back with no publishers"


def test_seam_graph_command_line_reads_the_written_snapshot(tmp_path):
    """check_seam.sh reaches the checker this way, so the file is tried here too.

    Exit 2 is the one that matters: it means the snapshot itself was refused.
    Exit 1 is expected instead, because seam_manifests.json also requires the
    radio and the observer to be in the graph and both of them hold a ground
    truth topic that check_seam.sh forbids this file from naming. So the
    assertion is that the checker read the graph and then complained about its
    contents, not about its format.
    """
    snapshot, record = publish(tmp_path)
    proc = subprocess.run(
        [sys.executable, SEAM_GRAPH, "--scenario", SCENARIO,
         "--snapshot", str(snapshot), "--expect-run", str(record)],
        capture_output=True, text=True, timeout=120)
    said = proc.stdout + proc.stderr

    assert proc.returncode != 2, f"the snapshot was refused: {said}"
    assert "CANNOT CHECK" not in said, said
    assert "/link_layer is absent" in said, (
        "the checker never got as far as the process list, so the snapshot "
        f"was not read: {said}")


# ----------------------------------------------------------- refused captures
def test_a_node_with_no_endpoint_detail_is_refused():
    nodes = raw_nodes()
    nodes["/uav_1/router"] = {}
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "/uav_1/router" in str(caught.value)


def test_a_node_whose_detail_is_missing_entirely_is_refused():
    nodes = raw_nodes()
    nodes["/uav_2/router"] = None
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "/uav_2/router" in str(caught.value)


def test_a_node_holding_no_endpoints_at_all_is_refused():
    # Round 5 finding 7. Every list empty passed the seam check clean, and a
    # live node always holds /rosout at the very least.
    nodes = raw_nodes()
    nodes["/uav_3/router"] = {kind: [] for kind in graph_snapshot.KINDS}
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "/uav_3/router" in str(caught.value)


def test_an_endpoint_without_a_type_is_refused():
    nodes = raw_nodes()
    del nodes["/uav_1/router"]["publishers"][0]["type"]
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "type" in str(caught.value)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_endpoint_with_a_blank_type_is_refused(blank):
    """Blank is worse than absent: it fills the field and enforces nothing.

    seam_graph.read_snapshot dies on this, so a writer that emits it produces a
    file the gate cannot check and a human reading the JSON cannot see is
    broken.
    """
    nodes = raw_nodes()
    nodes["/uav_2/mission_executor"]["subscribers"][0]["type"] = blank
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "type" in str(caught.value)


def test_an_endpoint_without_a_topic_is_refused():
    nodes = raw_nodes()
    del nodes["/gcs/gcs_node"]["subscribers"][0]["topic"]
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes)
    assert "topic" in str(caught.value)


def test_an_endpoint_with_a_blank_topic_is_refused():
    nodes = raw_nodes()
    nodes["/uav_4/router"]["publishers"][0]["topic"] = "  "
    with pytest.raises(IncompleteSnapshot):
        build(nodes)


def test_a_node_with_no_command_behind_it_is_refused():
    nodes = raw_nodes()
    commands = raw_commands(nodes)
    del commands["/uav_1/mission_executor"]
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes, commands=commands)
    assert "/uav_1/mission_executor" in str(caught.value)


def test_a_failed_node_info_is_refused():
    nodes = raw_nodes()
    commands = raw_commands(nodes)
    commands["/uav_1/router"]["returncode"] = 2
    with pytest.raises(IncompleteSnapshot) as caught:
        build(nodes, commands=commands)
    assert "exited 2" in str(caught.value)


def test_a_capture_that_never_listed_the_nodes_is_refused():
    nodes = raw_nodes()
    commands = raw_commands(nodes)
    commands["node_list"]["returncode"] = 1
    with pytest.raises(IncompleteSnapshot):
        build(nodes, commands=commands)


@pytest.mark.parametrize("field", ["run_id", "scenario", "source_tree_sha256",
                                   "captured_at"])
def test_blank_provenance_is_refused(field):
    with pytest.raises(IncompleteSnapshot):
        build(**{field: "  "})


def test_the_writer_refuses_an_incomplete_document(tmp_path):
    """Defence in depth: a hand-assembled document cannot skip the validator."""
    document = build()
    document["/uav_1/router"]["publishers"][0]["type"] = ""
    destination = tmp_path / "latest-graph.json"
    with pytest.raises(IncompleteSnapshot):
        write_snapshot(destination, document)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------ stable bytes
def test_two_writes_of_the_same_graph_are_byte_identical(tmp_path):
    """The record carries a digest of this file and the checker recomputes it.

    The second document is built from the same nodes discovered in the reverse
    order, because that is what varies between two captures of one swarm.
    """
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    write_snapshot(first, build())
    write_snapshot(second, build(raw_nodes(tuple(reversed(VEHICLES)))))

    assert first.read_bytes() == second.read_bytes()
    assert sha256_of(first) == sha256_of(second)
    assert sha256_of(first) == hashlib.sha256(first.read_bytes()).hexdigest()


def test_the_written_file_is_lf_only_utf8_json(tmp_path):
    path = tmp_path / "latest-graph.json"
    write_snapshot(path, build())
    raw = path.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8"))["_meta"]["scenario"] == SCENARIO


# ---------------------------------------------------------- atomic publish
def test_a_successful_write_leaves_no_temporary_file(tmp_path):
    write_snapshot(tmp_path / "latest-graph.json", build())
    assert [p.name for p in tmp_path.iterdir()] == ["latest-graph.json"]


def test_a_failed_rename_leaves_the_previous_snapshot_intact(tmp_path,
                                                             monkeypatch):
    """Half a latest-graph.json read by a later gate is the failure to avoid.

    The rename is the publish, so a failure at that moment has to leave the
    directory holding the old capture and nothing else.
    """
    destination = tmp_path / "latest-graph.json"
    write_snapshot(destination, build())
    before = destination.read_bytes()

    def refuse(src, dst):
        raise OSError("rename refused")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(OSError):
        write_snapshot(destination, build(run_id="a_later_run"))
    monkeypatch.undo()

    assert destination.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["latest-graph.json"]


def test_a_serialisation_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    destination = tmp_path / "latest-graph.json"
    write_snapshot(destination, build())
    before = destination.read_bytes()

    def refuse(document):
        raise TypeError("not serialisable")

    monkeypatch.setattr(graph_snapshot, "serialise", refuse)
    with pytest.raises(TypeError):
        write_snapshot(destination, build(run_id="a_later_run"))
    monkeypatch.undo()

    assert destination.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["latest-graph.json"]


def test_the_writer_creates_a_missing_runs_directory(tmp_path):
    destination = tmp_path / "runs" / "latest-graph.json"
    write_snapshot(destination, build())
    assert validate_snapshot(json.loads(destination.read_text(encoding="utf-8")))


# -------------------------------------------------- the node info parser
def test_node_info_output_parses_into_endpoints():
    """Same parser as seam_graph.read_live, so the two passes stay comparable."""
    name = "/uav_1/router"
    info = "\n".join([
        name,
        "  Subscribers:",
        f"    {rx('uav_1')}: {PACKET}",
        "    /parameter_events: " + PARAM,
        "  Publishers:",
        f"    {tx('uav_1')}: {PACKET}",
        "    /rosout: " + LOG,
        "  Service Servers:",
        f"    {name}/get_parameters: rcl_interfaces/srv/GetParameters",
        "  Service Clients:",
        "",
        "  Action Servers:",
        "",
        "  Action Clients:",
        "",
    ])
    detail = graph_snapshot.parse_node_info(info)

    assert detail["publishers"] == [ep(tx("uav_1"), PACKET), ep("/rosout", LOG)]
    assert detail["subscribers"][0] == ep(rx("uav_1"), PACKET)
    assert detail["services"] == [ep(f"{name}/get_parameters",
                                     "rcl_interfaces/srv/GetParameters")]
    assert detail["actions"] == []


def test_a_typeless_line_in_node_info_reaches_the_validator():
    """The parser keeps a bad row rather than dropping it, so the build refuses.

    Silently discarding an endpoint it could not parse would turn a broken
    discovery into a clean looking node, which is the whole defect. A row with
    no colon also has to come back with a blank type: seam_graph.read_live's
    rpartition hands the topic back as the type on that row, and its own
    non-blank check then passes an endpoint carrying no type at all.
    """
    info = "\n".join(["/uav_1/router", "  Publishers:", "    /rosout"])
    detail = graph_snapshot.parse_node_info(info)
    assert detail["publishers"] == [{"topic": "/rosout", "type": ""}]

    nodes = {"/uav_1/router": detail}
    with pytest.raises(IncompleteSnapshot):
        build(nodes, commands=raw_commands(nodes))
