"""Chunk 3.4: holding one point, as arithmetic the node can be handed.

`relay_required.yaml` and `direct_only.yaml` are described in
architecture.md section 6 as common geometry with station-keeping and no
survey motion, and until this chunk nothing in the workspace could fly a
vehicle to a fixed point. The mission executor plans boustrophedon paths
over a strip; `MissionExecutor` refuses an empty plan and checks every
waypoint lies inside the strip it was given, so a single station point
cannot be expressed as a plan and pretending otherwise would mean loosening
the check that keeps a surveyor inside its own lane.

So station-keeping is a second mode rather than a degenerate survey, and
what decides which mode a node is in lives here, where a test can reach it
without a simulator.

Two things are worth stating about the shape of the parameter.

**Unset is three NaNs, not an empty list.** rclpy infers a parameter's type
from its default and refuses to declare an empty list, and a default of
(0, 0, 0) would make the origin of the frozen frame indistinguishable from
"nobody asked for a station". NaN is the value that cannot be confused with
a position.

**The altitude is cross-checked rather than trusted twice.** A scenario
names each vehicle's layer altitude in `hover_altitudes_m` and again as the
third component of its station, because the runner climbs against the first
and the executor holds the second. Two spellings of one number drift, and
the drift here would be a vehicle holding station 10 m off its layer, which
is the whole separation floor. They are compared, and a disagreement is
refused before anything is launched.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

# The layer altitudes are 10 m apart and PX4 holds a setpoint to well inside
# a metre, so a tenth of a metre is far tighter than any real disagreement
# and far looser than float noise between a YAML scalar and a ROS double.
ALTITUDE_TOLERANCE_M = 0.1


class StationError(ValueError):
    """A station a vehicle will not be sent to, naming the reason."""


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def unset() -> Tuple[float, float, float]:
    """The default a node declares when the scenario asks for no station."""
    return (math.nan, math.nan, math.nan)


def station_of(values: Sequence, altitude_m=None
               ) -> Optional[Tuple[float, float, float]]:
    """The station this node holds, or None when it is flying a survey.

    None is a real answer. `survey_baseline` names no stations and its four
    executors plan strips, so the absence has to be expressible without an
    error.
    """
    if values is None:
        return None
    values = list(values)
    if len(values) != 3:
        raise StationError(
            f"a station is three numbers, x, y and z in the frozen frame; "
            f"got {len(values)}")
    finite = [_finite(v) for v in values]
    if not any(finite):
        return None
    if not all(finite):
        raise StationError(
            f"station {values!r} has some components set and some not. A "
            f"vehicle cannot hold two of three coordinates, and the half "
            f"that was set would look like a deliberate position")
    station = tuple(float(v) for v in values)
    if altitude_m is not None:
        if not _finite(altitude_m):
            raise StationError(
                f"the layer altitude is {altitude_m!r}, so the station's "
                f"height has nothing to be checked against")
        if abs(station[2] - float(altitude_m)) > ALTITUDE_TOLERANCE_M:
            raise StationError(
                f"the station sits at z {station[2]} and the scenario climbs "
                f"this vehicle to {float(altitude_m)}. One of the two is what "
                f"the run flies and the record cannot say which")
    return station
