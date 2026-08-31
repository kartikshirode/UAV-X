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
