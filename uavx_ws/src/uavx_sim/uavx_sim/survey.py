"""Chunk 2.4: what the runner needs to fly a survey, with no ROS in it.

The runner brings four vehicles to a hover and, until this chunk, that was
the whole flight. A survey adds three things: a mission executor per vehicle,
the metrics collector watching ground truth, and the coverage figure those
two produce landing in the run record. All three are wiring, and wiring is
where a harness goes wrong quietly, so the decisions are made here where a
test can reach them and scenario_runner.py only carries them out.

What is decided here:

    survey_spec           the frozen box, read off the scenario and refused if
                          any dimension is missing, because a default box is a
                          survey area nobody chose being scored as if somebody
                          had
    model_map             which gazebo model is which vehicle, read off the
                          launcher's spawn manifest rather than guessed from
                          the spawn order
    mission_node_command  the exact ros2 invocation of one vehicle's executor,
                          with its home taken from the same manifest
    collector_command     the exact ros2 invocation of the collector
    coverage_from_payload the four coverage fields the record carries, taken
                          off the collector's final payload and refused if
                          they do not agree with each other

Nothing here names a vehicle. Every id arrives as an argument, because
scripts/check_seam.sh counts the distinct vehicle endpoints a file names and
a file that names two is a bypass whatever it does with them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

# The six numbers that describe a survey box, in the names
# uavx_eval.metrics_collector declares them under. architecture.md section 6
# freezes their values per scenario; this module freezes only their names.
SURVEY_KEYS = ("origin_x", "origin_y", "width_m", "height_m", "cell_m",
               "footprint_m")

# What the record carries about coverage, and what the collector's payload
# has to supply. The gate asserts on the first two and reads them at the top
# level of the record, so that is where the runner puts them.
COVERAGE_KEYS = ("coverage_fraction", "coverage_source",
                 "coverage_cells_total", "coverage_cells_seen")

# PX4's horizontal speed limit in offboard position control. architecture.md
# section 6 names a cruise speed for the survey and this is the parameter that
# makes the vehicle honour it.
CRUISE_SPEED_PARAM = "MPC_XY_VEL_MAX"

TILING_TOLERANCE = 1e-6


class SurveyError(ValueError):
    """A survey the runner refuses to fly, naming the reason."""


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


@dataclass(frozen=True)
class SurveySpec:
    """One survey box and the speed it is flown at."""

    origin_x: float
    origin_y: float
    width_m: float
    height_m: float
    cell_m: float
    footprint_m: float
    cruise_speed_mps: Optional[float]

    @property
    def cell_count(self) -> int:
        return (int(round(self.width_m / self.cell_m))
                * int(round(self.height_m / self.cell_m)))

    def collector_parameters(self) -> dict:
        """The coverage block as the collector node declares it."""
        return {f"coverage.{key}": float(getattr(self, key))
                for key in SURVEY_KEYS}

    def mission_parameters(self) -> dict:
        """The box as the mission executor node declares it."""
        return {
            "area_sw_m": [float(self.origin_x), float(self.origin_y)],
            "area_width_m": float(self.width_m),
            "area_height_m": float(self.height_m),
            "cell_m": float(self.cell_m),
            "sensor_radius_m": float(self.footprint_m),
        }

    def as_record(self) -> dict:
        out = {key: float(getattr(self, key)) for key in SURVEY_KEYS}
        out["cell_count"] = self.cell_count
        out["cruise_speed_mps"] = self.cruise_speed_mps
        return out


def survey_spec(raw: Mapping) -> Optional[SurveySpec]:
    """The survey block of a scenario, or None when the scenario flies none.

    None is a real answer: harness_check hovers and surveys nothing. A block
    that is present and incomplete is not, because the cell count is the
    denominator of every coverage figure and a box with a dimension missing
    has no cell count.
    """
    block = raw.get("survey") if isinstance(raw, Mapping) else None
    if block is None:
        return None
    if not isinstance(block, Mapping):
        raise SurveyError(f"survey is {block!r} and must be a mapping of the "
                          f"six box dimensions")
    missing = [key for key in SURVEY_KEYS if key not in block]
    if missing:
        raise SurveyError(
            f"survey is missing {', '.join(missing)}. Every dimension of the "
            f"box is frozen in architecture.md section 6 and none of them has "
            f"a default")
    values = {}
    for key in SURVEY_KEYS:
        value = block[key]
        if not _finite(value):
            raise SurveyError(f"survey.{key} is {value!r}, not a finite number")
        if key not in ("origin_x", "origin_y") and value <= 0:
            raise SurveyError(f"survey.{key} is {value!r} and must be positive")
        values[key] = float(value)
    for axis in ("width_m", "height_m"):
        cells = values[axis] / values["cell_m"]
        if abs(cells - round(cells)) > TILING_TOLERANCE:
            raise SurveyError(
                f"a {values['cell_m']} m cell does not divide the "
                f"{values[axis]} m {axis}, so the box has no cell count and "
                f"no fraction of it means anything")
    speed = block.get("cruise_speed_mps")
    if speed is not None:
        if not _finite(speed) or speed <= 0:
            raise SurveyError(
                f"survey.cruise_speed_mps is {speed!r} and must be a positive "
                f"number of metres per second")
        speed = float(speed)
    return SurveySpec(cruise_speed_mps=speed, **values)


# ------------------------------------------------------------- the manifest
def model_map(manifest: Mapping) -> list:
    """`<model>_<instance>=<vehicle_id>` for every vehicle the launcher placed.

    Read off runs/.launcher-spawn.json, which scripts/sitl_multi.sh writes as
    it spawns. The gazebo model name is `<model>_<instance>` because that is
    the `--model-name` the launcher passes to `gz model`, and the vehicle id
    beside it is the one PX4 was given as its namespace. Deriving the pairing
    from the spawn order instead would agree with this until the launcher
    changed, which is the drift the manifest exists to prevent.
    """
    if not isinstance(manifest, Mapping):
        raise SurveyError("the spawn manifest is not a mapping")
    model = manifest.get("model")
    if not isinstance(model, str) or not model:
        raise SurveyError("the spawn manifest names no model, so no gazebo "
                          "model name can be tied to a vehicle")
    rows = manifest.get("vehicles")
    if not isinstance(rows, list) or not rows:
        raise SurveyError("the spawn manifest lists no vehicles")
    entries = []
    for row in rows:
        instance = row.get("instance") if isinstance(row, Mapping) else None
        vehicle = row.get("vehicle_id") if isinstance(row, Mapping) else None
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise SurveyError(f"spawn row {row!r} has no integer instance")
        if not isinstance(vehicle, str) or not vehicle:
            raise SurveyError(f"spawn row {row!r} has no vehicle_id")
        entries.append((instance, f"{model}_{instance}={vehicle}"))
    entries.sort()
    return [entry for _, entry in entries]


def home_of(spawn_row) -> tuple:
    """Where a vehicle stands in the frozen frame, from its spawn row.

    The survey box is frozen in one frame and each PX4 reports in its own,
    with the origin wherever that vehicle was placed. Week 1 audit finding 11:
    the offset is measured by the launcher and carried in the manifest, never
    recomputed from a spawn rule. No row means no home, and a survey flown
    from a guessed home is four boxes in four places.
    """
    if not isinstance(spawn_row, Mapping):
        raise SurveyError("no spawn row for this vehicle, so its home in the "
                          "frozen frame is unknown and its survey box would "
                          "be wherever it happened to start")
    try:
        x, y = float(spawn_row["x_m"]), float(spawn_row["y_m"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SurveyError(f"spawn row {spawn_row!r} carries no x_m and "
                          f"y_m") from exc
    if not (math.isfinite(x) and math.isfinite(y)):
        raise SurveyError(f"spawn row {spawn_row!r} has a non-finite position")
    return (x, y, 0.0)


# ------------------------------------------------------------ the commands
def _yaml_scalar(value) -> str:
    """One parameter value the way `ros2 run --ros-args -p` parses it.

    Floats always carry a decimal point. PyYAML reads `1e-05` as a string,
    and a string where the node declared a double is a parameter rclpy
    refuses at startup, after the simulator is already up.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_yaml_scalar(v) for v in value) + "]"
    text = str(value)
    if not text:
        raise SurveyError("an empty string is not a parameter value")
    return text


