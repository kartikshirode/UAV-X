"""Chunk 3.4: the runner's half of the radio, and the ways it can lie.

Two groups of tests, and they fail for different reasons.

The first group is the scenario block. Every number under `comms` is part of
a topology claim, and a station 10 m out or a role nobody assigned produces a
run that flies, delivers packets and proves something other than what
architecture.md section 6 says. Nothing downstream notices that, which is why
it is refused before a simulator starts.

The second group is the ledger arithmetic. The communication row of the
rubric is scored on `delivery_ratio_by_node.uav_4` in `relay_required`
against `direct_only`, and every way of getting that number wrong looks like
a working swarm:

  a denominator the destination supplied, which is always satisfied;
  a numerator holding ids nobody sent;
  a node counting one number and listing another;
  and an edge count standing in for a forwarder count, which turns a direct
  delivery into a relayed one.

    python3 -m pytest -q uavx_ws/src/uavx_sim/test/test_comms.py

Runs on a clean checkout with nothing built.
"""

import json

import pytest

from uavx_sim.comms import (CommsError, arm_radio_command, blackout_at_s,
                            blackout_hold_s, blackout_nodes, comms_spec,
                            delivery_from_ledgers, gate_radio_command,
                            gated_radios_command, gcs_command,
                            link_layer_command, read_ledger,
                            role_manager_command, role_managers_of,
                            router_command, station_node_command)
from uavx_sim.work import WorkError

# Built per index, never written out. scripts/check_seam.sh counts distinct
# vehicle endpoint literals per file and that rule covers tests.
VEHICLES = tuple(f"uav_{n}" for n in range(1, 5))
ANCHOR, RELAY, NEAR, FAR = VEHICLES

# The common geometry table, architecture.md section 6.
STATIONS = {ANCHOR: [165.0, 0.0, 30.0], RELAY: [330.0, 0.0, 40.0],
            NEAR: [475.0, 75.0, 50.0], FAR: [475.0, -75.0, 60.0]}
ALTITUDES = {ANCHOR: 30, RELAY: 40, NEAR: 50, FAR: 60}
ROLES = {ANCHOR: "gcs_anchor", RELAY: "relay", NEAR: "survey", FAR: "survey"}
SPAWN = {"vehicle_id": FAR, "x_m": 0.0, "y_m": -7.5, "z_m": 0.83}


def block(**overrides):
    body = {"enabled": True, "forwarding": True, "elections_enabled": True,
            "roles": dict(ROLES), "stations": {k: list(v)
                                               for k, v in STATIONS.items()}}
    body.update(overrides)
    return {"comms": body}


def spec():
    return comms_spec(block(), VEHICLES, ALTITUDES)


def ident(node, sequence):
    return f"{node}:{sequence}"


def router_ledger(node, count, first=1):
    ids = [ident(node, n) for n in range(first, first + count)]
    return {"node": node, "generated": count, "generated_ids": ids}


def gcs_ledger(delivered, hops=None, edges=None):
    return {"node": "gcs", "delivered_ids": list(delivered),
            "delivered_hops_by_node": dict(hops or {}),
            "delivered_edges_by_node": dict(edges or {})}


# --------------------------------------------------------- the comms block
def test_a_scenario_with_no_comms_block_has_no_radio():
    assert comms_spec({}, VEHICLES, ALTITUDES) is None


def test_communications_disabled_is_the_same_as_absent():
    """survey_baseline says so in the file, and the runner honours it."""
    assert comms_spec(block(enabled=False), VEHICLES, ALTITUDES) is None


def test_the_frozen_geometry_loads():
    got = spec()
    assert got.forwarding is True
    assert got.station_of(FAR) == (475.0, -75.0, 60.0)
    assert got.role_of(RELAY) == "relay"


def test_the_control_differs_in_one_flag():
    """direct_only is relay_required with forwarding off, and nothing else."""
    on = spec()
    off = comms_spec(block(forwarding=False), VEHICLES, ALTITUDES)
    assert off.forwarding is False
    assert off.roles == on.roles
    assert off.stations == on.stations
    assert off.elections_enabled == on.elections_enabled


