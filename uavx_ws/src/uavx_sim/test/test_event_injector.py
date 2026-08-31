"""Chunk 1.4: does an injected event change the target and get observed.

The question this file answers is not whether the injector can list an event.
It is whether the thing it recorded actually happened. `scripts/gate.sh` chunk
1.7 asserts `injected_event_observed` and `injected_event_count`, and both are
only worth asserting if an injector that fires into the void reports itself as
such. So the case that matters most here is the effect that never becomes
visible, and that test is written to fail loudly rather than quietly.

Plain pytest, no simulator and no ROS graph:

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_event_injector.py
"""

import json
from pathlib import Path

import pytest

from uavx_sim.event_injector import RECORD_KEYS, EventInjector, PendingEvent


class FakeWorld:
    """What the runner's two callables talk to, with the wiring left out.

    `apply` and `is_visible` are deliberately not connected. Nothing makes an
    effect visible except a test saying it became visible, which is the only
    way the never-visible case can be written at all. A fake that flipped
    visibility inside `apply` would pass an injector that stamps observed_t at
    fire time, which is the bug this file exists to catch.
    """

    def __init__(self):
        self.applied = []
        self._visible = set()

    def apply(self, event_type, target):
        self.applied.append((event_type, target))

    def is_visible(self, event_type, target):
        return (event_type, target) in self._visible

    def becomes_visible(self, event_type, target):
        self._visible.add((event_type, target))


def kill_at(at_s, target="uav_2"):
    return PendingEvent(type="kill", target=target, at_s=at_s)


def injector_for(events, world):
    return EventInjector(events, world.apply, world.is_visible)


# ------------------------------------------------------------------ firing

def test_an_event_fires_once_however_many_ticks_pass():
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    for step in range(30, 60):
        injector.tick(float(step))

    assert world.applied == [("kill", "uav_2")], (
        "the kill was applied more than once. A fault injected twice leaves "
        "the scenario in a state no record describes."
    )
    assert injector.count_fired() == 1


def test_an_event_does_not_fire_before_its_at_s():
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    for early in (0.0, 15.0, 29.0, 29.999):
        injector.tick(early)
    assert world.applied == []

    injector.tick(30.0)
    assert world.applied == [("kill", "uav_2")], "at_s is inclusive; t=30 fires"


def test_the_effect_is_applied_to_the_named_target():
    world = FakeWorld()
    injector = injector_for(
        [
            PendingEvent(type="kill", target="uav_2", at_s=10.0),
            PendingEvent(type="comms_blackout", target="uav_4", at_s=20.0),
        ],
        world,
    )

    injector.tick(25.0)

    assert world.applied == [("kill", "uav_2"), ("comms_blackout", "uav_4")]


def test_firing_is_driven_by_the_simulated_time_passed_in():
    """No wall clock anywhere near a value that reaches the record.

    Seeded replay is standing rule 3. An injector reading time.time() would
    make the same seed and the same commit produce different event times.
    """
    import uavx_sim.event_injector as module

    assert not hasattr(module, "time"), "the injector imported a clock"
    assert not hasattr(module, "rclpy"), "the injector imported rclpy at module scope"


# ------------------------------------------------------------- observation

def test_an_unfired_event_is_not_observed_even_when_the_effect_is_visible():
    """A condition that was already true is not evidence the injection worked."""
    world = FakeWorld()
    world.becomes_visible("kill", "uav_2")
    injector = injector_for([kill_at(30.0)], world)

    injector.tick(10.0)
    injector.poll_observations(10.0)

    assert injector.count_observed() == 0
    assert injector.records()[0]["observed_t"] is None


def test_observed_t_is_when_the_effect_appeared_not_when_it_fired():
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    injector.tick(30.0)
    injector.poll_observations(30.0)
    injector.tick(31.0)
    injector.poll_observations(31.0)
    assert injector.records()[0]["observed_t"] is None, (
        "observed_t was stamped while the effect was not yet visible"
    )

    world.becomes_visible("kill", "uav_2")
    injector.tick(32.0)
    injector.poll_observations(32.0)

    record = injector.records()[0]
    assert record["requested_t"] == 30.0
    assert record["observed_t"] == 32.0, (
        "the effect took two ticks to show up, so observed_t is 32, not the "
        "30 the scenario asked for"
    )


def test_observed_t_is_stamped_only_the_first_time_the_effect_is_seen():
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    injector.tick(30.0)
    world.becomes_visible("kill", "uav_2")
    injector.poll_observations(30.5)
    for later in (31.0, 40.0, 55.0):
        injector.poll_observations(later)

    assert injector.records()[0]["observed_t"] == 30.5


def test_observed_t_is_never_earlier_than_requested_t():
    world = FakeWorld()
    events = [
        PendingEvent(type="kill", target="uav_2", at_s=30.0),
        PendingEvent(type="gps_degrade", target="uav_3", at_s=12.5),
    ]
    injector = injector_for(events, world)

    world.becomes_visible("gps_degrade", "uav_3")
    world.becomes_visible("kill", "uav_2")
    for step in range(0, 40):
        injector.tick(float(step))
        injector.poll_observations(float(step))

    for record in injector.records():
        assert record["observed_t"] is not None
        assert record["observed_t"] >= record["requested_t"], (
            f"{record['type']} on {record['target']} was observed at "
            f"{record['observed_t']}, before the {record['requested_t']} it "
            "was requested for"
        )


def test_an_observation_earlier_than_the_request_is_refused():
    """A clock running backwards is a bug to surface, not a value to clamp."""
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    injector.tick(30.0)
    world.becomes_visible("kill", "uav_2")

    with pytest.raises(ValueError, match="backwards"):
        injector.poll_observations(29.0)


