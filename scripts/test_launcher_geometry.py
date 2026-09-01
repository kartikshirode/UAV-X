#!/usr/bin/env python3
"""Exercise the two pieces of arithmetic in scripts/sitl_multi.sh.

Week 1 audit finding 9. The launcher put one vehicle on the 5 cm lip at the
edge of the asphalt plane, that vehicle took off tilted and flew away under
failsafe, and it cost seven runs before anybody looked at the ground rather
than at the vehicle. The fix centred the spawn line and added a resting tilt
check. Neither had a test. `check_shell.sh` runs `bash -n`, which sees no
further into an awk program than into a comment, and the only way anyone knew
the tilt check worked was that somebody had watched it fire once.

Both programs are pulled out of the launcher by the markers around them and run
here directly, so this needs no simulator and no ROS. Extracting rather than
restating is the point: a copy of the formula here would agree with itself
forever while the launcher moved on, which is round 2 finding 2 and week 1
audit finding 2.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "scripts" / "sitl_multi.sh"

# Where the pavement ends, measured on this machine. The asphalt_plane gazebo
# resolves for empty.world is 20 m on a side, centred on the origin, so its lip
# runs along y = +/- half of that.
PLATE_HALF_WIDTH_M = 10.0

failures: list = []


def fail(case: str, why: str) -> None:
    failures.append(case)
    print(f"  FAIL  {case}: {why}")


def ok(case: str, detail: str = "") -> None:
    print(f"  ok    {case}{'   ' + detail if detail else ''}")


def extract(marker: str) -> str:
    """The awk program between the two markers named for it."""
    body = LAUNCHER.read_text(encoding="utf-8")
    m = re.search(rf"# >>> {marker}[^\n]*\n(.*?)\n# <<< {marker}", body, re.S)
    if not m:
        print(f"  FAIL  cannot find the {marker} block in {LAUNCHER.name}. "
              f"If it was renamed, rename it here; do not copy the program.")
        sys.exit(1)
    program = m.group(1)
    # The block is a shell assignment. What awk runs is what is inside the
    # single quotes, and reading it out this way means a change to the shell
    # around it does not quietly stop this file from testing anything.
    q = re.search(r"^[A-Z_]+='(.*)'$", program, re.S)
    if not q:
        print(f"  FAIL  the {marker} block is not a single quoted assignment "
              f"any more, so this file cannot tell what awk is given.")
        sys.exit(1)
    return q.group(1)


SPACING_AWK = extract("spacing-awk")
TILT_AWK = extract("tilt-awk")


def spawn_y(i: int, n: int, spacing: float) -> float:
    out = subprocess.run(
        ["awk", "-v", f"i={i}", "-v", f"n={n}", "-v", f"s={spacing}",
         SPACING_AWK],
        capture_output=True, text=True, check=True).stdout
    return float(out)


def tilt(pose: str):
    """The launcher's own reading of a pose line: degrees, or None if refused."""
    r = subprocess.run(["awk", TILT_AWK], input=pose,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return float(r.stdout)


print("--- where the vehicles stand")

# The line is centred, which is the whole fix. Four vehicles used to run out
# from the origin to y = 15 and put instance 2 on the lip at exactly 10.
for n in (2, 3, 4, 5, 6):
    ys = [spawn_y(i, n, 5) for i in range(n)]
    if abs(sum(ys)) > 1e-6:
        fail(f"{n} vehicles are centred on the origin",
             f"they sum to {sum(ys)}, so the line is off centre")
    elif ys != sorted(ys):
        fail(f"{n} vehicles are centred on the origin",
             "they are not in increasing order of y")
    else:
        ok(f"{n} vehicles centred on the origin", f"y = {ys}")

# The default formation has to clear the lip, and clear it by enough that a
# vehicle settling is not sitting on the edge of the edge.
ys = [spawn_y(i, 4, 5) for i in range(4)]
margin = PLATE_HALF_WIDTH_M - max(abs(y) for y in ys)
if margin < 1.0:
    fail("the default formation clears the pavement edge",
         f"nearest vehicle is {margin} m from the lip")
else:
    ok("the default formation clears the pavement edge",
       f"{margin} m of margin at y = {ys}")

# And the guard has to have something to guard: a wide enough spacing must put
# a vehicle back on the lip, or the check added this week is unreachable.
wide = [spawn_y(i, 2, 20) for i in range(2)]
if PLATE_HALF_WIDTH_M not in [abs(y) for y in wide]:
    fail("a wide spacing still reaches the lip",
         f"2 vehicles at 20 m sit at {wide}, so the negative case does not bite")
else:
    ok("a wide spacing still reaches the lip", f"y = {wide}")

# Half spacings need real arithmetic. Integer division would put an even count
# back on whole metres and quietly undo the centring.
if spawn_y(0, 4, 5) != -7.5:
    fail("an even vehicle count keeps its half spacing",
         f"instance 0 of 4 at 5 m is at {spawn_y(0, 4, 5)}, expected -7.5")
else:
    ok("an even vehicle count keeps its half spacing")

print()
print("--- reading a resting pose")

level = "0.0 -7.5 0.0544 0.0012 -0.0009 0.0"
got = tilt(level)
if got is None:
    fail("a level vehicle reads as level", "the pose was refused")
elif got > 0.2:
    fail("a level vehicle reads as level", f"got {got} degrees")
else:
    ok("a level vehicle reads as level", f"{got} deg")

# The measured roll of the vehicle that was standing on the lip. 0.157 rad.
on_the_lip = "0.0 10.0 0.0910 -0.157 0.0007 0.0"
got = tilt(on_the_lip)
if got is None:
    fail("the vehicle that was on the lip reads as tilted", "pose refused")
elif not 8.5 < got < 9.5:
    fail("the vehicle that was on the lip reads as tilted",
         f"got {got} degrees, expected about 9")
else:
    ok("the vehicle that was on the lip reads as tilted", f"{got} deg")

# Pitch has to count as much as roll. A vehicle nose down on a slope is no more
# fit to take off than one on its side.
got = tilt("0.0 0.0 0.05 0.0 -0.157 0.0")
if got is None or not 8.5 < got < 9.5:
    fail("pitch counts as much as roll", f"got {got}")
else:
    ok("pitch counts as much as roll", f"{got} deg")

# Everything below here is the failing-open case. awk reads a non-numeric field
# as zero, so before this week's hardening each of these produced 0.00 and the
# launcher reported a vehicle level that it had never measured.
refused = [
    ("nothing at all", ""),
    ("an error instead of a pose", "Error [Node.cc:120] No namespace found"),
    ("five numbers where six were expected", "0.0 -7.5 0.05 0.001 -0.0009"),
    ("seven numbers", "0.0 -7.5 0.05 0.001 -0.0009 0.0 1.0"),
    ("a labelled format", "x: 0.0 y: -7.5 z: 0.05 r: 0.9 p: 0.1 yaw: 0"),
    ("words in the angle fields", "0.0 -7.5 0.05 nan none 0.0"),
    ("a blank line", "\n"),
]
for name, pose in refused:
    got = tilt(pose)
    if got is None:
        ok(f"refuses {name}")
    else:
        fail(f"refuses {name}",
             f"read {got} degrees out of {pose!r}, which is a guard failing open")

# A pose printed with a header line still has to be read, or a chatty gazebo
# turns every vehicle into a launcher failure. The first parseable line wins.
got = tilt("Waiting for master.\n0.0 -7.5 0.0544 0.0012 -0.0009 0.0")
if got is None:
    fail("skips a header line and reads the pose under it", "refused it")
elif got > 0.2:
    fail("skips a header line and reads the pose under it", f"got {got}")
else:
    ok("skips a header line and reads the pose under it", f"{got} deg")

print()
if failures:
    print(f"FAILED: {len(failures)} launcher geometry check(s) behaved wrongly")
    sys.exit(1)
print("the spawn line and the tilt reading both behave as the launcher claims")
sys.exit(0)
