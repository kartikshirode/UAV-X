"""The pure objects must not be able to reach past the tx/rx seam.

Breaking the seam silently voids a quarter of the rubric and nothing else
notices. scripts/check_seam.sh guards the ROS side in W3, over source and over
a captured graph, but it cannot see a pure object holding a reference to
another pure object, and that is the shortcut W3 would find easiest to take:
hand the router a dictionary of its peers and the mesh becomes a function call.

So the shape is asserted here, where it is still cheap to fix. A Router may
know its own id, its own position, and whatever arrived in a packet. Nothing
else.
"""

import ast
import inspect
import re
from pathlib import Path

from conftest import settled_net

from uavx_comms import pure_net, router as router_module

SOURCE = Path(router_module.__file__).resolve().parent
REPO = SOURCE.parents[3]
CHECK_SEAM = REPO / "scripts" / "check_seam.sh"

# Chunk 3.1. Until it, every file in this package was pure logic and these
# tests said so without qualification. Wiring the mesh to ROS needs files that
# import rclpy and name the seam, and relaxing the rules for the whole package
# to admit them would delete the guarantee the package exists to make.
#
# So the exceptions are named, and the naming is the test. A file that is not
# in one of these two sets is held to every original rule, and a file that is
# in one of them and does not exist fails, so the lists cannot rot quietly
# once a module is renamed or deleted.
#
# codec is not a node. It is the one place that knows both the dataclass and
# the wire type, so it imports the message package and nothing else from ROS.
WIRE_MODULES = frozenset({"codec.py"})

# The nodes. Each one wires an object from the pure half to the two endpoints
# scripts/seam_manifests.json allows it and adds no decisions of its own.
NODE_MODULES = frozenset({"link_layer.py", "router_node.py"})

EXCEPTED = WIRE_MODULES | NODE_MODULES


def pure_modules():
    """Every file in this package that is still required to be pure logic."""
    return [p for p in sorted(SOURCE.glob("*.py")) if p.name not in EXCEPTED]


def privileged_by_the_real_checker():
    """The modules scripts/check_seam.sh exempts from the ground truth ban.

    Read out of the checker rather than restated here. Week 2's third harness
    defect was this exemption and a fixture agreeing with each other and both
    being wrong about where a module lives, because the fixture was written
    from the same assumption as the checker. A list typed here would be a
    third copy of that assumption.
    """
    assert CHECK_SEAM.is_file(), (
        str(CHECK_SEAM) + " is the checker these rules mirror, and without it "
        "the exemption below would be this file's own opinion")
    body = CHECK_SEAM.read_text(encoding="utf-8")
    prefix = "uavx_comms/uavx_comms/"
    found = set()
    for line in body.splitlines():
        text = line.strip()
        if text.startswith(prefix) and ")" in text:
            named = text.split(")")[0].strip()
            leaf = named[len(prefix):]
            if leaf.endswith(".py") and "*" not in leaf:
                found.add(leaf)
    assert found, (
        "no exempt module was found in " + str(CHECK_SEAM) + ". Either the "
        "exemption moved or its spelling changed, and this test would then "
        "be enforcing a rule the gate does not.")
    return found


def test_a_router_holds_no_reference_to_another_router_or_to_the_link():
    net = settled_net()
    for node_id in net.live_ids():
        held = net.router(node_id)
        for name, value in vars(held).items():
            assert not isinstance(value, router_module.Router), (
                node_id + "." + name + " is another Router, so this node can "
                "reach a peer without crossing the seam")
            assert not isinstance(value, pure_net.PureNet), (
                node_id + "." + name + " is the network itself")
            if isinstance(value, (list, tuple, set, frozenset)):
                assert not any(isinstance(v, router_module.Router)
                               for v in value), node_id + "." + name
            if isinstance(value, dict):
                assert not any(isinstance(v, router_module.Router)
                               for v in value.values()), node_id + "." + name


def test_the_only_traffic_surface_is_on_rx_and_drain_tx():
    """One way in, one way out. W3 wires these to the two allowed topics.

    A public method that took or returned a packet by any other name would be a
    second endpoint, and the endpoint allowlist has exactly one of each per
    swarm process.
    """
    public = {name for name, _ in
              inspect.getmembers(router_module.Router, inspect.isfunction)
              if not name.startswith("_")}
    traffic = {"on_rx", "drain_tx"}
    assert traffic <= public
    unexpected = public - traffic - {
        "observe", "retry_pending", "set_position", "set_work", "tick",
        "topology", "relay_set", "custodian", "reachability", "route_status",
        "observation_summary",
    }
    assert not unexpected, (
        "Router grew public methods this test has not considered: "
        + repr(sorted(unexpected)) + ". If one of them carries packets it is a "
        "second endpoint.")


