"""No node in this workspace may assign over a property rclpy.node.Node owns.

Chunk 2.4's first survey run died four times in four seconds. Every mission
executor raised in its own constructor:

    self.executor = MissionExecutor(...)
    AttributeError: 'MissionExecutor' object has no attribute 'add_node'

`executor` is a property on `rclpy.node.Node` with a setter, and that setter
calls `new_executor.add_node(self)` on whatever it is handed. So an attribute
named after the thing the class is about ran straight into a name the base
class had already claimed. Nothing caught it because chunk 2.1 proved the
planner, the partitioner and the executor with no simulator and no ROS, which
was the right call for the arithmetic and left the twenty lines of wiring
around it never once executed.

A unit test cannot start a ROS node without a graph, but it does not need to.
The collision is visible in the source, and the list of names to avoid is
visible in rclpy. This reads the first out of the tree and the second out of
the library, so neither is a copy that can drift.

It scans every package under uavx_ws/src rather than only this one. The
defect belongs to the shape of an rclpy subclass and not to uavx_mission, and
the collector two directories away is the same kind of file. It lives here
because this is the package that had the bug.

    python3 -m pytest -q uavx_ws/src/uavx_mission/test/test_node_attributes.py

Needs rclpy, so it skips on a checkout with nothing sourced and runs for real
under `colcon test`, which is where the gate calls it.
"""

import ast
from pathlib import Path

import pytest

rclpy_node = pytest.importorskip(
    "rclpy.node", reason="the list of reserved names comes from rclpy itself")

# test/ -> uavx_mission/ -> src/ -> uavx_ws/ -> the repository root.
REPO = Path(__file__).resolve().parents[4]
SRC = REPO / "uavx_ws" / "src"


def reserved_names():
    """Every property rclpy.node.Node defines, read off the class."""
    return {name for name, value in vars(rclpy_node.Node).items()
            if isinstance(value, property)}


def node_subclasses(tree):
    """Classes in one module whose bases include something called Node."""
    for item in ast.walk(tree):
        if not isinstance(item, ast.ClassDef):
            continue
        for base in item.bases:
            name = base.id if isinstance(base, ast.Name) else (
                base.attr if isinstance(base, ast.Attribute) else None)
            if name == "Node":
                yield item
                break


def assigned_attributes(cls):
    """`self.<name>` on the left of an assignment anywhere in the class."""
    found = {}
    for item in ast.walk(cls):
        targets = []
        if isinstance(item, ast.Assign):
            targets = item.targets
        elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
            targets = [item.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"):
                found.setdefault(target.attr, item.lineno)
    return found


def node_modules():
    """Every source file under uavx_ws/src that defines an rclpy node."""
    for path in sorted(SRC.rglob("*.py")):
        if "/test/" in path.as_posix():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                          # pragma: no cover
            continue
        for cls in node_subclasses(tree):
            yield path, cls


def test_the_workspace_has_nodes_to_check():
    """A scan that matches nothing reports agreement forever."""
    assert SRC.is_dir(), f"{SRC} is where this repository's packages live"
    found = list(node_modules())
    assert found, ("no rclpy node was found under uavx_ws/src, so this test "
                   "is checking nothing. Either the layout moved or the base "
                   "class is being named some other way.")


def test_no_node_assigns_over_an_rclpy_property():
    reserved = reserved_names()
    assert "executor" in reserved, (
        "rclpy.node.Node has no `executor` property any more. The defect this "
        "test was written for is gone; check what replaced it before deleting "
        "the test.")

    problems = []
    for path, cls in node_modules():
        rel = path.relative_to(REPO).as_posix()
        for attribute, line in assigned_attributes(cls).items():
            if attribute in reserved:
                problems.append(
                    f"{rel}:{line} {cls.name} assigns self.{attribute}, which "
                    f"rclpy.node.Node already defines as a property")
    assert not problems, "\n".join(problems)


def test_the_check_would_catch_the_defect_it_was_written_for():
    """The scan is live, not a tautology that passes on any input."""
    source = (
        "from rclpy.node import Node\n"
        "class Example(Node):\n"
        "    def __init__(self):\n"
        "        self.executor = object()\n"
        "        self.mission = object()\n")
    tree = ast.parse(source)
    classes = list(node_subclasses(tree))
    assert len(classes) == 1
    attributes = assigned_attributes(classes[0])
    assert set(attributes) == {"executor", "mission"}
    assert set(attributes) & reserved_names() == {"executor"}
