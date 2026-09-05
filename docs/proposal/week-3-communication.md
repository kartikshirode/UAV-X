# Communication architecture

Drafted in week 3 from the runs week 3 produced. Standing rule 5: a section a
week, so the submission tail is a freeze and a send. Every number below names
the run it came off and none of it is quoted from a design document.

Target length in the assembled proposal: about 1.5 of the 6 to 8 pages. This is
the section carrying the most marks, so it is the one written closest to the
evidence.

## The claim

Four vehicles holding station 475 m from a ground station deliver 99.7% of what
they generate, and the far vehicle delivers 99.7% of its own over a mesh where
it has no link to the ground station at all. Turn forwarding off and that same
vehicle delivers nothing. The pair is the evidence, and it is two runs of the
same file differing in one flag.

## Why a relay is needed at all

The radio is a two band model. Delivery is deterministic at or under 200 m,
impossible beyond 250 m, and the band between the two is drawn against a seeded
random number so a run replays exactly. `scripts/check_geometry.py` enumerates
all ten pairs of the frozen layout rather than the handful an author would
think to list, which is the check that caught an earlier version of this design
where the survey drones already reached the anchor directly and killing the
relay proved nothing.

At the frozen positions the graph is a chain:

| Pair | Distance | Link |
| --- | --- | --- |
| `gcs` to `uav_1` | 167.7 m | yes |
| `uav_1` to `uav_2` | 165.3 m | yes |
| `uav_2` to `uav_3` | 163.6 m | yes |
| `uav_2` to `uav_4` | 164.5 m | yes |
| `uav_3` to `uav_4` | 150.3 m | yes |
| `gcs` to `uav_4` | 484.6 m | no |

The last row is what the section rests on. `uav_4` is 484.6 m from the ground
station, 1.9 times the range at which any link exists, so every observation of
its that arrives has crossed hops the radio model drew.

## What the swarm runs

Each vehicle runs one router. Routers do not talk to each other. Each publishes
to its own transmit endpoint and subscribes to its own receive endpoint, and a
single link layer process, which stands outside the swarm and represents the
physics, is what carries anything between them. It is the only process allowed
to know where every vehicle is.

That split is enforced rather than described. A static pass over the source
refuses any file naming two vehicles' endpoints, reading simulator ground truth
or opening a service between swarm nodes. A second pass reads the live ROS
graph captured mid-run and compares every publisher and subscriber against a
per-scenario manifest. Both ran clean on the runs quoted here. Without them the
easy implementation is a swarm sharing a Python dictionary, reporting a
delivery ratio of 1.0 that means nothing.

The ground station is a node in that graph like any other, at the origin, with
one transmit and one receive endpoint. It was not, in an early draft, and the
reason it is now is that a destination wired straight to every router measures
nothing at all.

## What delivered once means

An observation is identified by its origin and its sequence number and by
nothing else. The delivery ratio is a comparison of two sets of those
identities, not a count of arrivals, because a retry from the origin and a
drain from a backlog custodian are both correct and both arrive. Counting
arrivals reports a drained backlog delivered twice. RFC 9171 draws the same
line for the same reason.

The denominator comes from the origins. Each router writes what it generated
and the ground station writes what it accepted. A denominator the destination
supplies is always satisfied, which is the shape of a delivery ratio that reads
1.0 in every run including the ones where the relay was dead.

Two hop numbers are reported and they are not interchangeable. `hop_count`
counts forwarders and the path length counts edges, so `uav_4` reaching the
ground station through `uav_2` and `uav_1` shows 2 in the first and 3 in the
second. Both are in the record under names that say which is which, and both
are the minimum across that origin's deliveries rather than the mean. A mean
lets a run that mostly went direct report a relayed route.

## The measurement

`relay_required`, run `relay_required_20260905T112903Z`. Four vehicles station-keeping at the
frozen positions for 240 s, each sending 5 application packets a second to the
ground station. They reached station in 41 s of ingress before the clock
started and finished the run between 5 and 20 cm from where the design puts
them.

| Origin | Sent | Delivered | Ratio | Forwarders | Edges |
| --- | --- | --- | --- | --- | --- |
| `uav_1` | 1239 | 1237 | 0.9984 | 0 | 1 |
| `uav_2` | 1238 | 1235 | 0.9976 | 1 | 2 |
| `uav_3` | 1239 | 1235 | 0.9968 | 2 | 3 |
| `uav_4` | 1239 | 1235 | 0.9968 | 2 | 3 |

Swarm figure 0.9974. The forwarder column is the topology read back out of the
delivered traffic: none for the anchor, one for its neighbour, two for both
surveyors, which is `uav_4 -> uav_2 -> uav_1 -> gcs`. Nobody told the ground
station to expect that path.

## The control

`direct_only`, run `direct_only_20260905T113527Z`. The same file with forwarding disabled.
Same seed, same stations, same roles, same duration, so the pair differ in one
flag, and `check_geometry.py` compares the two files field by field to keep
them that way.

| Origin | Sent | Delivered | Ratio |
| --- | --- | --- | --- |
| `uav_1` | 1243 | 1242 | 0.9992 |
| `uav_2` | 1243 | 0 | 0.0000 |
| `uav_3` | 1243 | 0 | 0.0000 |
| `uav_4` | 1243 | 0 | 0.0000 |

Swarm figure 0.2498. Only the anchor, at 167.7 m, has a link to the ground
station, and it keeps delivering at the same rate it did with forwarding on.
Everybody else is out of range and delivers nothing, which is the arithmetic in
the distance table rather than a coincidence of this run.

## What this does not yet show

Nothing has failed in either run. Both are steady state, and the interesting
half of the challenge is what happens when the relay dies. That is week 4's
`relay_kill` and `link_loss`. The buffering and the role transfer they exercise
are implemented and tested against the state machines, and have not yet been
driven by a simulator.

The 0.3% that does not arrive is the tail of the run. The routers and the
ground station stop together, so an observation generated in the last fraction
of a second has nowhere to be acknowledged. It is a property of where the run
ends and not of the mesh.
