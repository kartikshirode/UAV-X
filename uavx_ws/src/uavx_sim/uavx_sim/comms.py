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
    role_manager_command one vehicle's role executive, for a run with an
                         election in it
    link_layer_command   the radio, with the model map the launcher wrote
    blackout_hold_s      how long a gated radio stays gated, as a duration
    gate_radio_command   the parameter set that injects a blackout
    gated_radios_command the parameter get that observes one landed
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple

from uavx_comms import params as comms_params
from uavx_gcs import ledger as led
from uavx_mission import frames

from uavx_sim import work
from uavx_sim.survey import (SurveyError, home_of, mirrored_of,
                            ros_args, strip_plans)

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

# What a role manager has to have written before the record will say a role
# moved. Chunk 4.2: the three flags are the recovery, and a file without them
# is a vehicle that started and never answered.
ROLE_LEDGER_KEYS = ("node", "moved", "released", "returned_to_station")

# The five fields the gate reads off the top of the record.
DELIVERY_KEYS = ("delivery_ratio", "delivery_ratio_by_node",
                 "delivered_hops_by_node", "delivered_edges_by_node",
                 "app_packets_sent_by_node", "app_packets_delivered_by_node")

# The radio's node name. seam_manifests.json matches /link_layer by exact
# name, and this is the other place that name is written: the runner injects a
# blackout by setting a parameter on it.
LINK_LAYER_NODE = "/link_layer"

# The two events a scenario can inject that this runner carries out.
KILL = "kill"
COMMS_BLACKOUT = "comms_blackout"

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
    # The vehicles that mint observations, or None for all of them.
    #
    # None is what every scenario but queue_drain wants: traffic from every
    # aircraft is the load the mesh is scored on. queue_drain is the one run
    # whose claim is about a queue depth, and that arithmetic is written for
    # the two surveying origins the outage cuts off. With the anchor and the
    # relay minting as well, the run produces twice the outage ids the custody
    # claim is about.
    observation_origins: Optional[Tuple[str, ...]] = None
    # The straight lines the encounter pair fly, empty for every other
    # scenario. Held here because the runner needs three things from them and
    # gets them in three different places: where to fly each vehicle before
    # the run starts, what to launch its executor with, and what to compare
    # its finishing position against.
    tracks: Mapping[str, work.Track] = field(default_factory=dict)
    # The lane path each surveying vehicle flies, empty for the eight
    # scenarios that survey nothing or that survey with the radio off. Held
    # for the same three reasons the tracks are: where to fly it before the
    # run starts, which end to launch its executor pointing at, and who the
    # box was split between.
    strips: Mapping[str, object] = field(default_factory=dict)
    # Whether consecutive strips are flown from opposite ends. Carried beside
    # the strips because a vehicle that inherits a neighbour's work rebuilds
    # the neighbour's path, and which end it started from is half of it.
    mirrored: bool = False

    def track_of(self, vehicle_id: str):
        return self.tracks.get(vehicle_id)

    def survey_of(self, vehicle_id: str):
        return self.strips.get(vehicle_id)

    @property
    def surveyors(self) -> Tuple[str, ...]:
        """The vehicles the survey box is split between, in plan order."""
        return tuple(sorted(self.strips))

    def observes(self, vehicle_id: str) -> bool:
        if self.observation_origins is None:
            return True
        return vehicle_id in self.observation_origins

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
            # Named in the record either way. A reader working out why a run
            # generated what it did should not have to know that an absent
            # key means everybody.
            "observation_origins": (None if self.observation_origins is None
                                    else list(self.observation_origins)),
            "tracks": {name: line.as_record()
                       for name, line in sorted(self.tracks.items())},
            "strips": {name: plan.as_record()
                       for name, plan in sorted(self.strips.items())},
            "mirrored": self.mirrored,
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

    # Who holds a point, and who is busy elsewhere. Weeks 2 and 3 had one
    # answer per scenario; mission_integrated has two vehicles surveying while
    # two hold the chain up, and encounter has both of its vehicles flying
    # lines. work.assignments is what refuses a vehicle with no job and a
    # vehicle with two, and it runs before the points are read so a
    # contradiction is reported as a contradiction rather than as a missing
    # station.
    jobs = work.assignments(raw, vehicles, altitudes)
    held = tuple(v for v in vehicles if jobs[v] == work.STATION)
    tracks = work.tracks_of(raw, vehicles, altitudes)
    stations = _stations(block.get("stations"), vehicles, altitudes, held)
    # A track vehicle's station is the head of its own line. The ingress flies
    # every vehicle to a station and refuses to start the run until they are
    # all on one, so a vehicle with a track and no station would hold the run
    # at the gate until the deadline and fail there. The head of the line is
    # also the only place it can be when the run starts, or the two legs are
    # no longer the same length and the pair no longer arrive together.
    for vehicle, line in tracks.items():
        stations[vehicle] = line.start
    # And a surveying vehicle's is the head of its first lane, for the same
    # reason. One rule covers all three kinds of work: the ingress flies every
    # vehicle to the start of whatever it has been given to do. mission_integrated
    # is where the rule was missing, and the run died at the gate with
    # "uav_3 has no station" after two healthy aircraft had already climbed.
    strips = strip_plans(raw, [v for v in vehicles if jobs[v] == work.SURVEY],
                         altitudes)
    for vehicle, plan in strips.items():
        stations[vehicle] = plan.head
    return CommsSpec(forwarding=bool(block["forwarding"]),
                     elections_enabled=bool(block["elections_enabled"]),
                     roles={v: str(roles[v]) for v in vehicles},
                     stations=stations,
                     observation_origins=_origins(
                         block.get("observation_origins"), vehicles),
                     tracks=tracks,
                     strips=strips,
                     mirrored=mirrored_of(raw))


