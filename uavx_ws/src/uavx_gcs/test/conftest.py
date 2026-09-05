"""Make the ground station's arithmetic runnable by plain pytest.

`uavx_gcs.ledger` imports no ROS on purpose, so it can be driven on a clean
checkout with nothing built, exactly as `uavx_eval.collector` and
`uavx_comms.router` can. The gate runs these through
`colcon test --packages-select uavx_gcs`, which stands in a sourced overlay;
this path is for whoever is writing the thing and has no overlay yet.

`gcs_node` is deliberately not importable from here. It imports rclpy and
px4 message types, and a test that needs a built workspace before it can
report is no use while the arithmetic under it is being written.
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
