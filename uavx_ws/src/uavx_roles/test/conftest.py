"""Make this package's tests runnable by plain pytest, with nothing built.

Same shape as the other packages. The decision modules here import nothing
from ROS, so the whole suite runs from a clean checkout, and only the node
itself needs an overlay.

Sibling roots are appended and never inserted. Every package in this
workspace keeps its tests in a `test` directory with an `__init__.py`, so a
sibling ahead of this package's own root on sys.path makes `test` resolve to
somebody else's and pytest reports ImportPathMismatchError on a suite that is
perfectly fine.
"""

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent

if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

# uavx_roles imports the role numbers, the message kinds and the lease period
# from uavx_comms rather than restating them, and the unset station from
# uavx_mission. All three modules are pure.
SRC = PACKAGE_ROOT.parent
for sibling in ("uavx_comms", "uavx_mission"):
    root = SRC / sibling
    if root.is_dir() and str(root) not in sys.path:
        sys.path.append(str(root))