def _origins(block, vehicles: Sequence[str]) -> Optional[Tuple[str, ...]]:
    """Which vehicles mint observations, or None for every one of them.

    Absent is the answer for eight of the nine scenarios: the mesh is scored
    on the traffic the whole swarm makes. queue_drain names two, because its
    claim is a queue depth and the depth is arithmetic over the origins the
    outage cuts off.
    """
    if block is None:
        return None
    if not isinstance(block, (list, tuple)) or not block:
        raise CommsError(
            f"comms.observation_origins is {block!r}. It is a list of the "
            f"vehicles that survey, and a run where nothing observes carries "
            f"no traffic to measure")
    named = [str(v) for v in block]
    unknown = sorted(set(named) - set(vehicles))
    if unknown:
        raise CommsError(
            f"comms.observation_origins names {', '.join(unknown)}, which "
            f"the scenario does not fly")
    if len(set(named)) != len(named):
        raise CommsError(
            f"comms.observation_origins names a vehicle twice: {named}")
    return tuple(sorted(named))


def _stations(block, vehicles: Sequence[str], altitudes: Mapping,
              held: Sequence[str]) -> Dict[str, Tuple[float, float, float]]:
    """Where the station-keeping vehicles stand, against the climb they got.

    The altitude appears twice in a scenario, once as the layer the runner
    climbs to and once as the height of the station. They are compared here
    rather than in the node alone, so a disagreement costs a file read and
    not a bring-up.

    `held` is the vehicles whose work is a point. The rest are flying a track
    or a survey strip and a station for one of them was refused before this
    function was reached.
    """
    if not isinstance(block, Mapping):
        raise CommsError(
            f"comms.stations is {block!r} and must be a mapping of vehicle "
            f"id to an x, y, z in the frozen frame")
    out: Dict[str, Tuple[float, float, float]] = {}
    for vehicle in held:
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


def stations_of(spec: Optional["CommsSpec"], vehicles: Sequence[str]):
    """The vehicles this run has to fly to a point before the clock starts.

    An empty answer is a real one. `encounter` gives both of its vehicles a
    track, so nothing holds a station and the ingress gate has nothing to
    wait for.
    """
    if spec is None:
        return ()
    return tuple(v for v in vehicles if v in spec.stations)


# ------------------------------------------------------------- the ingress
def station_gap(local_ned, home, station) -> float:
    """How far a vehicle is from its station, in the frozen frame.

    The conversion is imported and not repeated. `uavx_mission.frames` is the
    one module that owns it and it records what goes wrong when a home is
    subtracted in the wrong frame: the answer still looks like a position,
    and it is wrong by exactly the distance the vehicle is from the world
    origin, which reads correctly for the one vehicle that spawned there.
    """
    here = frames.px4_to_frozen(tuple(float(v) for v in local_ned),
                                tuple(float(v) for v in home))
    return math.dist(here, tuple(float(v) for v in station))


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


