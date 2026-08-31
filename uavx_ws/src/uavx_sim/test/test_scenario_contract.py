"""The scenario loader's rejections, one fault at a time.

Every negative case here starts from the real scenarios/harness_check.yaml,
deep copies it, changes exactly one thing and writes that to a temporary file.
A rejection test that starts from an already broken document proves only that
some file somewhere fails, not that the rule under test is the one doing the
work, and it keeps passing after that rule is deleted.

The positive case asserts the values scripts/gate.sh assumes when it runs
`check_scenario.py --duration 60 --vehicles 4 --needs-injected-event` and then
asserts `pose_sample_count>=100` against the run. If the harness scenario is
retuned, this test is where it has to be argued.

Runs under plain pytest with nothing built: conftest.py puts the package root
on sys.path and the loader imports no rclpy.
"""

import copy
import math
from pathlib import Path

import pytest
import yaml

from uavx_sim.scenario import ScenarioError, load

# test/ -> uavx_sim/ -> src/ -> uavx_ws/ -> the repository root.
REPO = Path(__file__).resolve().parents[4]
HARNESS = REPO / "scenarios" / "harness_check.yaml"

# The stem has to stay harness_check: the contract ties `name` to the file
# name, so a temporary file called anything else would fail every case here for
# a reason that has nothing to do with the mutation under test.
STEM = "harness_check"


def good_doc():
    """The harness scenario as parsed, for a mutation to be applied to."""
    assert HARNESS.is_file(), (
        f"{HARNESS} is a chunk 1.3 deliverable and four W1 gates run it")
    return yaml.safe_load(HARNESS.read_text(encoding="utf-8"))


def write(tmp_path, doc):
    path = tmp_path / f"{STEM}.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


def broken(tmp_path, mutate):
    """The good document with one mutation applied, written to a temp file."""
    doc = copy.deepcopy(good_doc())
    mutate(doc)
    return write(tmp_path, doc)


def test_the_harness_scenario_loads_and_says_what_the_gate_assumes(tmp_path):
    scenario = load(HARNESS)

    assert scenario.name == STEM
    assert isinstance(scenario.seed, int) and not isinstance(scenario.seed, bool)
    assert scenario.duration_s == 60
    assert scenario.vehicles == ("uav_1", "uav_2", "uav_3", "uav_4")
    assert scenario.headless is True

    assert len(scenario.injected_events) == 1
    event = scenario.injected_events[0]
    assert event.type in ("kill", "comms_blackout", "gps_degrade")
    assert event.target in scenario.vehicles
    assert event.at_s == 30

    # Unknown top level keys survive the load. The layer altitudes ride in one
    # of them because `vehicles` is contractually a list of ids with nowhere to
    # hang a number, and Stage 2 adds its disturbances the same way.
    assert scenario.raw["hover_altitudes_m"] == {
        "uav_1": 30, "uav_2": 40, "uav_3": 50, "uav_4": 60}

    # The mutation pipeline itself has to produce a scenario that loads, or
    # every rejection below could be passing on the round trip rather than on
    # the field it changed.
    assert load(broken(tmp_path, lambda doc: None)).name == STEM


@pytest.mark.parametrize("key", ["name", "seed", "duration_s", "vehicles",
                                 "injected_events", "headless"])
def test_a_missing_required_key_is_rejected(tmp_path, key):
    path = broken(tmp_path, lambda doc: doc.pop(key))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert key in str(caught.value)


def test_a_name_that_is_not_the_file_stem_is_rejected(tmp_path):
    path = broken(tmp_path, lambda doc: doc.update(name="some_other_scenario"))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    message = str(caught.value)
    assert "name" in message and STEM in message


@pytest.mark.parametrize("seed", ["17", 17.5, True, None])
def test_a_seed_that_is_not_an_integer_is_rejected(tmp_path, seed):
    path = broken(tmp_path, lambda doc: doc.update(seed=seed))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "seed" in str(caught.value)


@pytest.mark.parametrize("duration", [0, -60, "60", math.inf, math.nan])
def test_a_duration_that_is_not_positive_and_finite_is_rejected(tmp_path, duration):
    path = broken(tmp_path, lambda doc: doc.update(duration_s=duration))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "duration_s" in str(caught.value)


def test_an_empty_vehicle_list_is_rejected(tmp_path):
    path = broken(tmp_path, lambda doc: doc.update(vehicles=[]))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "vehicles" in str(caught.value)


def test_a_duplicated_vehicle_id_is_rejected(tmp_path):
    # uav_2 twice, and uav_4 gone, so the list is still four entries long and
    # only uniqueness distinguishes it from the good one.
    path = broken(tmp_path, lambda doc: doc.update(
        vehicles=["uav_1", "uav_2", "uav_3", "uav_2"]))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "vehicles" in str(caught.value)


@pytest.mark.parametrize("vehicle", ["uav_x", "drone_4", "uav_", "UAV_4", 4])
def test_a_vehicle_id_that_is_not_uav_digits_is_rejected(tmp_path, vehicle):
    path = broken(tmp_path, lambda doc: doc["vehicles"].__setitem__(3, vehicle))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "vehicles" in str(caught.value)


@pytest.mark.parametrize("event", ["kill uav_2 at 30", ["kill", "uav_2", 30], 30])
def test_an_event_that_is_not_a_mapping_is_rejected(tmp_path, event):
    path = broken(tmp_path, lambda doc: doc["injected_events"].__setitem__(0, event))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "injected_events" in str(caught.value)


@pytest.mark.parametrize("event_type", ["crash", "KILL", "", None])
def test_an_unknown_event_type_is_rejected(tmp_path, event_type):
    path = broken(tmp_path,
                  lambda doc: doc["injected_events"][0].update(type=event_type))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    message = str(caught.value)
    assert "injected_events" in message and "type" in message


@pytest.mark.parametrize("target", ["uav_9", "gcs", None])
def test_an_event_target_that_is_not_a_listed_vehicle_is_rejected(tmp_path, target):
    path = broken(tmp_path,
                  lambda doc: doc["injected_events"][0].update(target=target))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    message = str(caught.value)
    assert "injected_events" in message and "target" in message


@pytest.mark.parametrize("at_s", [-1, 60, 90, "30", math.inf, math.nan, None])
def test_an_event_time_outside_the_run_is_rejected(tmp_path, at_s):
    # 60 is in here because the window is half open. An event at exactly
    # duration_s fires as the run ends or not at all, and the record cannot
    # say which.
    path = broken(tmp_path,
                  lambda doc: doc["injected_events"][0].update(at_s=at_s))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "at_s" in str(caught.value)


@pytest.mark.parametrize("headless", [False, "true", "yes", 1, None])
def test_headless_that_is_not_exactly_true_is_rejected(tmp_path, headless):
    # 1 is in here on purpose: `headless is not True` is the only test that
    # rejects it, and `if not headless` would let a truthy 1 launch a GUI.
    path = broken(tmp_path, lambda doc: doc.update(headless=headless))
    with pytest.raises(ScenarioError) as caught:
        load(path)
    assert "headless" in str(caught.value)