@pytest.mark.parametrize("key", ["forwarding", "elections_enabled"])
def test_a_flag_left_out_is_refused_rather_than_defaulted(key):
    body = block()
    del body["comms"][key]
    with pytest.raises(CommsError, match=key):
        comms_spec(body, VEHICLES, ALTITUDES)


@pytest.mark.parametrize("value", ["true", 1, None])
def test_a_flag_that_is_not_a_boolean_is_refused(value):
    with pytest.raises(CommsError, match="forwarding"):
        comms_spec(block(forwarding=value), VEHICLES, ALTITUDES)


def test_enabled_must_be_stated_and_is_never_guessed():
    body = block()
    del body["comms"]["enabled"]
    with pytest.raises(CommsError, match="enabled"):
        comms_spec(body, VEHICLES, ALTITUDES)


# ------------------------------------------------------------- the roles
def test_a_role_no_election_could_assign_is_refused():
    roles = dict(ROLES)
    roles[RELAY] = "repeater"
    with pytest.raises(CommsError, match="repeater"):
        comms_spec(block(roles=roles), VEHICLES, ALTITUDES)


def test_a_vehicle_without_a_role_is_refused():
    roles = dict(ROLES)
    del roles[FAR]
    with pytest.raises(CommsError, match=FAR):
        comms_spec(block(roles=roles), VEHICLES, ALTITUDES)


def test_a_role_for_a_vehicle_the_scenario_does_not_fly_is_refused():
    roles = dict(ROLES)
    roles["uav_9"] = "survey"
    with pytest.raises(CommsError, match="uav_9"):
        comms_spec(block(roles=roles), VEHICLES, ALTITUDES)


# ----------------------------------------------------------- the stations
def test_a_station_disagreeing_with_the_climb_is_refused():
    stations = {k: list(v) for k, v in STATIONS.items()}
    stations[FAR][2] = 50.0
    with pytest.raises(CommsError, match="the record cannot say which"):
        comms_spec(block(stations=stations), VEHICLES, ALTITUDES)


def test_a_vehicle_with_no_station_is_refused():
    # Chunk 4.1 moved the reason. A station-keeping scenario is one where
    # every vehicle's work is a point, so a vehicle left out of the block has
    # no work at all, and uavx_sim.work says so before the points are read.
    # The refusal is the same and the message is the more useful one.
    stations = {k: list(v) for k, v in STATIONS.items()}
    del stations[RELAY]
    with pytest.raises(WorkError, match=RELAY):
        comms_spec(block(stations=stations), VEHICLES, ALTITUDES)


def test_a_vehicle_with_no_hover_altitude_has_nothing_to_compare():
    altitudes = dict(ALTITUDES)
    del altitudes[NEAR]
    with pytest.raises(CommsError, match="nothing compares the two"):
        comms_spec(block(), VEHICLES, altitudes)


@pytest.mark.parametrize("point", [[475.0, -75.0], [475.0, -75.0, 60.0, 0.0],
                                   "475,-75,60", None])
def test_a_station_that_is_not_three_numbers_is_refused(point):
    stations = {k: list(v) for k, v in STATIONS.items()}
    stations[FAR] = point
    with pytest.raises(CommsError, match="three numbers"):
        comms_spec(block(stations=stations), VEHICLES, ALTITUDES)


def test_a_station_for_an_absent_vehicle_is_refused():
    stations = {k: list(v) for k, v in STATIONS.items()}
    stations["uav_9"] = [0.0, 0.0, 10.0]
    with pytest.raises(CommsError, match="uav_9"):
        comms_spec(block(stations=stations), VEHICLES, ALTITUDES)


# ------------------------------------------------------------ the commands
def test_the_station_executor_mints_nothing():
    command = station_node_command(FAR, SPAWN, STATIONS[FAR], VEHICLES)
    assert "observations:=false" in command
    assert "station_enu:=[475.000000, -75.000000, 60.000000]" in command
    assert "__ns:=/" + FAR in command


def test_the_station_executor_climbs_to_the_height_it_holds():
    command = station_node_command(FAR, SPAWN, STATIONS[FAR], VEHICLES)
    assert "survey_altitude_m:=60.000000" in command