def track_node_command(vehicle_id: str, spawn_row, track,
                       vehicles: Sequence[str]) -> list:
    """`ros2 run uavx_mission mission_executor`, flying one straight line.

    The third kind of executor, beside the survey and the station. It carries
    the same `observations: false` for the same reason: the router on this
    vehicle mints the identities, and identity is `(origin_id, sequence)`, so
    two processes on one aircraft counting from zero would collide.

    `track_epoch_s` is not here. Nothing knows where the scenario's zero sits
    until the ingress has finished, so it is set on this node during the run
    and the vehicle waits at the head of its line until it arrives.

    `use_sim_time` is on, and this is the only mission executor that has it.
    The epoch handed to this node is an absolute simulated time, so a node
    reading a wall clock would compare two different clocks and start its leg
    at whatever the real time factor happened to be. It matters here and only
    here because starting together is what makes the pair arrive together:
    the survey and station executors have nothing to be on time for.

    Not switched on for the other two in this chunk. It would change the
    timestamp every mission executor puts on its PX4 setpoints and the rate
    its offboard heartbeat runs at, in all nine scenarios, and that is a
    change to prove with flights rather than to make in passing.
    """
    parameters = {
        "use_sim_time": True,
        "vehicle_id": vehicle_id,
        "swarm_vehicles": list(vehicles),
        "survey_altitude_m": float(track.start[2]),
        "track_start_enu": [float(v) for v in track.start],
        "track_end_enu": [float(v) for v in track.end],
        "track_start_s": float(track.start_s),
        "track_speed_mps": float(track.speed_mps),
        "observations": False,
        "home_enu": list(home_of(spawn_row)),
    }
    return (["ros2", "run", "uavx_mission", "mission_executor"]
            + ros_args(parameters, namespace=vehicle_id))


def track_epoch_command(vehicle_id: str, epoch_s: float) -> list:
    """`ros2 param set` telling one executor where the scenario's zero is.

    Absolute simulated seconds, because that is the clock the node reads.
    Both track vehicles are given the same number and neither flies until it
    arrives, which is what makes them start together. Two legs of the same
    length started together is the whole reason neither of them reaches the
    crossing first, and a pair that started a second apart would pass safely
    with the rule doing nothing.
    """
    if not _finite(epoch_s) or epoch_s <= 0:
        raise CommsError(
            f"the scenario zero is {epoch_s!r}. It is a positive simulated "
            f"time, and zero is the value that means the run has not started")
    return ["ros2", "param", "set", f"/{vehicle_id}/mission_executor",
            "track_epoch_s", f"{float(epoch_s):.6f}"]


def survey_epoch_command(vehicle_id: str, epoch_s: float) -> list:
    """The same call for a surveying vehicle, and for the same reason.

    A survey with a pace has a start time in scenario seconds, and scenario
    zero is not known until the ingress ends. Until this arrives the vehicle
    holds the head of its first lane, which is where the ingress put it.
    """
    if not _finite(epoch_s) or epoch_s <= 0:
        raise CommsError(
            f"the scenario zero is {epoch_s!r}. It is a positive simulated "
            f"time, and zero is the value that means the run has not started")
    return ["ros2", "param", "set", f"/{vehicle_id}/mission_executor",
            "survey_epoch_s", f"{float(epoch_s):.6f}"]


def router_command(vehicle_id: str, spawn_row, station, spec: CommsSpec,
                   ledger_path, yield_enabled: bool = True) -> list:
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
        "observations": spec.observes(vehicle_id),
        # architecture.md section 5. False is the encounter_noyield control,
        # which is the same flight with the rule switched off. It reaches the
        # router rather than the executor because the router is the node that
        # decodes HELLO and therefore the node that decides.
        "yield_enabled": bool(yield_enabled),
        "ledger_path": str(ledger_path),
    }
    return (["ros2", "run", "uavx_comms", "router"]
            + ros_args(parameters, namespace=vehicle_id))


