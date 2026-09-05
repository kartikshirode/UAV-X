"""Chunk 3.4: what the runner needs to fly a swarm with its radio on.

`survey.py` did this for the survey and this is the same job for the mesh.
The runner has to start five more processes, put every vehicle on the frozen
station the topology assumes, and turn what those processes wrote on the way
down into the delivery numbers the run record carries. All of it is wiring,
and wiring is where a harness goes wrong quietly, so the decisions live here
where a test can reach them without a simulator.

What is decided here:

    comms_spec           the radio block of a scenario, refused if the roles,
                         stations or flags are not the ones the topology
                         claims
    station_node_command one vehicle's mission executor, holding a point
    router_command       one vehicle's router
    link_layer_command   the radio, with the model map the launcher wrote
    gcs_command          the ground station
    delivery_from_ledgers the five delivery fields, from the files the nodes
                         wrote, refused if they contradict each other

**The arithmetic is imported, not repeated.** `uavx_gcs.ledger` already
decides what a delivery ratio is and what a hop count counts, it imports no
ROS, and chunk 3.1 proved it with 19 tests. A second implementation here
would be a second answer to the question the communication row of the rubric
is scored on.

**Nothing here names a vehicle.** Every id arrives from the scenario, because
scripts/check_seam.sh counts the distinct vehicle endpoints a file names and
a file naming two is a bypass whatever it does with them.

**The ground station's id is imported too.** It is frozen in
`uavx_comms.params` and pinned there against architecture.md, and a string
literal here would be a second place for it to be spelled.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from uavx_comms import params as comms_params
from uavx_gcs import ledger as led

from uavx_sim.survey import SurveyError, home_of, ros_args

# The three roles a router will start in, as `uavx_comms.router_node` spells
# them. architecture.md section 6 gives each vehicle one in the common
# geometry table, and a scenario that names a fourth is a scenario naming a
# role no election could ever assign.
ROLE_NAMES = ("survey", "relay", "gcs_anchor")

# What a router ledger has to carry before the record will quote it.
ROUTER_LEDGER_KEYS = ("node", "generated", "generated_ids")

# And the ground station's.
GCS_LEDGER_KEYS = ("node", "delivered_ids", "delivered_hops_by_node",
                   "delivered_edges_by_node")

# The five fields the gate reads off the top of the record.
DELIVERY_KEYS = ("delivery_ratio", "delivery_ratio_by_node",
                 "delivered_hops_by_node", "delivered_edges_by_node",
                 "app_packets_sent_by_node", "app_packets_delivered_by_node")

# A ratio is delivered over generated and both come from counting the same
# identities, so the two ways of arriving at it agree exactly or one of them
# is wrong. The tolerance is float division noise and nothing else.
RATIO_TOLERANCE = 1e-9


class CommsError(ValueError):
    """A radio the runner refuses to fly, naming the reason."""


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


@dataclass(frozen=True)
class CommsSpec:
    """One scenario's radio, already checked against its vehicle list."""

    forwarding: bool
    elections_enabled: bool
    roles: Mapping[str, str]
    stations: Mapping[str, Tuple[float, float, float]]

    def role_of(self, vehicle_id: str) -> str:
        try:
            return self.roles[vehicle_id]
        except KeyError:
            raise CommsError(
                f"{vehicle_id} has no starting role. Every vehicle in the "
                f"common geometry table has one and a router cannot be "
                f"started without it") from None

    def station_of(self, vehicle_id: str) -> Tuple[float, float, float]:
        try:
            return self.stations[vehicle_id]
        except KeyError:
            raise CommsError(
                f"{vehicle_id} has no station. The topology in "
                f"architecture.md section 6 is a claim about where every "
                f"vehicle stands, and a vehicle standing somewhere else "
                f"makes the claim false without making the run fail") from None

    def as_record(self) -> dict:
        return {
            "forwarding": self.forwarding,
            "elections_enabled": self.elections_enabled,
            "roles": dict(sorted(self.roles.items())),
            "stations": {name: list(point)
                         for name, point in sorted(self.stations.items())},
        }