def ros_args(parameters: Mapping, namespace: Optional[str] = None) -> list:
    """`--ros-args` for a node, parameters in a fixed order."""
    args = ["--ros-args"]
    if namespace:
        args += ["-r", f"__ns:=/{namespace}"]
    for name in sorted(parameters):
        args += ["-p", f"{name}:={_yaml_scalar(parameters[name])}"]
    return args


def mission_node_command(vehicle_id: str, spawn_row, altitude_m,
                         spec: SurveySpec, vehicles: Sequence[str]) -> list:
    """`ros2 run uavx_mission mission_executor` for one vehicle.

    The node goes in the vehicle's own namespace so the graph names it
    `/<vehicle>/mission_executor`, which is the name scripts/seam_manifests.json
    expects per vehicle. Its topics are absolute and built from `vehicle_id`
    inside the node, so the namespace changes the node name and nothing else.
    """
    if not _finite(altitude_m) or altitude_m <= 0:
        raise SurveyError(f"{vehicle_id} has no survey altitude; the scenario "
                          f"names none for it in hover_altitudes_m")
    parameters = {
        "vehicle_id": vehicle_id,
        "swarm_vehicles": list(vehicles),
        "survey_altitude_m": float(altitude_m),
        "home_enu": list(home_of(spawn_row)),
    }
    parameters.update(spec.mission_parameters())
    return (["ros2", "run", "uavx_mission", "mission_executor"]
            + ros_args(parameters, namespace=vehicle_id))


