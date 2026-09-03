"""Every frozen protocol parameter this package runs on, in one place.

stage-1/architecture.md sections 2, 3, 4 and 5 freeze these numbers, and
scripts/check_geometry.py holds the machine copy that scripts/check_docs.py
reads when it checks a number quoted in prose. That file deliberately needs no
ROS and lives outside the workspace, so a node installed under the overlay
cannot import it. This module is therefore the runtime home, and
test/test_frozen_params.py binds every shared name back to check_geometry so a
drift fails loudly instead of quietly asserting last week's design.

Nothing here is copied out of scripts/gate.sh. That file owns the acceptance
thresholds and it is the only place they live. The two numbers that look like
gate values, the queue capacity and the forward rate, are architecture
parameters that the gate happens to assert against; the arithmetic tying them
together is derived in the tests rather than written down as a constant.
"""

# --- section 2, the link model ---------------------------------------------

# Deterministic delivery at or under R_FULL_M, deterministic drop beyond
# R_MAX_M, and a linear fade between them. The fade band exists to be exercised
# and reported: two lossy hops in series need each hop above 0.97, which lands
# within a few metres of R_FULL_M, and no moving vehicle holds that.
R_FULL_M = 200.0
R_MAX_M = 250.0
HOP_LATENCY_S = 0.020
POSE_SAMPLE_HZ = 20.0

# --- section 2, the placement rule -----------------------------------------

# A link the routing must use sits inside USED_LINK_MAX_M; a link that must not
# exist sits beyond UNUSED_LINK_MIN_M. A pair between the two makes a code
# failure and an unlucky draw produce the same result, which is untestable.
USED_LINK_MAX_M = 175.0
UNUSED_LINK_MIN_M = 300.0

# --- section 3, routing ----------------------------------------------------
HELLO_PERIOD_S = 1.0
NEIGHBOUR_TIMEOUT_S = 3.0
LSA_PERIOD_S = 2.0
LSA_TTL = 4
ROUTE_HYSTERESIS_WINS = 2
APP_PACKET_RATE_HZ = 5.0
QUEUE_CAPACITY = 512
FORWARD_RATE_PPS = 200.0
OBSERVATION_BYTES = 256
OBSERVATION_LIFETIME_S = 300.0

# lsa_seq is a uint32 in SwarmPacket's sibling LinkState.msg. Higher wins, and
# the longest frozen scenario floods a few hundred of them, so the counter
# cannot approach this. The router asserts it rather than implementing a
# wrapping comparison nobody could exercise.
UINT32_MAX = 0xFFFFFFFF

# --- section 4, roles ------------------------------------------------------
ELECTION_WINDOW_S = 1.0
STABILITY_WINDOW_S = 3.0

# The allowance in the derived reconnect budget for accelerating out of a
# hold and settling onto the slot. It is the one term in that sum that is an
# allowance rather than arithmetic, which is why it is named here instead of
# appearing as a number inside the sum.
SETTLE_ALLOWANCE_S = 4.0
ROLE_LEASE_S = 30.0
RELAY_BAND_M = 75.0
SLOT_CLEARANCE_M = 15.0
SLOT_RAISE_STEP_M = 5.0
SLOT_CEILING_M = 95.0

# --- section 5, safety and motion ------------------------------------------
CRUISE_SPEED_MPS = 10.0
MIN_SEPARATION_M = 10.0

# The ground station is a node in the graph like any other. Round 2 found that
# leaving it out invited an implementer to wire it straight to every router.
GCS_ID = "gcs"
