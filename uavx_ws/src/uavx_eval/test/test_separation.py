"""The separation monitor, including the case where it measured nothing.

Round 3 finding 8: `collision_contacts==0` passes when no contact monitor was
ever attached, which is the same shape as the package check that passed on a
machine with no simulator. So the monitor reports how many frames it looked at,
and the test below drives the case where that number is zero.

The floor is imported from `scripts/check_geometry.py`, which is the one place
it is defined and the file that proves the frozen geometry against it. Week 1
audit finding 2 is what a second copy costs.
"""

import pytest
from check_geometry import MIN_SEPARATION

from uavx_eval.separation import (CONTACT_M, SeparationError,
                                  SeparationMonitor, pairwise_minimum)

CLEAR = MIN_SEPARATION * 3
TOO_CLOSE = MIN_SEPARATION / 2


def test_the_floor_comes_from_the_frozen_geometry():
    """Not a number typed here that happens to match today."""
    assert MIN_SEPARATION > 0
    monitor = SeparationMonitor(MIN_SEPARATION)
    assert monitor.min_separation_m == MIN_SEPARATION


def test_a_floor_that_is_not_a_positive_distance_is_refused():
    for bad in (0, -1.0, None, float("nan")):
        with pytest.raises(SeparationError):
            SeparationMonitor(bad)


def test_distance_is_three_dimensional():
    """Altitude layering is a separation strategy, so height has to count."""
    stacked = {"uav_1": (0.0, 0.0, 0.0), "uav_2": (0.0, 0.0, CLEAR)}
    distance, first, second = pairwise_minimum(stacked)
    assert distance == pytest.approx(CLEAR)
    assert (first, second) == ("uav_1", "uav_2")


def test_one_vehicle_has_no_separation_to_report():
    """An invented large number here would read as a comfortable pass."""
    assert pairwise_minimum({"uav_1": (0.0, 0.0, 0.0)}) is None
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(0.0, {"uav_1": (0.0, 0.0, 0.0)})
    report = monitor.report()
    assert report["contact_monitor_samples"] == 0
    assert "min_pairwise_separation_m" not in report


def test_a_monitor_that_watched_nothing_says_so():
    """Zero violations out of zero frames must not read like a clean run."""
    report = SeparationMonitor(MIN_SEPARATION).report()
    assert report == {"contact_monitor_samples": 0,
                      "separation_violations": 0,
                      "collision_contacts": 0,
                      "contact_distance_m": CONTACT_M}
    # The closest approach and the two "first" times are the fields that would
    # read as a measurement. None of them is invented for a run with no frames.
    assert "min_pairwise_separation_m" not in report
    assert "first_separation_violation_s" not in report
    assert "first_collision_contact_s" not in report


def test_a_clear_pair_is_not_a_violation():
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(1.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (CLEAR, 0.0, 0.0)})
    report = monitor.report()
    assert report["separation_violations"] == 0
    assert report["contact_monitor_samples"] == 1
    assert report["min_pairwise_separation_m"] == pytest.approx(CLEAR)


def test_a_close_pair_is_a_violation_and_is_timed():
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(1.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (CLEAR, 0.0, 0.0)})
    monitor.offer(2.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (TOO_CLOSE, 0.0, 0.0)})
    monitor.offer(3.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (TOO_CLOSE, 0.0, 0.0)})
    report = monitor.report()
    assert report["separation_violations"] == 2
    assert report["contact_monitor_samples"] == 3
    assert report["min_pairwise_separation_m"] == pytest.approx(TOO_CLOSE)
    assert report["first_separation_violation_s"] == 2.0
    assert report["min_pairwise_separation_pair"] == ["uav_1", "uav_2"]


def test_exactly_at_the_floor_is_not_a_violation():
    """The rule is `below 10 m`, so 10 m itself is separated."""
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(1.0, {"uav_1": (0.0, 0.0, 0.0),
                        "uav_2": (MIN_SEPARATION, 0.0, 0.0)})
    assert monitor.report()["separation_violations"] == 0


def test_the_closest_pair_of_three_is_the_one_reported():
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(1.0, {"uav_1": (0.0, 0.0, 0.0),
                        "uav_2": (CLEAR, 0.0, 0.0),
                        "uav_3": (CLEAR + TOO_CLOSE, 0.0, 0.0)})
    report = monitor.report()
    assert report["min_pairwise_separation_m"] == pytest.approx(TOO_CLOSE)
    assert report["min_pairwise_separation_pair"] == ["uav_2", "uav_3"]
    assert report["separation_violations"] == 1


def test_a_touch_is_a_contact_as_well_as_a_violation():
    # Chunk 4.2. Every W4 gate asks for zero contacts beside a non-zero
    # sample count, because zero contacts on a monitor that never ran is the
    # same shape as a package check passing on a machine with no simulator.
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(4.0, {"uav_1": (0.0, 0.0, 0.0),
                        "uav_2": (CONTACT_M / 2, 0.0, 0.0)})
    report = monitor.report()
    assert report["collision_contacts"] == 1
    assert report["separation_violations"] == 1
    assert report["first_collision_contact_s"] == 4.0


def test_a_violation_well_clear_of_the_airframes_is_not_a_contact():
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(4.0, {"uav_1": (0.0, 0.0, 0.0),
                        "uav_2": (TOO_CLOSE, 0.0, 0.0)})
    report = monitor.report()
    assert report["separation_violations"] == 1
    assert report["collision_contacts"] == 0
    assert "first_collision_contact_s" not in report


def test_exactly_at_the_contact_distance_counts():
    # The floor is `below 10 m` and this is `at or under a metre`, because one
    # is a rule about airmanship and the other is two airframes occupying the
    # same metre.
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(4.0, {"uav_1": (0.0, 0.0, 0.0),
                        "uav_2": (CONTACT_M, 0.0, 0.0)})
    assert monitor.report()["collision_contacts"] == 1


def test_the_contact_distance_is_reported_so_the_zero_can_be_read():
    monitor = SeparationMonitor(MIN_SEPARATION)
    monitor.offer(1.0, {"uav_1": (0.0, 0.0, 0.0), "uav_2": (CLEAR, 0.0, 0.0)})
    assert monitor.report()["contact_distance_m"] == CONTACT_M