def test_every_comms_node_runs_on_the_record_s_clock():
    """Simulated time, because every `_s` in the record is measured in it."""
    commands = [
        router_command(FAR, SPAWN, STATIONS[FAR], spec(), "/tmp/r.json"),
        link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24, "/tmp/l.json"),
        gcs_command(spec(), "/tmp/g.json"),
    ]
    for command in commands:
        assert "use_sim_time:=true" in command


def test_the_router_carries_the_flag_that_makes_the_control_a_control():
    off = comms_spec(block(forwarding=False), VEHICLES, ALTITUDES)
    command = router_command(FAR, SPAWN, STATIONS[FAR], off, "/tmp/r.json")
    assert "forwarding:=false" in command
    assert "role:=survey" in command


def test_the_ground_station_is_namespaced_where_the_manifest_expects_it():
    command = gcs_command(spec(), "/tmp/g.json")
    assert "__ns:=/gcs" in command


def test_the_radio_takes_no_namespace():
    """The manifest matches /link_layer by exact name."""
    command = link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24,
                                 "/tmp/l.json")
    assert "__ns" not in " ".join(command)


def test_the_radio_refuses_to_start_without_the_launcher_s_model_map():
    with pytest.raises(CommsError, match="invented distance"):
        link_layer_command(VEHICLES, [], 24, "/tmp/l.json")


@pytest.mark.parametrize("seed", [24.5, "24", True, None])
def test_a_radio_whose_seed_is_not_an_integer_cannot_be_replayed(seed):
    with pytest.raises(CommsError, match="seed"):
        link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], seed, "/tmp/l.json")


# -------------------------------------------------------------- the ledgers
def test_a_perfect_relay_run_reads_one():
    ledgers = [router_ledger(v, 1200) for v in VEHICLES]
    delivered = [i for row in ledgers for i in row["generated_ids"]]
    out = delivery_from_ledgers(
        ledgers, gcs_ledger(delivered, hops={FAR: 2}, edges={FAR: 3}))
    assert out["delivery_ratio"] == 1.0
    assert out["delivery_ratio_by_node"][FAR] == 1.0
    assert out["app_packets_sent_by_node"][FAR] == 1200
    assert out["app_packets_delivered_by_node"][FAR] == 1200
    assert out["delivered_hops_by_node"][FAR] == 2.0
    assert out["delivered_edges_by_node"][FAR] == 3.0


def test_the_control_reads_zero_for_the_far_vehicle_and_one_for_the_anchor():
    ledgers = [router_ledger(v, 100) for v in VEHICLES]
    delivered = router_ledger(ANCHOR, 100)["generated_ids"]
    out = delivery_from_ledgers(ledgers, gcs_ledger(delivered, hops={ANCHOR: 0},
                                                    edges={ANCHOR: 1}))
    assert out["delivery_ratio_by_node"][FAR] == 0.0
    assert out["delivery_ratio_by_node"][ANCHOR] == 1.0
    assert out["app_packets_delivered_by_node"][FAR] == 0
    assert out["delivery_ratio"] == 0.25


def test_the_denominator_is_never_taken_from_what_arrived():
    ledgers = [router_ledger(FAR, 400)]
    delivered = router_ledger(FAR, 100)["generated_ids"]
    out = delivery_from_ledgers(ledgers, gcs_ledger(delivered, hops={FAR: 2},
                                                    edges={FAR: 3}))
    assert out["app_packets_sent_by_node"][FAR] == 400
    assert out["delivery_ratio"] == 0.25


def test_a_delivered_id_nobody_generated_is_refused():
    ledgers = [router_ledger(FAR, 10)]
    with pytest.raises(CommsError, match="nobody generated"):
        delivery_from_ledgers(ledgers, gcs_ledger([ident(FAR, 99)]))


def test_a_node_counting_one_number_and_listing_another_is_refused():
    bad = router_ledger(FAR, 10)
    bad["generated"] = 12
    with pytest.raises(CommsError, match="counted 12"):
        delivery_from_ledgers([bad], gcs_ledger([]))


def test_a_repeated_generated_identity_is_refused():
    bad = router_ledger(FAR, 3)
    bad["generated_ids"].append(ident(FAR, 1))
    bad["generated"] = len(bad["generated_ids"])
    with pytest.raises(CommsError, match="same identity twice"):
        delivery_from_ledgers([bad], gcs_ledger([]))


