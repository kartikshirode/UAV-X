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

from uavx_sim.comms import (CommsError, comms_spec, delivery_from_ledgers,
                            gcs_command, link_layer_command, read_ledger,
                            router_command, station_node_command)

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
    stations = {k: list(v) for k, v in STATIONS.items()}
    del stations[RELAY]
    with pytest.raises(CommsError, match=RELAY):
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