def role_managers_of(spec: Optional["CommsSpec"],
                     vehicles: Sequence[str]) -> Tuple[str, ...]:
    """The vehicles that run a role executive in this scenario.

    Exactly the runs with an election in them. `queue_drain` and the
    encounter pair keep the radio and forbid the election on purpose, so no
    role is ever granted and a role manager there would be a node holding a
    tx endpoint it never uses. scripts/seam_manifests.json names the same
    three scenarios, and this is the condition that has to agree with it.
    """
    if spec is None or not spec.elections_enabled:
        return ()
    return tuple(vehicles)


def role_manager_command(vehicle_id: str, spawn_row, spec: CommsSpec,
                         ledger_path, station=None) -> list:
    """`ros2 run uavx_roles role_manager` for one vehicle.

    The station is omitted rather than passed as three NaNs for a vehicle
    whose work is a survey strip. `ros2 run -p` parses its values as YAML and
    `nan` there is the string, which rclpy refuses against a declared double
    array after the simulator is already up. The node declares the unset
    default itself, so leaving the parameter out says the same thing in a
    spelling the launcher can express.
    """
    parameters = {
        "use_sim_time": True,
        "vehicle_id": vehicle_id,
        "home_enu": list(home_of(spawn_row)),
        "role": spec.role_of(vehicle_id),
        "ledger_path": str(ledger_path),
    }
    if spec.surveyors:
        # Who the box is split between, so this vehicle can work out whether
        # it is the one to take over an elected relay's strip. Left out for a
        # scenario that surveys nothing, where the node's own empty default
        # says the same thing and the launcher cannot render an empty list.
        parameters["survey_peers"] = list(spec.surveyors)
    if station is not None:
        if len(station) != 3 or not all(_finite(v) for v in station):
            raise CommsError(
                f"{vehicle_id} was given the station {station!r}, which is "
                f"not three finite numbers of metres")
        parameters["station_enu"] = [float(v) for v in station]
    return (["ros2", "run", "uavx_roles", "role_manager"]
            + ros_args(parameters, namespace=vehicle_id))


def blackout_hold_s(raw: Mapping) -> float:
    """How long a radio gated by this scenario stays gated.

    A duration and not a moment. The radio counts in simulated seconds since
    the simulator came up and the scenario counts from its own zero, and the
    difference between the two is the bring-up, which nothing knows when the
    radio is launched. A duration means the same thing in both clocks.

    Zero when the scenario gates nothing, which is also what a scenario that
    starts in a blackout and never lifts it would want.
    """
    events = raw.get("injected_events") if isinstance(raw, Mapping) else None
    holds = []
    for event in events or ():
        if not isinstance(event, Mapping) or event.get("type") != COMMS_BLACKOUT:
            continue
        at = event.get("at_s")
        restore = event.get("restore_at_s")
        if not _finite(at) or not _finite(restore):
            raise CommsError(
                f"a comms_blackout runs from {at!r} to {restore!r}, and both "
                f"have to be times for the radio to know how long to hold")
        holds.append(float(restore) - float(at))
    if not holds:
        return 0.0
    if len(set(holds)) > 1:
        raise CommsError(
            f"this scenario gates radios for {sorted(set(holds))} seconds. "
            f"One hold is passed to the radio, so two would mean one of the "
            f"blackouts ends at a time nothing chose")
    return holds[0]


def blackout_nodes(raw: Mapping) -> Tuple[str, ...]:
    """The vehicles this scenario schedules a blackout for.

    Read at launch and passed to the radio beside the hold, because the radio
    needs to know who before it can be handed a bare time. The alternative was
    two parameter sets at fire time whose order decided whether the gate landed
    when it was asked for or the moment the first one arrived.
    """
    events = raw.get("injected_events") if isinstance(raw, Mapping) else None
    named = []
    for event in events or ():
        if not isinstance(event, Mapping) or event.get("type") != COMMS_BLACKOUT:
            continue
        target = event.get("target")
        if not isinstance(target, str) or not target.strip():
            raise CommsError(
                f"a comms_blackout in this scenario is aimed at {target!r}. A "
                f"fault needs a vehicle or it lands nowhere")
        named.append(target.strip())
    return tuple(sorted(set(named)))


