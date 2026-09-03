"""Make these tests runnable by plain pytest, and reach the geometry oracle.

Two paths get added and both are deliberate.

The package root, so `import uavx_comms` works from a clean checkout with
nothing built. Chunk 2.3 is pure logic and a test that needs a colcon overlay
before it can report is no use while the logic is being written.

The repository's scripts directory, so the tests can import
scripts/check_geometry.py. That file is the oracle for every distance, every
hop count and every slot in this repository, and it enumerates all ten pairs at
every sampled instant of every frozen trajectory rather than the handful an
author thought to list. Recomputing a distance inside a test would be writing a
second oracle that agrees with itself, which is the defect round 3 and round 4
both found: a checker only checks the thing its author thought to enumerate.
"""

import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = TEST_DIR.parent
REPO = PACKAGE_ROOT.parent.parent.parent
REPO_SCRIPTS = REPO / "scripts"

# TEST_DIR is here because test/ carries an __init__.py, which makes pytest
# import these modules as test.<name> and leaves plain `import conftest`
# unresolvable. The alternative is dropping the __init__.py, which changes how
# colcon collects the package.
for extra in (TEST_DIR, PACKAGE_ROOT, REPO_SCRIPTS):
    if extra.is_dir() and str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


import check_geometry as geometry_oracle           # noqa: E402
from uavx_comms import election, params, pure_net  # noqa: E402

# The roles the frozen scenarios start in, from architecture.md section 6. The
# anchor is never eligible to move and the ground station is not a vehicle.
FROZEN_ROLES = {
    "gcs": election.ROLE_GCS_ANCHOR,
    "uav_1": election.ROLE_GCS_ANCHOR,
    "uav_2": election.ROLE_RELAY,
    "uav_3": election.ROLE_SURVEY,
    "uav_4": election.ROLE_SURVEY,
}

# Long enough for HELLO, the link-state flood and two route computations to
# settle from nothing. Derived from the frozen periods rather than guessed, so
# a change to either period moves this with it.
CONVERGENCE_S = (params.NEIGHBOUR_TIMEOUT_S
                 + 2 * params.LSA_PERIOD_S
                 + params.HELLO_PERIOD_S)


def frozen_net(seed=7, **kwargs):
    """The frozen five-node topology from architecture.md section 6.

    Positions come from the geometry oracle rather than from a copy here, so a
    test can never pass against coordinates this repository has stopped using.
    """
    return pure_net.PureNet(geometry_oracle.START, roles=dict(FROZEN_ROLES),
                            seed=seed, **kwargs)


def settled_net(seed=7, **kwargs):
    net = frozen_net(seed=seed, **kwargs)
    net.run_for(CONVERGENCE_S)
    return net


def oracle_path(positions, src, dst="gcs"):
    """What check_geometry.py says the path is, over the positions given."""
    return geometry_oracle.path_to(positions, src, dst)
