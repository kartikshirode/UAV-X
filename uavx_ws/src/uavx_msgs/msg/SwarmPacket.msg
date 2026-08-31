# The only message that crosses the swarm seam.
# Frozen in stage-1/architecture.md section 1b. Field order is the contract.
# (origin_id, sequence) is the identity the GCS deduplicates on.

# Vehicle that first produced this. A relayed packet keeps its origin.
string origin_id
# Monotonic per origin, so a gap in the sequence is visible.
uint32 sequence
# Sim seconds when the observation was made, not when it was sent.
float64 created_at
# created_at plus observation_lifetime, 300 s.
float64 expires_at
# One of the constants below. It selects the queue and is never inferred
# from the payload. An unknown value is dropped and counted as a
# protocol error.
uint8 kind
uint8 OBSERVATION=0
uint8 HELLO=1
uint8 LSA=2
uint8 ROLE=3
uint8 ACK=4
# gcs for an observation. Empty for a broadcast.
string dest_id
# Incremented by each forwarder, capped at lsa_ttl.
uint8 hop_count
# Node ids in order, appended by each forwarder. Makes
# handback.prepared_path checkable.
string[] path
# 256 bytes for an observation.
uint8[] payload
