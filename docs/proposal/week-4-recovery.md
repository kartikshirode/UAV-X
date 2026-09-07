# Fault recovery and the integrated run

Drafted in week 4 from the run week 4 produced. Every number below names the
run it came off.

Target length in the assembled proposal: about 1.5 of the 6 to 8 pages. It is
the last section and it carries the rubric's fault recovery row at 15% and its
safety row at 10%, and it is where the other three sections stop being separate
claims.

## The claim

One run, 240 s, four vehicles. The swarm surveys a box 475 m from the ground
station, holds a two hop link home while it does it, loses the vehicle carrying
that link, elects a replacement out of the two that can still hear each other,
flies it to a slot it computed itself, hands the survey work the winner dropped
to the vehicle that stayed, and finishes the box.

Run `mission_integrated_20260907T101913Z`.

| | |
| --- | --- |
| Coverage | 1.0000, 120 of 120 cells, from pose samples |
| Coverage when the relay died | 0.8000, read 1.5 s after the kill |
| Relay role | moved to `uav_3`, slot clearance 39.4 m |
| Strip reassigned | `uav_3` to `uav_4` at t = 96.3 s |
| Reconnected in | 21.3 s, against a derived budget of 28 and a gate of 45 |
| Delivery ratio | 0.9998, `uav_4` home at 2 forwarders and 3 edges |
| Generated during the outage | 273, all delivered afterwards |
| Observations evicted or expired | 0 and 0 |
| Closest approach | 15.93 m, 0 violations and 0 contacts over 2490 frames |

## What the failure actually does to the swarm

`uav_2` is killed at t = 70 s. It is the middle of the chain, so both surveyors
lose their route to the ground station in the same instant and `uav_1` keeps
its own. Nothing about that is announced: the two surveyors find out because
`uav_2`'s beacons stop arriving and its advertisement ages out.

What happens next is a decision the swarm makes with no ground station in the
loop, because the ground station is on the far side of the break.

The component that can still talk to itself is `{uav_3, uav_4}`. It opens an
election, each member bids its own distance to the attachment node, and the
nearest wins. Mirroring the two survey strips is what makes that a result
rather than a coin toss: flown from opposite ends the two vehicles sit either
side of the centre line for the whole survey, so `uav_3` is nearer by at least
13 m throughout, where the two station-keeping candidates in `relay_kill` are
separated by 0.8 m. On paper 0.8 m is deterministic. In SITL, where position
hold carries its own error, it is not.

The winner computes its own slot: the balance point between the attachment node
and the member that stays, in a reserved altitude band above both. In this run
that put it 39.4 m clear of every other aircraft, with both new hops at
165.3 m, inside the range where delivery is deterministic.

## Buffering, which is the part that is easy to fake

273 observations were minted while there was no route home, and all of them
arrived after the route came back. None was evicted and none expired.

That number is only meaningful because of how delivery is defined. An
observation is identified by its origin and sequence number, the delivery ratio
is a comparison of two sets of identities rather than a count of arrivals, and
the denominator comes from the origins rather than from the destination. A
retry from the origin and a drain from a backlog custodian are both correct and
both arrive; counting arrivals would report a drained backlog as delivered
twice, and a destination-supplied denominator reads 1.0 in every run including
the ones where the relay is dead.

The backlog drained in 0.9 s once the route returned. The queue is sized for a
45 s outage and this run never came near filling it, which is why a separate
scenario exists to go there on purpose.

## The work the elected vehicle drops

`uav_3` was 58% through its strip when it was elected. Its unflown work goes to
`uav_4` rather than being abandoned, and the mechanism is worth a paragraph
because it is where a swarm design usually cheats.

Three hops, one of which crosses a radio. `uav_3`'s executor gives up the
unflown part of its plan when its own role manager sends it to the slot. That
role manager puts the first unflown waypoint into the acknowledgement it was
already sending, so nothing new crosses the mesh. Every other role manager
hears it, and the one whose vehicle is the lowest id still surveying tells its
own executor to take the work.

One point crosses, not a list of waypoints. The vehicle taking over rebuilds
the rest of the plan from the box, the split and the mirroring, all of which it
already holds, and flies the inherited lane at its own altitude rather than the
leaver's, because the altitude layers are what keep two aircraft apart when
their ground tracks cross.

The alternative is one aircraft writing into another's plan directly, which
delivers the handover with no hop, no latency and no chance of the link
dropping it. A static pass over the source and a second pass over the ROS graph
captured mid-run both refuse that, and the two shapes are pinned as fixtures so
the rule is tested rather than trusted.

## Safety, and the control that proves the monitor works

Closest approach over the whole 240 s was 15.93 m against a 10 m floor, with
zero violations and zero contacts across 2490 sampled frames.

A monitor that never fires has not been shown to work, so a separate pair of
runs exists to make it fire. Two vehicles are put on a converging course at the
same altitude with the yield rule on and then off, same seed, same geometry,
one flag different:

| | rule on | rule off |
| --- | --- | --- |
| Closest approach | 20.88 m | 0.37 m |
| Separation violations | 0 | 20 |
| Collision contacts | 0 | 2 |

With the rule on, the higher system id gave way once and held for 2.7 s while
predicting a closest approach of 0.47 m. With the rule off, the pair actually
passed at 0.37 m. The forecast the rule acted on was right to within a few
centimetres, and the lower id never stopped in either run, which is what makes
it a rule rather than both aircraft braking.

Giving way stops the aircraft rather than only the number in the record. A
vehicle flying a timed line holds by stopping its own clock, so the leg after
the hold is the same leg later; a vehicle flying waypoints holds the point it
was at when the rule fired. Either way the run record's hold seconds describe
an aircraft that was actually stationary.

## What this run does not show

The box is small: 25 m by 120 m. That is not a limitation of the survey and it
is the most useful thing in this section. Every survey point has to stay within
175 m of the relay or the observations have no route home, and at least 300 m
from the anchor or killing the relay proves nothing because the surveyors would
reach the ground station directly. The triangle inequality then caps the region
at 350 m from the anchor, and what is left is a sliver of about 3,000 square
metres.

**The area a swarm can survey behind a relay is bounded by radio geometry, not
by battery or flight time.** Widening it means more relays, not bigger
batteries, and that is the engineering trade-off this challenge is actually
about.

Ground truth also arrives at 10 Hz on this stack because Gazebo publishes its
clock at 10 Hz, and nothing sampling against it can go faster. What that costs
is resolution rather than coverage. A pass-through between two frames would
need two aircraft closing faster than the sampling rate can catch, which is why
the separation floor is 10 m and not 1 m, and why the violation count rather
than the contact count is the number the safety claim rests on.
