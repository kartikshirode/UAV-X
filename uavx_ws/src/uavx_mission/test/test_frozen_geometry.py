"""Hold the survey box in this package against the one architecture.md freezes.

Standing rule 4: no week-agent may change a value in architecture.md to make
its own gate pass. That rule only bites if something compares the two, and
nothing did. The survey box is written down in stage-1/architecture.md
section 6 and again, necessarily, as constants the planner can do arithmetic
on, and a document and a constant drift apart in silence. Week 1 audit
finding 2 was this exact shape one layer down: one threshold, three
hand-written copies, no comparison.

So this file reads the frozen table back out of the document and fails when
either side moves. It fails in both directions on purpose. A planner edited
to cover a smaller box fails here, and so does a box edited to suit a
planner.

The parse is deliberately narrow. It reads the `survey_baseline.yaml` block
and nothing else, and a row it cannot find is a failure rather than a skip: a
checker that quietly matches nothing is round 4 finding 9, and it reports
agreement forever.
"""

import re
from pathlib import Path

import pytest

from uavx_mission.survey_area import (BASELINE_CELL_M, BASELINE_DURATION_S,
                                      BASELINE_SENSOR_RADIUS_M, BASELINE_SIDE_M,
                                      BASELINE_STRIP_COUNT, BASELINE_SW_CORNER_M,
                                      SurveyArea)

REPO = Path(__file__).resolve().parents[4]
ARCHITECTURE = REPO / "stage-1" / "architecture.md"


def frozen_block() -> str:
    """The `survey_baseline.yaml` section of architecture.md, and only it."""
    assert ARCHITECTURE.is_file(), (
        f"{ARCHITECTURE} is the frozen design and every number in this "
        f"package is a copy of something in it")
    text = ARCHITECTURE.read_text(encoding="utf-8")
    m = re.search(r"^### `survey_baseline\.yaml`$(.*?)^### ", text,
                  re.M | re.S)
    assert m, ("architecture.md has no ### `survey_baseline.yaml` section. "
               "If it was renamed, rename it here rather than losing the "
               "comparison.")
    return m.group(1)


def row(pattern: str):
    """One table row out of the frozen block, or a failure naming the row."""
    m = re.search(pattern, frozen_block())
    assert m, (f"architecture.md's survey_baseline table has no row matching "
               f"{pattern!r}, so this package's copy of that figure is held "
               f"to nothing")
    return m


def test_the_box_matches_the_corner_and_side_architecture_freezes():
    m = row(r"\|\s*Survey area\s*\|\s*([\d.]+) m by ([\d.]+) m,\s*"
            r"south-west corner at \(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
    width, height, east, north = (float(g) for g in m.groups())

    assert width == BASELINE_SIDE_M
    assert height == BASELINE_SIDE_M
    assert (east, north) == BASELINE_SW_CORNER_M

    area = SurveyArea.baseline()
    assert (area.x_min, area.y_min) == (east, north)
    assert (area.x_max, area.y_max) == (east + width, north + height)


def test_the_grid_matches_the_cell_and_the_cell_count():
    m = row(r"\|\s*Grid cell\s*\|\s*([\d.]+) m by ([\d.]+) m, so (\d+) cells")
    across, along, cells = float(m.group(1)), float(m.group(2)), int(m.group(3))

    assert across == along == BASELINE_CELL_M
    # The denominator of coverage_fraction. It is stated in the document and
    # derived here from the box and the cell, so a box or a cell edited
    # without the other fails rather than quietly rescoring the metric.
    assert SurveyArea.baseline().cell_count == cells


def test_the_sensor_footprint_matches():
    m = row(r"\|\s*Sensor footprint\s*\|\s*([\d.]+) m radius disc")
    assert float(m.group(1)) == BASELINE_SENSOR_RADIUS_M
    assert SurveyArea.baseline().sensor_radius_m == BASELINE_SENSOR_RADIUS_M


def test_the_strip_count_and_the_strip_width_match():
    m = row(r"\|\s*Vehicles surveying\s*\|\s*(\d+), area split into (\d+) "
            r"vertical strips of ([\d.]+) m")
    vehicles, strips, strip_width = (int(m.group(1)), int(m.group(2)),
                                     float(m.group(3)))

    assert vehicles == strips == BASELINE_STRIP_COUNT
    assert strips * strip_width == BASELINE_SIDE_M, (
        f"{strips} strips of {strip_width} m do not tile a "
        f"{BASELINE_SIDE_M} m box")


def test_the_run_duration_matches():
    m = row(r"\|\s*Run duration\s*\|\s*([\d.]+) s")
    assert float(m.group(1)) == BASELINE_DURATION_S


def test_the_coverage_source_is_still_sampled_poses_and_not_the_plan():
    """The one sentence in the block that constrains what this package is for.

    architecture.md says coverage comes from sampled vehicle poses and never
    from the planned path, and plan.md repeats it. This package plans paths.
    If that sentence ever leaves the document, a planner that reported its own
    intended coverage would satisfy the survey gate without a vehicle moving,
    and the 25% mission row would be earned by arithmetic.
    """
    block = frozen_block()
    assert re.search(r"sampled vehicle poses", block), (
        "architecture.md no longer names sampled poses as the coverage source")
    assert re.search(r"never the planned path", block), (
        "architecture.md no longer forbids scoring coverage off the plan")


def test_the_document_is_read_and_not_assumed():
    """The parse has to be able to fail, or none of the above proves anything.

    A regex that matches nothing and a test that skips look identical from
    the outside. This asks for a row that is not there and requires the
    failure.
    """
    with pytest.raises(AssertionError):
        row(r"\|\s*Survey area\s*\|\s*a field somewhere\s*\|")