def test_a_repeated_delivered_identity_is_refused():
    ledgers = [router_ledger(FAR, 3)]
    twice = ledgers[0]["generated_ids"] + [ident(FAR, 1)]
    with pytest.raises(CommsError, match="arrivals rather than deliveries"):
        delivery_from_ledgers(ledgers, gcs_ledger(twice))


def test_a_hop_row_for_an_origin_nobody_generated_is_refused():
    ledgers = [router_ledger(ANCHOR, 5)]
    with pytest.raises(CommsError, match="generated nothing"):
        delivery_from_ledgers(
            ledgers, gcs_ledger(ledgers[0]["generated_ids"], hops={FAR: 2}))


def test_fewer_edges_than_forwarders_is_impossible_and_is_refused():
    ledgers = [router_ledger(FAR, 5)]
    with pytest.raises(CommsError, match="fewer edges"):
        delivery_from_ledgers(
            ledgers, gcs_ledger(ledgers[0]["generated_ids"],
                                hops={FAR: 2}, edges={FAR: 1}))


def test_no_router_ledger_at_all_is_a_refusal_and_not_a_zero():
    with pytest.raises(CommsError, match="division by nothing"):
        delivery_from_ledgers([], gcs_ledger([]))


def test_a_run_that_delivered_nothing_at_all_still_reports_its_denominators():
    ledgers = [router_ledger(v, 50) for v in VEHICLES]
    out = delivery_from_ledgers(ledgers, gcs_ledger([]))
    assert out["delivery_ratio"] == 0.0
    assert set(out["app_packets_sent_by_node"].values()) == {50}
    assert out["delivered_hops_by_node"] == {}


# ----------------------------------------------------------- reading a file
def test_a_missing_ledger_names_the_node_that_did_not_write_it(tmp_path):
    with pytest.raises(CommsError, match="never started or died"):
        read_ledger(tmp_path / "router-uav_9.json", ("node",))


def test_a_ledger_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "router.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CommsError, match="not JSON"):
        read_ledger(path, ("node",))


def test_a_ledger_missing_a_field_the_record_quotes_is_refused(tmp_path):
    path = tmp_path / "router.json"
    path.write_text(json.dumps({"node": FAR}), encoding="utf-8")
    with pytest.raises(CommsError, match="generated_ids"):
        read_ledger(path, ("node", "generated_ids"))


def test_a_good_ledger_comes_back_whole(tmp_path):
    path = tmp_path / "router.json"
    path.write_text(json.dumps(router_ledger(FAR, 3)), encoding="utf-8")
    assert read_ledger(path, ("node", "generated_ids"))["generated"] == 3


# ----------------------------------------------------------- the role manager
def test_a_run_with_an_election_gives_every_vehicle_a_role_manager():
    assert role_managers_of(spec(), VEHICLES) == VEHICLES


def test_a_run_that_forbids_the_election_starts_none():
    """queue_drain and the encounter pair keep the radio and forbid the vote.

    A role manager there would hold a tx endpoint it never sends on, and
    scripts/seam_manifests.json names the same three scenarios that have one.
    """
    quiet = comms_spec(block(elections_enabled=False), VEHICLES, ALTITUDES)
    assert role_managers_of(quiet, VEHICLES) == ()


def test_a_run_with_no_radio_starts_none():
    assert role_managers_of(None, VEHICLES) == ()


def test_the_role_manager_is_namespaced_where_the_manifest_expects_it():
    command = role_manager_command(FAR, SPAWN, spec(), "/tmp/role.json")
    assert "__ns:=/" + FAR in command
    assert "use_sim_time:=true" in command
    assert "role:=survey" in command


def test_it_is_told_the_point_it_goes_back_to():
    command = role_manager_command(FAR, SPAWN, spec(), "/tmp/role.json",
                                   station=STATIONS[FAR])
    assert "station_enu:=[475.000000, -75.000000, 60.000000]" in command


def test_a_vehicle_flying_a_strip_is_given_no_station_at_all():
    """Rather than three NaNs.

    `ros2 run -p` parses its values as YAML and `nan` there is the string,
    which rclpy refuses against a declared double array after the simulator
    is already up. The node declares the unset default itself.
    """
    command = role_manager_command(FAR, SPAWN, spec(), "/tmp/role.json")
    assert "station_enu" not in " ".join(command)


