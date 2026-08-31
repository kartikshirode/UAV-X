# One role grant for one node, valid for one epoch.
# Frozen in stage-1/architecture.md section 1b.

# Increments on every reassignment. An older epoch never wins.
uint32 epoch
# Node the role is granted to.
string node_id
# One of the constants below.
uint8 role
uint8 SURVEY=0
uint8 RELAY=1
uint8 GCS_ANCHOR=2
# Assigned station in the scenario frame, metres, x y z.
float32[3] slot
# Sim seconds after which the grant is void unless renewed.
float64 lease_expires_at
# Node that issued the grant. Lets the gate assert a release came from
# the epoch owner and not from a node that used to be one.
string sender_id
