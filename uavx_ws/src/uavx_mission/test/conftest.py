"""Make chunk 2.1's tests runnable by plain pytest as well as by colcon.

The gate runs these through `colcon test --packages-select uavx_mission`,
which stands in a sourced overlay, so the installed `uavx_mission` and
`uavx_sim` are importable. Somebody writing the planner does not have an
overlay yet, and a test suite that only reports after a full workspace build
is no use while the thing under test is being written. So the source trees go
on `sys.path` as a fallback, exactly as uavx_sim's conftest does.

`scripts/` goes on the path for a different reason. Two frozen figures this
suite needs already have a home in `scripts/check_geometry.py`: the
`mission_integrated` box with its four frozen lane positions, and the
separation floor. Week 1 audit finding 2 was three hand-written copies of one
threshold, none of them compared with the original, so these are imported
from the file that already owns them rather than typed out again. The frozen
pose rate comes out of the runner that samples at it for the same reason.
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# test/ -> uavx_mission/ -> src/ -> uavx_ws/ -> the repository root.
REPO = PACKAGE_ROOT.parents[2]

for extra in (PACKAGE_ROOT,
              REPO / "scripts",
              REPO / "uavx_ws" / "src" / "uavx_sim"):
    if extra.is_dir() and str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

# Chunk 3.4. uavx_sim.scenario_runner now imports uavx_sim.comms, which
# imports the ground station's delivery arithmetic and the frozen protocol
# constants rather than restating either. Reaching POSE_HZ therefore reaches
# those too, on a checkout with nothing built.
#
# Appended, never inserted. Every package here keeps its tests in a `test`
# directory with an `__init__.py`, so a sibling root ahead of this one on
# sys.path makes `test` resolve to somebody else's and pytest reports
# ImportPathMismatchError on a suite that is perfectly fine.
for sibling in ("uavx_comms", "uavx_gcs"):
    root = REPO / "uavx_ws" / "src" / sibling
    if root.is_dir() and str(root) not in sys.path:
        sys.path.append(str(root))