@pytest.mark.parametrize("bad", [[1.0, 2.0], [1.0, 2.0, float("nan")]])
def test_a_station_that_is_not_a_point_is_refused(bad):
    with pytest.raises(CommsError, match="three finite numbers"):
        role_manager_command(FAR, SPAWN, spec(), "/tmp/role.json", station=bad)


# --------------------------------------------------------------- the blackout
def gated(**overrides):
    row = {"type": "comms_blackout", "target": RELAY, "at_s": 120,
           "restore_at_s": 240}
    row.update(overrides)
    return {"injected_events": [row]}


def test_the_hold_is_the_gap_between_the_two_times():
    assert blackout_hold_s(gated()) == 120.0


def test_a_scenario_that_gates_nothing_holds_nothing():
    assert blackout_hold_s({}) == 0.0
    assert blackout_hold_s({"injected_events": [
        {"type": "kill", "target": RELAY, "at_s": 120}]}) == 0.0


def test_a_blackout_with_no_end_cannot_produce_a_hold():
    body = gated()
    del body["injected_events"][0]["restore_at_s"]
    with pytest.raises(CommsError, match="how long to hold"):
        blackout_hold_s(body)


def test_two_blackouts_of_different_lengths_are_refused():
    # One hold is passed to the radio, so two would mean one of them ends at
    # a time nobody chose.
    body = gated()
    body["injected_events"].append({"type": "comms_blackout", "target": NEAR,
                                    "at_s": 120, "restore_at_s": 300})
    with pytest.raises(CommsError, match="one of the blackouts"):
        blackout_hold_s(body)


def test_the_radio_is_told_how_long_to_hold_a_gate():
    command = link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24,
                                 "/tmp/l.json", hold_s=120.0)
    assert "blackout_hold_s:=120.000000" in command


def test_a_run_with_no_blackout_still_says_so():
    command = link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24,
                                 "/tmp/l.json")
    assert "blackout_hold_s:=0.000000" in command


# ---------------------------------------------------------- arming the radio
def test_the_scenario_names_who_a_blackout_is_for():
    assert blackout_nodes(gated()) == (RELAY,)
    assert blackout_nodes({}) == ()
    assert blackout_nodes({"injected_events": [
        {"type": "kill", "target": RELAY, "at_s": 120}]}) == ()


def test_the_radio_learns_who_at_launch():
    """Beside the hold, because the scenario knows both at that point.

    Naming them at launch is what lets the gate be one parameter carrying a
    time. Two parameters at fire time, one for who and one for when, would
    land in whichever order DDS delivered them, and the wrong order gates the
    radio the moment the first one arrives.
    """
    command = link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24,
                                 "/tmp/l.json", hold_s=120.0, gated=[RELAY])
    assert f"blackout_nodes:=[{RELAY}]" in command


def test_a_run_with_no_blackout_names_nobody():
    command = link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24,
                                 "/tmp/l.json")
    assert not any(arg.startswith("blackout_nodes:=") for arg in command), (
        "the node declares the empty default itself, and an empty list is not "
        "a value the parameter renderer has a spelling for")


def test_a_blackout_aimed_at_a_vehicle_that_is_not_flying_is_refused():
    with pytest.raises(CommsError, match="does not fly"):
        link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24, "/tmp/l.json",
                           gated=["uav_9"])


def test_the_scenario_names_when_a_blackout_is_due():
    assert blackout_at_s(gated()) == 120.0
    assert blackout_at_s({}) is None


def test_two_blackouts_due_at_different_moments_are_refused():
    # One instant is armed on the radio. Two would mean one of the faults
    # lands when a parameter call happened to arrive, which is the behaviour
    # the arming replaced.
    body = gated()
    body["injected_events"].append({"type": "comms_blackout", "target": NEAR,
                                    "at_s": 130, "restore_at_s": 250})
    with pytest.raises(CommsError, match="one of the blackouts"):
        blackout_at_s(body)