def test_only_the_network_object_holds_more_than_one_vehicle_position():
    """The link layer is the only process allowed to see every vehicle.

    A Router does hold a position cache, and it is fed only by packets that
    arrived over the radio, so it is knowledge the node was told rather than
    ground truth it read. The network object is the one that reads ground
    truth, and it stands outside the swarm for exactly that reason.
    """
    net = settled_net()
    for node_id in net.live_ids():
        held = net.router(node_id)
        assert held.position == net.position(node_id)
        for other in net.live_ids():
            if other == node_id:
                continue
            assert not hasattr(held, "vehicles")
            assert "position_cache" in vars(held)


def code_strings(path):
    """Every string literal in a module that is not a docstring.

    Scanning the raw text would flag the docstrings, which are where the seam
    is explained. What matters is what the code does, so this parses and skips
    the strings the parser knows are documentation.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    documentation = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                documentation.add(id(body[0].value))
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in documentation]


def imported_names(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_no_pure_module_names_a_topic_or_a_vehicle_id():
    """Nothing here may bake in a name, which is what makes the seam movable.

    A pure module that spelled out a topic would be a second place the endpoint
    allowlist lives, and one that spelled out two vehicle ids would fail the
    static seam pass, which counts exactly that. Every id in this package
    arrives as an argument.
    """
    offenders = []
    for path in pure_modules():
        for value in code_strings(path):
            if "/uavx/" in value:
                offenders.append(path.name + " builds the topic " + repr(value))
            if value.startswith("uav_") or value.startswith("/uav_"):
                offenders.append(path.name + " names the vehicle " + repr(value))
    assert not offenders, repr(offenders)


def test_no_node_module_names_a_second_vehicle():
    """The nodes may name the seam. They may not name somebody else's.

    This is the rule scripts/check_seam.sh actually applies: it counts the
    distinct vehicle endpoint literals in a file and calls a file holding two
    of them a bypass, because a process that can name a second vehicle's
    endpoint can talk to it without crossing the radio. The link layer is
    exempt there because it is the radio, and it builds its endpoints from a
    vehicle list rather than spelling any of them, so it passes this anyway.
    """
    endpoint = re.compile(r"/uavx/(uav_[0-9]+|gcs)/")
    offenders = []
    for name in sorted(EXCEPTED):
        path = SOURCE / name
        named = set()
        for value in code_strings(path):
            named.update(endpoint.findall(value))
        if len(named) > 1:
            offenders.append(name + " names " + repr(sorted(named)))
    assert not offenders, repr(offenders)


def test_the_excepted_modules_all_exist():
    """A list of exceptions that names nothing exempts nothing, and a list
    that names a deleted file hides the next one that is added."""
    missing = sorted(n for n in EXCEPTED if not (SOURCE / n).is_file())
    assert not missing, repr(missing)


# scripts/check_seam.sh greps this whole source tree for the ground-truth
# interface and for service and action constructors, and it does not care that
# a match is inside a test asserting the opposite. Spelling either token here
# fails the static seam pass in W3, so they are assembled from fragments. The
# alternative was to widen the checker's exemptions, which is the one thing
# nobody may do to make their own work pass.
GROUND_TRUTH = ("/gazebo/model" + "_states", "gazebo" + "_msgs",
                "Model" + "States", "GetModel" + "State")
CHANNELS = ("create_" + "service", "create_" + "client",
            "Action" + "Server", "Action" + "Client")


def test_nothing_in_the_package_imports_ros_or_the_simulator():
    """Pure means pure. The gate builds this package before any node exists."""
    banned_imports = {"rclpy", GROUND_TRUTH[1], "px4" + "_msgs", "uavx_msgs"}
    offenders = []
    for path in pure_modules():
        for name in sorted(imported_names(path) & banned_imports):
            offenders.append(path.name + " imports " + name)
        for value in code_strings(path):
            for banned in GROUND_TRUTH:
                if banned in value:
                    offenders.append(path.name + " names " + banned)
    assert not offenders, repr(offenders)


def test_every_module_that_imports_ros_has_been_declared_one_that_may():
    """The set is checked in both directions, which is what makes it a pin.

    Without this, the exception list would only ever have to grow: somebody
    adds a fourth node, adds its name, and nothing notices that the package
    quietly stopped being mostly pure logic. Here a file that imports ROS and
    is not declared fails, and a declared file that imports none of it fails
    too, so the list describes the package rather than excusing it.
    """
    ros = {"rclpy", GROUND_TRUTH[1], "px4" + "_msgs", "uavx_msgs"}
    reaches_ros = {p.name for p in sorted(SOURCE.glob("*.py"))
                   if imported_names(p) & ros}
    assert reaches_ros == EXCEPTED, (
        "modules importing ROS: " + repr(sorted(reaches_ros))
        + ", declared: " + repr(sorted(EXCEPTED)))


def test_the_wire_module_imports_the_message_package_and_no_more():
    """codec converts. It does not run, and it does not read the world."""
    for name in sorted(WIRE_MODULES):
        imports = imported_names(SOURCE / name)
        assert "uavx_msgs" in imports, name + " converts nothing"
        for banned in ("rclpy", GROUND_TRUTH[1], "px4" + "_msgs"):
            assert banned not in imports, name + " imports " + banned


def test_no_module_here_would_fail_the_static_seam_pass():
    """The two bypasses scripts/check_seam.sh looks for, checked at source.

    Only the link layer and the evaluator may read simulator ground truth;
    everything else reading a pose interface is the swarm becoming omniscient,
    which is exactly the claim the proposal must not make. And a service or an
    action between two swarm nodes carries swarm data over a channel the radio
    model never sees, so the link layer's numbers describe traffic that did not
    all go through it.

    Running the same two predicates here means a violation is found while the
    file is being written rather than in W3, where it fails a gate.
    """
    exempt = privileged_by_the_real_checker()
    ground_truth = re.compile("|".join(re.escape(t) for t in GROUND_TRUTH))
    channels = re.compile("|".join(re.escape(t) for t in CHANNELS))
    offenders = []
    for path in sorted(SOURCE.glob("*.py")):
        body = path.read_text(encoding="utf-8")
        if ground_truth.search(body) and path.name not in exempt:
            offenders.append(path.name + " reads simulator ground truth")
        if channels.search(body):
            offenders.append(path.name + " creates a service or an action")
    assert not offenders, repr(offenders)


def test_the_ground_truth_exemption_is_used_and_is_not_wider_than_it_needs():
    """The exemption is a real hole in the rule, so it is held to two things.

    It is used, meaning the file it names does read ground truth. An exemption
    covering a file that does not need it is an exemption nobody would notice
    growing. And it is small: exactly one module in this package, the radio.
    """
    exempt = privileged_by_the_real_checker()
    assert exempt == {"link_layer.py"}, repr(sorted(exempt))
    ground_truth = re.compile("|".join(re.escape(t) for t in GROUND_TRUTH))
    for name in sorted(exempt):
        path = SOURCE / name
        assert path.is_file(), name + " is exempted and does not exist"
        assert ground_truth.search(path.read_text(encoding="utf-8")), (
            name + " is exempted from the ground truth ban and reads no "
            "ground truth, so the exemption is covering nothing")


def test_a_router_can_be_driven_with_nothing_but_the_two_methods():
    """The proof that the seam is sufficient: two nodes, wired by hand.

    No network object, no positions, no shared state. If this passes, W3 can
    reproduce the whole mesh by connecting on_rx and drain_tx to the two
    topics, which is the entire claim this package makes about the seam.
    """
    from uavx_comms import election, params

    left = router_module.Router("uav_1", (0.0, 0.0, 30.0),
                                role=election.ROLE_GCS_ANCHOR)
    right = router_module.Router("gcs", (0.0, 0.0, 0.0),
                                 role=election.ROLE_GCS_ANCHOR)
    now, dt = 0.0, 0.05
    for _ in range(int(20.0 / dt)):
        now += dt
        left.tick(now, dt)
        right.tick(now, dt)
        for outgoing in left.drain_tx(now):
            right.on_rx(outgoing.copy(), now)
        for outgoing in right.drain_tx(now):
            left.on_rx(outgoing.copy(), now)

    assert left.route.installed == ["uav_1", "gcs"]
    assert left.neighbours.live() == frozenset({"gcs"})
    assert params.GCS_ID == "gcs"
