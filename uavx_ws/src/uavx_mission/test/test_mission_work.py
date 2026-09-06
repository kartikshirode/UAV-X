"""What a mission executor is for is decided once, not asked three times.

Chunk 4.5's first encounter flight died in the constructor:

    f"{self.vehicle_id} surveying strip {strip.index}, "
    AttributeError: 'NoneType' object has no attribute 'index'

`self.station is None` had meant "this vehicle is surveying" for two weeks and
it was true. Then a third kind of work arrived, a vehicle flying a straight
line has no station either, and three separate conditions had to be updated
together. Two of them were. The third logged the survey plan a track vehicle
never had.

So the question is asked once, in the constructor, and the answer is
`self.work`. This test is the rule that keeps it that way: outside
construction, nothing branches on which of the three fields happens to be set.

A unit test cannot start a ROS node without a graph and does not need to. The
rule is visible in the source, which is the same argument test_node_attributes
makes about reserved property names.

    python3 -m pytest -q uavx_ws/src/uavx_mission/test/test_mission_work.py
"""

import ast
from pathlib import Path

import pytest

# test/ -> uavx_mission/ -> src/ -> uavx_ws/ -> the repository root.
REPO = Path(__file__).resolve().parents[4]
NODE = REPO / "uavx_ws" / "src" / "uavx_mission" / "uavx_mission" / "mission_node.py"

# The fields that say what one particular kind of work needs. Reading them is
# fine; deciding what the vehicle is by looking at them is what went wrong.
WORK_FIELDS = ("station", "track", "mission")


def mission_node():
    tree = ast.parse(NODE.read_text(encoding="utf-8"))
    for item in ast.walk(tree):
        if isinstance(item, ast.ClassDef) and item.name == "MissionNode":
            return item
    raise AssertionError(f"no MissionNode class in {NODE}")


def methods(cls):
    return [item for item in cls.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]


def self_attributes(node):
    """Every `self.<name>` read anywhere inside this subtree."""
    out = set()
    for item in ast.walk(node):
        if (isinstance(item, ast.Attribute)
                and isinstance(item.value, ast.Name)
                and item.value.id == "self"):
            out.add(item.attr)
    return out


def presence_tests(node):
    """The `self.<field> is None` comparisons in this subtree, by field name.

    The shape and not merely the name. `self.track.arrival_s` inside a test is
    a track vehicle asking how long its own line is, which is a reading of the
    field rather than a decision about what kind of vehicle this is. What
    broke was asking whether the field is set at all.
    """
    out = set()
    for item in ast.walk(node):
        if not isinstance(item, ast.Compare):
            continue
        if not all(isinstance(op, (ast.Is, ast.IsNot)) for op in item.ops):
            continue
        looks_at_none = any(isinstance(c, ast.Constant) and c.value is None
                            for c in item.comparators)
        if not looks_at_none:
            continue
        left = item.left
        if (isinstance(left, ast.Attribute)
                and isinstance(left.value, ast.Name)
                and left.value.id == "self"):
            out.add(left.attr)
    return out


def test_the_node_has_one_field_saying_what_it_is_for():
    assigned = [item for item in ast.walk(mission_node())
                if isinstance(item, ast.Assign)
                and "work" in self_attributes(ast.Module(body=[item],
                                                         type_ignores=[]))]
    targets = [t for item in assigned for t in item.targets
               if isinstance(t, ast.Attribute) and t.attr == "work"]
    assert len(targets) == 1, (
        "self.work is assigned in more than one place, so there is more than "
        "one answer to what this vehicle is for")


@pytest.mark.parametrize("method", [m.name for m in methods(mission_node())
                                    if m.name != "__init__"])
def test_no_method_decides_the_work_by_looking_at_its_fields(method):
    """Outside the constructor, branch on self.work and on nothing else.

    self.station is None was a correct test for surveying until a vehicle
    flying a track had no station either. Whichever field a future fourth kind
    of work leaves unset, this is the shape that breaks.
    """
    body = next(m for m in methods(mission_node()) if m.name == method)
    for branch in ast.walk(body):
        if not isinstance(branch, (ast.If, ast.IfExp)):
            continue
        looked_at = presence_tests(branch.test) & set(WORK_FIELDS)
        assert not looked_at, (
            f"{method} decides what to do by asking whether self."
            f"{sorted(looked_at)[0]} is set. What this vehicle is for is "
            f"decided once in the constructor; branch on self.work.")


def test_the_constructor_is_allowed_to_ask():
    """The exemption is deliberate and it is one place.

    Somewhere has to look at the fields to work out which of the three this
    is. That place is the constructor, and this test exists so that the rule
    above reads as a rule with an exception rather than as an oversight.
    """
    setup = next(m for m in methods(mission_node()) if m.name == "__init__")
    looked_at = set()
    for branch in ast.walk(setup):
        if isinstance(branch, (ast.If, ast.IfExp)):
            looked_at |= presence_tests(branch.test) & set(WORK_FIELDS)
    assert looked_at, ("the constructor no longer works out what this vehicle "
                       "is for, so self.work comes from somewhere else now")