def test_the_gate_is_armed_with_an_absolute_simulated_time():
    """Scenario seconds are the runner's own count and mean nothing here.

    The radio reads /clock and the scenario counts from its own zero. Only the
    runner knows the offset between them, so the conversion happens before the
    command is built and the radio is handed a number it can compare against
    its own clock with no arithmetic.
    """
    command = arm_radio_command(683.45)
    assert command[:5] == ["ros2", "param", "set", "/link_layer",
                           "radio_off_at_s"]
    assert command[5] == "683.450000"


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf"), None])
def test_a_gate_armed_for_no_particular_moment_is_refused(bad):
    with pytest.raises(CommsError, match="armed"):
        arm_radio_command(bad)


@pytest.mark.parametrize("bad", [-1.0, float("nan"), None])
def test_a_hold_that_is_not_a_length_of_time_is_refused(bad):
    with pytest.raises(CommsError):
        link_layer_command(VEHICLES, ["iris_0=" + ANCHOR], 24, "/tmp/l.json",
                           hold_s=bad)


def test_the_gate_is_set_on_the_radio_and_on_nothing_else():
    """The one interface a swarm node has that is not the seam.

    A topic the runner published on would be a runner that can inject
    anything, and the static pass counts a second vehicle endpoint in a file
    as a bypass whatever the file does with it.
    """
    command = gate_radio_command([RELAY])
    assert command[:4] == ["ros2", "param", "set", "/link_layer"]
    assert command[4] == "radio_off"
    assert command[5] == "['" + RELAY + "']"


def test_lifting_the_gate_is_the_empty_list():
    assert gate_radio_command([])[5] == "[]"


def test_observing_a_gate_reads_it_back_off_the_radio():
    command = gated_radios_command()
    assert command[:4] == ["ros2", "param", "get", "/link_layer"]
    assert "--hide-type" in command


# ------------------------------------------------------ who is surveying
def test_every_vehicle_observes_unless_the_scenario_says_otherwise():
    # Eight of the nine scenarios. The mesh is scored on the traffic the
    # whole swarm makes, so an absent key cannot quietly narrow it.
    body = spec()
    assert body.observation_origins is None
    for vehicle in VEHICLES:
        assert body.observes(vehicle)
        command = router_command(vehicle, SPAWN, STATIONS[vehicle], body,
                                 "/tmp/x.json")
        assert "observations:=true" in " ".join(command).lower()


def test_a_scenario_can_name_the_surveying_origins():
    """queue_drain, and the reason it is the only one.

    Its claim is a queue depth, and the depth is 45 s at 5 Hz from the two
    origins the outage cuts off. With the anchor and the relay minting too the
    run makes 900 ids inside the window and the custody claim is about half of
    them.
    """
    body = comms_spec(block(observation_origins=[NEAR, FAR]), VEHICLES,
                      ALTITUDES)
    assert body.observation_origins == (NEAR, FAR)
    assert body.observes(NEAR) and body.observes(FAR)
    assert not body.observes(ANCHOR) and not body.observes(RELAY)
    quiet = " ".join(router_command(ANCHOR, SPAWN, STATIONS[ANCHOR], body,
                                    "/tmp/x.json")).lower()
    assert "observations:=false" in quiet


def test_the_record_says_who_was_surveying_either_way():
    assert spec().as_record()["observation_origins"] is None
    named = comms_spec(block(observation_origins=[NEAR]), VEHICLES, ALTITUDES)
    assert named.as_record()["observation_origins"] == [NEAR]


def test_a_run_where_nothing_observes_is_refused():
    with pytest.raises(CommsError) as caught:
        comms_spec(block(observation_origins=[]), VEHICLES, ALTITUDES)
    assert "no traffic to measure" in str(caught.value)


def test_an_origin_the_scenario_does_not_fly_is_refused():
    with pytest.raises(CommsError) as caught:
        comms_spec(block(observation_origins=[NEAR, "uav_9"]), VEHICLES,
                   ALTITUDES)
    assert "does not fly" in str(caught.value)


def test_the_same_origin_twice_is_refused():
    with pytest.raises(CommsError) as caught:
        comms_spec(block(observation_origins=[NEAR, NEAR]), VEHICLES,
                   ALTITUDES)
    assert "twice" in str(caught.value)