# ------------------------------------- the case the whole chunk exists for

def test_an_effect_that_never_becomes_visible_is_never_counted():
    """The injector asked, the target never changed, and the record says so.

    This is the failure the chunk is built around. An injector that stamps
    observed_t when it fires passes every other test in this file and this one
    alone catches it, because here the effect is requested and applied and the
    target never shows it. If observed_t is not None below, the run record is
    claiming an event landed on the evidence that somebody asked for it, and
    every week 4 recovery timing measured against that record is timing a
    swarm that was never broken.
    """
    world = FakeWorld()
    injector = injector_for([kill_at(30.0)], world)

    for step in range(0, 60):
        injector.tick(float(step))
        injector.poll_observations(float(step))

    assert world.applied == [("kill", "uav_2")], (
        "the effect was never applied, so this test is not exercising the "
        "case it was written for"
    )

    record = injector.records()[0]
    assert record["requested_t"] == 30.0, "the request itself is still recorded"
    assert record["observed_t"] is None, (
        f"observed_t is {record['observed_t']!r} for an effect that never "
        "became visible on uav_2. The injector is stamping the request rather "
        "than the observation, so an event that never landed would be counted "
        "as one that did."
    )
    assert injector.count_observed() == 0, (
        "an event nobody ever saw land is in injected_event_count, which is "
        "the number gate.sh chunk 1.7 asserts is at least 1"
    )
    assert injector.all_observed() is False, (
        "all_observed() is true with an unobserved event, so the run record "
        "would carry injected_event_observed true for a run in which nothing "
        "was observed"
    )
    assert injector.unobserved() == [record], (
        "the unobserved event has to be reportable by name, not just missing "
        "from a count"
    )
    assert injector.count_fired() == 1, (
        "fired once and observed never is the distinction the runner logs"
    )


def test_one_unobserved_event_among_several_makes_all_observed_false():
    world = FakeWorld()
    injector = injector_for(
        [
            PendingEvent(type="kill", target="uav_2", at_s=10.0),
            PendingEvent(type="comms_blackout", target="uav_4", at_s=20.0),
        ],
        world,
    )

    world.becomes_visible("kill", "uav_2")
    for step in range(0, 30):
        injector.tick(float(step))
        injector.poll_observations(float(step))

    assert injector.count_observed() == 1
    assert injector.all_observed() is False
    assert [row["target"] for row in injector.unobserved()] == ["uav_4"]


def test_an_injector_with_no_events_claims_nothing():
    """The empty set must not satisfy the claim, the way it did in round 7."""
    injector = injector_for([], FakeWorld())

    injector.tick(30.0)
    injector.poll_observations(30.0)

    assert injector.records() == []
    assert injector.count_observed() == 0
    assert injector.all_observed() is False


def test_an_event_scheduled_past_the_run_is_still_reported():
    world = FakeWorld()
    injector = injector_for([kill_at(90.0)], world)

    for step in range(0, 60):
        injector.tick(float(step))
        injector.poll_observations(float(step))

    assert world.applied == []
    assert injector.records() == [
        {"type": "kill", "target": "uav_2", "requested_t": 90.0, "observed_t": None}
    ]


# ------------------------------------------------------- construction time

def test_an_unknown_event_type_raises_at_construction():
    with pytest.raises(ValueError, match="unknown event type"):
        PendingEvent(type="jam", target="uav_2", at_s=30.0)

    with pytest.raises(ValueError, match="unknown event type"):
        EventInjector(
            [{"type": "jam", "target": "uav_2", "at_s": 30.0}],
            lambda event_type, target: None,
            lambda event_type, target: True,
        )


def test_an_event_missing_a_contract_key_raises_at_construction():
    world = FakeWorld()
    with pytest.raises(ValueError, match="at_s"):
        injector_for([{"type": "kill", "target": "uav_2"}], world)


def test_a_scenario_event_dict_is_accepted_as_given():
    """The runner hands over what the loader read, without reshaping it."""
    world = FakeWorld()
    injector = injector_for([{"type": "kill", "target": "uav_2", "at_s": 30}], world)

    injector.tick(30.0)

    assert world.applied == [("kill", "uav_2")]
    assert injector.records()[0]["requested_t"] == 30.0


# ------------------------------------------------------------ record shape

def schema_event_keys():
    """The required keys the provenance contract itself names, if it is here."""
    for parent in Path(__file__).resolve().parents:
        schema_path = parent / "scenarios" / "run-record.schema.json"
        if schema_path.is_file():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            required = schema["properties"]["injected_events"]["items"]["required"]
            return set(required)
    return None


def test_records_carry_exactly_the_keys_the_schema_names():
    world = FakeWorld()
    injector = injector_for(
        [
            PendingEvent(type="kill", target="uav_2", at_s=10.0),
            PendingEvent(type="gps_degrade", target="uav_3", at_s=20.0),
        ],
        world,
    )
    world.becomes_visible("kill", "uav_2")
    injector.tick(25.0)
    injector.poll_observations(25.0)

    expected = {"type", "target", "requested_t", "observed_t"}
    assert set(RECORD_KEYS) == expected
    for record in injector.records():
        assert set(record) == expected, (
            "a record key the schema does not name, or one it requires and "
            "this record is missing"
        )

    from_schema = schema_event_keys()
    if from_schema is not None:
        assert from_schema == expected, (
            "scenarios/run-record.schema.json requires a different set of keys "
            "than the injector writes"
        )