def collector_command(run_id: str, scenario_path: str, spec: SurveySpec,
                      model_entries: Sequence[str],
                      min_separation_m: float) -> list:
    """`ros2 run uavx_eval metrics_collector` for this run.

    `use_sim_time` is on, because every `_s` value in the record is ROS
    simulated time and a collector on wall time would be stamping frames on
    a clock nothing else in the record uses.
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
    parameters.update(spec.collector_parameters())
    return (["ros2", "run", "uavx_eval", "metrics_collector"]
            + ros_args(parameters))


# ------------------------------------------------------------- the payload
def coverage_from_payload(payload: Mapping) -> dict:
    """The four coverage fields, off the collector's payload, checked.

    The collector is the only thing that saw the poses, so the numbers are
    its. What is checked is that they agree with each other: the fraction is
    the seen count over the total, the counts are counts, and the label is
    present. A payload that says 0.97 beside 300 of 400 cells is a payload
    that was edited, and the gate reading only the fraction would not know.
    """
    if not isinstance(payload, Mapping):
        raise SurveyError("the collector produced no payload to read "
                          "coverage from")
    missing = [key for key in COVERAGE_KEYS if key not in payload]
    if missing:
        raise SurveyError(
            f"the collector's payload has no {', '.join(missing)}. It was "
            f"started without a survey box, or a version that does not "
            f"report coverage")
    fraction = payload["coverage_fraction"]
    source = payload["coverage_source"]
    total = payload["coverage_cells_total"]
    seen = payload["coverage_cells_seen"]
    if not _finite(fraction) or not 0.0 <= float(fraction) <= 1.0:
        raise SurveyError(f"coverage_fraction is {fraction!r}, not a number "
                          f"in [0, 1]")
    if not isinstance(source, str) or not source.strip():
        raise SurveyError(f"coverage_source is {source!r}; the label is how "
                          f"a figure computed off flown poses is told from "
                          f"one computed off the plan")
    for name, value in (("coverage_cells_total", total),
                        ("coverage_cells_seen", seen)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SurveyError(f"{name} is {value!r}, not a count")
    if total < 1:
        raise SurveyError("coverage_cells_total is 0, so there is no box")
    if seen > total:
        raise SurveyError(f"coverage_cells_seen {seen} exceeds the "
                          f"{total} cells in the box")
    if abs(float(fraction) - seen / total) > 1e-9:
        raise SurveyError(
            f"coverage_fraction {fraction} does not equal {seen} over {total}; "
            f"the fraction and the counts describe different runs")
    return {
        "coverage_fraction": float(fraction),
        "coverage_source": source,
        "coverage_cells_total": int(total),
        "coverage_cells_seen": int(seen),
    }