# --------------------------------------------------------------- the block
def comms_spec(raw: Mapping, vehicles: Sequence[str],
               altitudes: Mapping) -> Optional[CommsSpec]:
    """The comms block of a scenario, or None when the radio is off.

    None is a real answer: `survey_baseline` disables communications so the
    scenario measures the mission and nothing else. What is refused is a
    block that is present and incomplete, because every number under it is
    part of a topology claim and a default would be a claim nobody made.
    """
    block = raw.get("comms") if isinstance(raw, Mapping) else None
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise CommsError(f"comms is {block!r} and must be a mapping")
    enabled = block.get("enabled")
    if not isinstance(enabled, bool):
        raise CommsError(
            f"comms.enabled is {enabled!r} and must be true or false. A "
            f"scenario either carries traffic or does not, and the run "
            f"record says which")
    if not enabled:
        return None

    for key in ("forwarding", "elections_enabled"):
        if not isinstance(block.get(key), bool):
            raise CommsError(
                f"comms.{key} is {block.get(key)!r} and must be true or "
                f"false. direct_only differs from relay_required in exactly "
                f"one of these, so neither may be left to a default")

    roles = block.get("roles")
    if not isinstance(roles, Mapping):
        raise CommsError(f"comms.roles is {roles!r} and must be a mapping of "
                         f"vehicle id to starting role")
    for vehicle in vehicles:
        role = roles.get(vehicle)
        if role not in ROLE_NAMES:
            raise CommsError(
                f"comms.roles[{vehicle!r}] is {role!r}, not one of "
                f"{', '.join(ROLE_NAMES)}")
    extra = sorted(set(roles) - set(vehicles))
    if extra:
        raise CommsError(
            f"comms.roles names {', '.join(extra)}, which the scenario does "
            f"not fly. A role for an absent vehicle is a topology this run "
            f"cannot produce")

    stations = _stations(block.get("stations"), vehicles, altitudes)
    return CommsSpec(forwarding=bool(block["forwarding"]),
                     elections_enabled=bool(block["elections_enabled"]),
                     roles={v: str(roles[v]) for v in vehicles},
                     stations=stations)


def _stations(block, vehicles: Sequence[str],
              altitudes: Mapping) -> Dict[str, Tuple[float, float, float]]:
    """Where every vehicle stands, checked against the climb it was given.

    The altitude appears twice in a scenario, once as the layer the runner
    climbs to and once as the height of the station. They are compared here
    rather than in the node alone, so a disagreement costs a file read and
    not a bring-up.
    """
    if not isinstance(block, Mapping):
        raise CommsError(
            f"comms.stations is {block!r} and must be a mapping of vehicle "
            f"id to an x, y, z in the frozen frame")
    out: Dict[str, Tuple[float, float, float]] = {}
    for vehicle in vehicles:
        point = block.get(vehicle)
        if not isinstance(point, (list, tuple)) or len(point) != 3:
            raise CommsError(
                f"comms.stations[{vehicle!r}] is {point!r} and must be three "
                f"numbers")
        if not all(_finite(v) for v in point):
            raise CommsError(
                f"comms.stations[{vehicle!r}] is {point!r}; every component "
                f"has to be a finite number of metres")
        station = tuple(float(v) for v in point)
        layer = altitudes.get(vehicle) if isinstance(altitudes, Mapping) else None
        if layer is None:
            raise CommsError(
                f"{vehicle} has a station and no hover altitude. The runner "
                f"climbs to one and the executor holds the other, and with "
                f"only one of them present nothing compares the two")
        if not _finite(layer):
            raise CommsError(
                f"hover_altitudes_m[{vehicle!r}] is {layer!r}, not a number")
        if abs(station[2] - float(layer)) > 1e-6:
            raise CommsError(
                f"{vehicle} climbs to {float(layer)} m and holds station at "
                f"{station[2]} m. One of the two is what the run flies and "
                f"the record cannot say which")
        out[vehicle] = station
    extra = sorted(set(block) - set(vehicles))
    if extra:
        raise CommsError(
            f"comms.stations names {', '.join(extra)}, which the scenario "
            f"does not fly")
    return out


# ------------------------------------------------------------ the commands
def station_node_command(vehicle_id: str, spawn_row, station,
                         vehicles: Sequence[str]) -> list:
    """`ros2 run uavx_mission mission_executor`, holding one point.

    `observations` is false. The router on this vehicle mints the identities
    now, and identity is `(origin_id, sequence)`, so two processes on one
    vehicle both counting from zero would produce colliding ids. Delivered
    once is a comparison of those ids, which means the collision would not
    look like a fault. It would look like a delivery.
    """
    parameters = {
        "vehicle_id": vehicle_id,
        "swarm_vehicles": list(vehicles),
        "survey_altitude_m": float(station[2]),
        "station_enu": [float(v) for v in station],
        "observations": False,
        "home_enu": list(home_of(spawn_row)),
    }
    return (["ros2", "run", "uavx_mission", "mission_executor"]
            + ros_args(parameters, namespace=vehicle_id))


