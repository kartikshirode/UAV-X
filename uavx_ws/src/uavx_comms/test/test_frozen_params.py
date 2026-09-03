"""The runtime parameters have to be the ones the geometry oracle uses.

scripts/check_geometry.py is where the frozen numbers live in machine-readable
form, and scripts/check_docs.py already reads it so that a number quoted in
prose is checked against arithmetic rather than against a list somebody
maintained by hand. A node installed under the colcon overlay cannot import it,
so uavx_comms/params.py is a second home, and week 1 audit finding 2 is exactly
about what a second home does when nothing compares it with the first: it does
not fail loudly, it asserts last week's contract and passes.

This is a drift guard rather than a behaviour test, and it is written down as
one. Everything else in this suite drives the state machines.
"""

from uavx_comms import params

import check_geometry as oracle

# Left is the runtime name, right is the oracle's. Every number this package
# shares with the frozen geometry appears here; a parameter that is genuinely
# ours alone, the queue capacity and the forward rate, is not in the oracle at
# all and is checked by the arithmetic tests instead.
SHARED = [
    ("R_FULL_M", "R_FULL"),
    ("R_MAX_M", "R_MAX"),
    ("USED_LINK_MAX_M", "USED_LINK_MAX"),
    ("UNUSED_LINK_MIN_M", "UNUSED_LINK_MIN"),
    ("NEIGHBOUR_TIMEOUT_S", "NEIGHBOUR_TIMEOUT"),
    ("LSA_PERIOD_S", "LSA_PERIOD"),
    ("ELECTION_WINDOW_S", "ELECTION_WINDOW"),
    ("STABILITY_WINDOW_S", "STABILITY_WINDOW"),
    ("SETTLE_ALLOWANCE_S", "SETTLE_ALLOWANCE"),
    ("RELAY_BAND_M", "RELAY_BAND"),
    ("SLOT_CLEARANCE_M", "SLOT_CLEARANCE"),
    ("SLOT_RAISE_STEP_M", "SLOT_RAISE_STEP"),
    ("SLOT_CEILING_M", "SLOT_CEILING"),
    ("CRUISE_SPEED_MPS", "CRUISE_SPEED"),
    ("MIN_SEPARATION_M", "MIN_SEPARATION"),
]


def test_every_shared_parameter_matches_the_geometry_oracle():
    drifted = []
    for ours, theirs in SHARED:
        mine = getattr(params, ours)
        yours = getattr(oracle, theirs)
        if mine != yours:
            drifted.append(ours + "=" + repr(mine) + " against check_geometry."
                           + theirs + "=" + repr(yours))
    assert not drifted, ("uavx_comms/params.py has drifted from "
                         "scripts/check_geometry.py: " + "; ".join(drifted))


def test_the_oracle_still_defines_every_name_this_binds_to():
    # If a name is renamed in check_geometry, getattr above would raise and the
    # test would error rather than silently stop comparing. This says so out
    # loud, because a drift guard that quietly checks nothing is worse than no
    # drift guard at all.
    missing = [theirs for _, theirs in SHARED if not hasattr(oracle, theirs)]
    assert not missing, ("check_geometry.py no longer defines " + repr(missing)
                         + ", so those parameters are no longer held to "
                           "anything")


def test_the_queue_holds_the_outage_the_gate_is_allowed_to_reach():
    """Round 4 finding 3, as arithmetic rather than as a number.

    Two frozen values contradicted each other: the queue held 50 packets, which
    is 10 seconds at the application rate, while the design claimed nothing was
    silently dropped across an outage many times longer. No implementation
    could satisfy both. The queue is sized from the outage the design has to
    survive, and this recomputes that rather than restating the answer.
    """
    outage_s = 45.0        # the longest hold queue_drain.yaml produces
    origins = 2            # both surveyors keep working through it
    needed = outage_s * params.APP_PACKET_RATE_HZ * origins
    assert params.QUEUE_CAPACITY >= needed, (
        "the queue holds " + str(params.QUEUE_CAPACITY) + " packets and the "
        "outage the design has to survive produces " + str(needed))


def test_the_forward_rate_clears_that_backlog_faster_than_recovery_is_declared():
    """The backlog reaches the link layer before recovery is even declared.

    Without this the drain bound and the stability window are two numbers that
    were never compared, and a swarm could declare itself recovered while its
    own queue was still the reason nothing had arrived.
    """
    backlog = 45.0 * params.APP_PACKET_RATE_HZ * 2
    drain_s = backlog / params.FORWARD_RATE_PPS
    assert drain_s < params.STABILITY_WINDOW_S


def test_the_relay_band_clears_every_frozen_mission_altitude():
    from uavx_comms import slots
    altitudes = sorted({oracle.START[v][2]
                        for v in ("uav_1", "uav_2", "uav_3", "uav_4")}
                       | set(oracle.SURVEY_ALT.values()))
    assert slots.band_clears(altitudes), (
        "a relay slot would sit less than the required clearance above the "
        "highest mission corridor, so a silent vehicle's altitude is the only "
        "thing keeping them apart")
