# One link state advertisement, flooded to build the routing view.
# Frozen in stage-1/architecture.md section 1b.

# Node whose neighbour set this describes, not the forwarder.
string originator_id
# Monotonic per originator. A newer seq supersedes an older one.
uint32 lsa_seq
# Node ids the originator can currently hear.
string[] neighbours
# Sim seconds at first transmission.
float64 sent_at
# Remaining hops. A forwarder decrements it and drops the LSA at zero.
uint8 ttl