def router_command(vehicle_id: str, spawn_row, station, spec: CommsSpec,
                   ledger_path) -> list:
    """`ros2 run uavx_comms router` for one vehicle.

    `use_sim_time` is on, and that is the decision worth naming. Every `_s`
    in the run record is ROS simulated time, and the frozen periods this
    router runs on are the same clock: a router on wall time would generate
    `duration_s` times the frozen rate of wall seconds and the record would
    divide by simulated ones. The two differ by about a fifth on this stack,
    which is a fifth added to every denominator in the communication row.
    """
    parameters = {
        "use_sim_time": True,
        "vehicle_id": vehicle_id,
        "position_enu": [float(v) for v in station],
        "home_enu": list(home_of(spawn_row)),
        "role": spec.role_of(vehicle_id),
        "forwarding": spec.forwarding,
        "elections_enabled": spec.elections_enabled,
        "observations": True,
        "ledger_path": str(ledger_path),
    }
    return (["ros2", "run", "uavx_comms", "router"]
            + ros_args(parameters, namespace=vehicle_id))


def link_layer_command(vehicles: Sequence[str], model_entries: Sequence[str],
                       seed: int, ledger_path) -> list:
    """`ros2 run uavx_comms link_layer`, the one radio for the whole swarm.

    No namespace. The graph names it `/link_layer`, which is the exact name
    scripts/seam_manifests.json matches an outside process by, and substring
    matching was removed there because a node called `uav_2/link_layer_helper`
    used to inherit the exemption that lets this one read ground truth.
    """
    if not vehicles:
        raise CommsError("the radio was given no vehicles")
    if not model_entries:
        raise CommsError(
            "the radio needs the launcher's model map. Without it no gazebo "
            "model is known to be a vehicle, and every link would be scored "
            "at an invented distance")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise CommsError(
            f"the seed is {seed!r} and must be an integer. The fade band is "
            f"random and a run nobody can replay is not evidence")
    parameters = {
        "use_sim_time": True,
        "vehicles": list(vehicles),
        "model_map": list(model_entries),
        "seed": int(seed),
        "ledger_path": str(ledger_path),
    }
    return (["ros2", "run", "uavx_comms", "link_layer"]
            + ros_args(parameters))


def gcs_command(spec: CommsSpec, ledger_path) -> list:
    """`ros2 run uavx_gcs gcs_node`, in the namespace the manifest expects."""
    parameters = {
        "use_sim_time": True,
        "forwarding": spec.forwarding,
        "ledger_path": str(ledger_path),
    }
    return (["ros2", "run", "uavx_gcs", "gcs_node"]
            + ros_args(parameters, namespace=comms_params.GCS_ID))


