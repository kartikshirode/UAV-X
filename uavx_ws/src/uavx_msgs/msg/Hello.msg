# One hop neighbour beacon. Not forwarded.
# Frozen in stage-1/architecture.md section 1b.

string sender_id
# Sim seconds at transmission.
float64 sent_at
# Metres in the scenario frame, x y z.
float32[3] position
# Metres per second, x y z.
float32[3] velocity
# Monotonic per sender, so a lost beacon is visible.
uint32 seq
