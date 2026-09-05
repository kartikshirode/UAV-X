"""Make the contract tests runnable by plain pytest.

scripts/gate.sh runs each W1 contract test with `python3 -m pytest -q <file>`
rather than through colcon, and it does that on purpose: a component test
that needs the whole workspace installed before it can report is no use while
the component is being written. gate_build sources the overlay first, so the
installed uavx_sim is normally importable. This falls back to the source tree
when it is not, so `pytest uavx_ws/src/uavx_sim/test/` works from a clean
checkout with nothing built.
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

# Week 1 audit finding 2. The memory ceiling the gate asserts had been written
# out by hand in five places, and a copy that drifts does not fail loudly: it
# asserts last week's contract and passes. It has one home,
# scripts/check_submission_const.py, and these tests need to reach it. Adding
# the directory here rather than in each test keeps the sys.path juggling in
# one file, which is what this file is for.
REPO_SCRIPTS = PACKAGE_ROOT.parent.parent.parent / "scripts"

if REPO_SCRIPTS.is_dir() and str(REPO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(REPO_SCRIPTS))

# Chunk 3.4. uavx_sim.comms imports the ground station's arithmetic and the
# frozen protocol constants rather than restating either, because a delivery
# ratio computed twice is two answers to the question the communication row
# of the rubric is scored on. Both of those modules are pure, so the same
# source tree fallback applies to them.
SRC = PACKAGE_ROOT.parent

for sibling in ("uavx_comms", "uavx_gcs"):
    root = SRC / sibling
    if root.is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