# -------------------------------------------------------------- the ledgers
def read_ledger(path, required: Sequence[str]) -> dict:
    """One node's file, or a refusal naming what is missing from it.

    A node that did not write its ledger is a node whose counters are gone,
    and the delivery ratio computed without it would be a ratio over the
    nodes that happened to shut down cleanly.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CommsError(
            f"no ledger at {path}. The node either never started or died "
            f"before it could write what it counted") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise CommsError(f"{path} is not JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise CommsError(f"{path} does not hold an object")
    missing = [key for key in required if key not in document]
    if missing:
        raise CommsError(f"{path} has no {', '.join(missing)}")
    return document


def delivery_from_ledgers(router_ledgers: Sequence[Mapping],
                          gcs_ledger: Mapping) -> dict:
    """The five delivery fields, from the origins and the destination.

    The denominator comes from the origins and never from the destination.
    A ground station that supplies its own denominator is always satisfied,
    which is the shape of a delivery ratio that reads 1.0 in every run
    including the ones where the relay was dead.
    """
    if not router_ledgers:
        raise CommsError(
            "no router wrote a ledger, so nothing said what it generated and "
            "every ratio would be a division by nothing")
    generated = led.generated_by_node(router_ledgers)
    if not generated:
        raise CommsError(
            "no router ledger said what it generated. Ratios computed "
            "against an empty denominator read as a perfect score")

    for ledger in router_ledgers:
        node = ledger.get("node")
        ids = list(ledger.get("generated_ids") or [])
        if len(set(ids)) != len(ids):
            raise CommsError(
                f"{node} generated the same identity twice. Delivered once "
                f"is a set comparison, so a repeated id makes the numerator "
                f"and the denominator disagree about what a packet is")
        count = ledger.get("generated")
        if isinstance(count, int) and not isinstance(count, bool):
            if count != len(ids):
                raise CommsError(
                    f"{node} counted {count} observations and listed "
                    f"{len(ids)}. The record would carry one and the ratio "
                    f"would be computed from the other")

    delivered = list(gcs_ledger.get("delivered_ids") or [])
    if len(set(delivered)) != len(delivered):
        raise CommsError(
            "the ground station listed the same delivered identity twice. "
            "It deduplicates by identity on acceptance, so a repeat here "
            "means the list is arrivals rather than deliveries")
    arrived = set(delivered)

    every = [i for ids in generated.values() for i in ids]
    unexpected = sorted(arrived - set(every))
    if unexpected:
        raise CommsError(
            f"the ground station accepted {len(unexpected)} identity or "
            f"identities nobody generated, the first being "
            f"{unexpected[0]!r}. A numerator with ids outside the "
            f"denominator is not a fraction of anything")

    sent = {node: len(ids) for node, ids in generated.items()}
    delivered_by_node = {node: len(set(ids) & arrived)
                         for node, ids in generated.items()}
    ratio_by_node = led.ratio_by_node(generated, arrived)
    for node, ratio in ratio_by_node.items():
        if sent[node] and abs(ratio - delivered_by_node[node] / sent[node]) > RATIO_TOLERANCE:
            raise CommsError(
                f"{node}'s ratio {ratio} is not {delivered_by_node[node]} "
                f"over {sent[node]}")

    hops = _by_node_numbers(gcs_ledger, "delivered_hops_by_node", generated)
    edges = _by_node_numbers(gcs_ledger, "delivered_edges_by_node", generated)
    for node in sorted(set(hops) & set(edges)):
        # router.py freezes the relationship: hop_count counts forwarders and
        # len(path) - 1 counts edges, so the same delivery is one apart in
        # the two. Reported separately and checked against each other, so
        # neither can quietly be used in place of the other.
        if edges[node] < hops[node]:
            raise CommsError(
                f"{node} delivered over {edges[node]} edge or edges through "
                f"{hops[node]} forwarders. A path cannot have fewer edges "
                f"than it has forwarders on it")

    return {
        "delivery_ratio": led.delivery_ratio(every, arrived),
        "delivery_ratio_by_node": ratio_by_node,
        "delivered_hops_by_node": hops,
        "delivered_edges_by_node": edges,
        "app_packets_sent_by_node": dict(sorted(sent.items())),
        "app_packets_delivered_by_node": dict(sorted(delivered_by_node.items())),
    }


def _by_node_numbers(ledger: Mapping, key: str,
                     generated: Mapping) -> Dict[str, float]:
    """One per node block of the ground station's file, checked.

    A row for an origin nobody generated is the destination inventing a
    sender, and the whole point of taking the denominator from the origins is
    that it cannot.
    """
    block = ledger.get(key)
    if not isinstance(block, Mapping):
        raise CommsError(f"the ground station's {key} is {block!r}")
    out: Dict[str, float] = {}
    for node, value in block.items():
        if node not in generated:
            raise CommsError(
                f"{key} names {node!r}, which generated nothing in this run")
        if not _finite(value) or float(value) < 0:
            raise CommsError(f"{key}[{node!r}] is {value!r}, not a count")
        out[str(node)] = float(value)
    return dict(sorted(out.items()))


def collector_command_no_survey(run_id: str, scenario_path: str,
                                model_entries: Sequence[str],
                                min_separation_m: float) -> list:
    """The collector for a run that carries traffic and surveys nothing.

    Every coverage parameter is left at its NaN default, which is how the
    node says this run has no box. It still watches separation, which is the
    one thing every scenario in Stage 1 is measured on.
    """
    if not run_id or not scenario_path:
        raise SurveyError("the collector needs the run id and scenario path "
                          "it reports for")
    if not model_entries:
        raise SurveyError("the collector needs at least one model to watch")
    if not _finite(min_separation_m) or min_separation_m <= 0:
        raise SurveyError(f"min_separation_m is {min_separation_m!r}")
    parameters = {
        "use_sim_time": True,
        "run_id": run_id,
        "scenario_path": scenario_path,
        "min_separation_m": float(min_separation_m),
        "model_map": list(model_entries),
    }
    return (["ros2", "run", "uavx_eval", "metrics_collector"]
            + ros_args(parameters))
