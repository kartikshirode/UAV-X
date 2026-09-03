"""The frozen ENU frame against the frame PX4 actually reports in.

architecture.md fixes every coordinate in one frame: local ENU metres with the
origin at the GCS, x east, y north, z up. PX4 reports and accepts a different
one: NED metres with the origin at the vehicle's own home, x north, y east,
z down. Two things differ at once, an origin and an axis convention, and each
of them is silent when it is wrong.

Week 1 audit finding 11 is the origin half. The vehicles spawn on a line
centred on the world origin, so every one of them has a different home. A
survey plan handed straight to PX4 as a setpoint therefore flies the box
relative to wherever that vehicle happened to start, and four vehicles fly
four different boxes that all look plausible in their own logs.

The home is an argument to every function here and is never derived inside
one. scripts/sitl_multi.sh writes runs/.launcher-spawn.json when it places
the vehicles and the runner carries it into each record as spawn_x_m and
spawn_y_m, so there is one measured answer to where a vehicle stands. A
second one computed from a spawn rule would agree with it until the launcher
moved, which is what it did.

The axis half is worse, because the numbers still look like a survey. Swap x
and y on a square box and the plan comes back a square box. It is the wrong
square, rotated into the wrong part of the world, and nothing about its shape
says so.

So the conversion is one function that does both, in one order, in one place.
Nothing else in this package is allowed to subtract a home or reorder an
axis.
"""

from __future__ import annotations

from typing import Tuple

Vec3 = Tuple[float, float, float]


def frozen_to_local_enu(point: Vec3, home: Vec3) -> Vec3:
    """A frozen frame point as an offset from the vehicle's home, still ENU."""
    return (point[0] - home[0], point[1] - home[1], point[2] - home[2])


def enu_to_ned(vec: Vec3) -> Vec3:
    """East, north, up to north, east, down.

    Not a rotation about the vertical: it is a reflection, and it is its own
    inverse. Writing it as a rotation is the mistake that leaves a mission
    mirrored about the diagonal and still shaped like a mission.
    """
    return (vec[1], vec[0], -vec[2])


def ned_to_enu(vec: Vec3) -> Vec3:
    """North, east, down to east, north, up. The same reflection, back."""
    return (vec[1], vec[0], -vec[2])


def frozen_to_px4(point: Vec3, home: Vec3) -> Vec3:
    """A frozen frame waypoint as a PX4 local NED setpoint.

    The home is subtracted in the frozen frame and the axes are reordered
    afterwards. The other order subtracts a home expressed east, north, up
    from a position expressed north, east, down, which is wrong by exactly
    the amount the vehicle is away from the world origin. It reads correctly
    for a vehicle that spawned at the origin, which is one of the four.
    """
    return enu_to_ned(frozen_to_local_enu(point, home))


def px4_to_frozen(local_ned: Vec3, home: Vec3) -> Vec3:
    """A PX4 local NED position back in the frozen frame.

    This is the direction the coverage metric needs. `coverage_fraction` is
    scored against a box whose corners are frozen ENU coordinates, and the
    poses it is scored from arrive per vehicle in that vehicle's own local
    frame.
    """
    enu = ned_to_enu(local_ned)
    return (enu[0] + home[0], enu[1] + home[1], enu[2] + home[2])