def blackout_at_s(raw: Mapping) -> Optional[float]:
    """When this scenario's blackout is due, in scenario seconds.

    None when nothing is gated. Every blackout in one scenario has to be due
    at the same moment for the same reason the hold does: the radio is armed
    once, with one time, and two would mean one of the faults lands at a
    moment nothing chose.
    """
    events = raw.get("injected_events") if isinstance(raw, Mapping) else None
    times = set()
    for event in events or ():
        if not isinstance(event, Mapping) or event.get("type") != COMMS_BLACKOUT:
            continue
        at = event.get("at_s")
        if not _finite(at):
            raise CommsError(
                f"a comms_blackout is due at {at!r}, which is not a time")
        times.add(float(at))
    if not times:
        return None
    if len(times) > 1:
        raise CommsError(
            f"this scenario gates radios at {sorted(times)}. One instant is "
            f"armed on the radio, so two would mean one of the blackouts "
            f"starts when a parameter call happened to arrive")
    return times.pop()


def arm_radio_command(at_s: float) -> list:
    """`ros2 param set` handing the radio the instant its gate is due.

    Absolute simulated seconds and not scenario relative. Both this process
    and the radio read `/clock`; only the runner knows where the scenario's
    zero sits in it, so the conversion happens here and the radio is given a
    time it can compare against its own clock with no arithmetic.

    One call, sent ahead of the fault. That is the whole point: a `ros2 param
    set` spends a second or two finding the node over DDS, and a gate applied
    when the call returns lands whenever the network felt like it. A gate
    given a time lands on the time.
    """
    if not _finite(at_s) or at_s <= 0:
        raise CommsError(
            f"the radio was armed for t={at_s!r}. A gate is due at a positive "
            f"simulated time, and zero is the value that means unarmed")
    return ["ros2", "param", "set", LINK_LAYER_NODE, "radio_off_at_s",
            f"{float(at_s):.6f}"]


def gate_radio_command(vehicles: Sequence[str]) -> list:
    """`ros2 param set` on the radio, gating exactly these vehicles.

    The parameter service, which is the one interface a swarm node has that
    is not the seam, and which seam_manifests.json allows on every node
    because rclpy gives every node one. The alternative was a topic the
    runner publishes on, and a runner that can publish to a node is a runner
    that can inject anything.

    An empty list lifts the gate. It is never used by a scenario, because the
    radio lifts its own gate after the frozen hold, and it is the honest
    spelling of the parameter's other value.
    """
    inside = ", ".join(f"'{str(v)}'" for v in vehicles)
    return ["ros2", "param", "set", LINK_LAYER_NODE, "radio_off",
            "[" + inside + "]"]


def gated_radios_command() -> list:
    """`ros2 param get` on the radio: which vehicles it says are gated.

    The other half of injecting a fault, and the half that matters. The
    injector's contract is that the effect is observed on the thing it was
    applied to rather than inferred from the request having been sent, and
    for a blackout the thing is the radio.
    """
    return ["ros2", "param", "get", LINK_LAYER_NODE, "radio_off",
            "--hide-type"]


def link_layer_command(vehicles: Sequence[str], model_entries: Sequence[str],
                       seed: int, ledger_path, hold_s: float = 0.0,
                       gated: Sequence[str] = ()) -> list:
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
    if not _finite(hold_s) or hold_s < 0:
        raise CommsError(
            f"the blackout hold is {hold_s!r}. A radio holds a gate for a "
            f"length of time, or for the rest of the run if that is zero")
    unknown = sorted(set(gated) - set(vehicles))
    if unknown:
        raise CommsError(
            f"a blackout is scheduled for {', '.join(unknown)}, which this "
            f"run does not fly")
    parameters = {
        "use_sim_time": True,
        "vehicles": list(vehicles),
        "model_map": list(model_entries),
        "seed": int(seed),
        "blackout_hold_s": float(hold_s),
        "ledger_path": str(ledger_path),
    }
    # Only when there is one. The node declares the empty default itself, and
    # _yaml_scalar has no rendering for an empty list.
    if gated:
        parameters["blackout_nodes"] = list(gated)
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
