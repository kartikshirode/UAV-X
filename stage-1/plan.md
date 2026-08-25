# UAV-X Stage 1 plan

**Deadline:** 27 September 2026, by email to pushpak_gc2026@aero.iitb.ac.in
**Working assumption on starting point:** Python solid, ROS and PX4 at tutorial level, networking conceptual, nothing built yet.

## What Stage 1 actually wants

Five things, all in one email:

1. Technical proposal, 6 to 8 pages, covering software architecture
2. A working proof of concept simulation
3. Source code
4. Installation instructions
5. Demonstration video

Register on techfest.org first. There is no submission portal anywhere in this track, so the email is the submission.

## The scope call

A Stage 1 proof of concept does not have to be the full resilient swarm. It has to make the core mechanism believable. Getting this scope right is the difference between finishing and not.

Look at where the marks sit:

| Criterion | Weight |
| --- | --- |
| Mission completion | 25% |
| Communication resilience | 25% |
| Autonomous relay and role management | 20% |
| Fault recovery and swarm reconfiguration | 15% |
| Safety and collision avoidance | 10% |
| Innovation and technical merit | 5% |

Communication resilience, relay and role management, and fault recovery come to 60%. Flying is 25% and it is the part the tooling gives you for free.

### The thing most teams will get wrong

PX4 and Gazebo hand you flight, waypoints and physics. Neither one models radio. There is no range limit, no packet loss, no link budget in the default stack. Every drone talks to everything, always.

So the mesh layer is not something you configure. It is the thing you build, and it is 60% of the score. Budget accordingly: flight should be a week, the comms layer should be two.

### Minimum viable proof of concept

- 4 to 6 drones in PX4 SITL with Gazebo and ROS 2
- A survey mission covering a defined disaster area
- A range-gated communication layer, so messages only pass between nodes within a modelled radio range
- At least one drone deliberately placed outside direct range of the ground control station, reaching it through a relay
- Failure injection: kill a relay node mid-mission and have the swarm notice, reassign the relay role, and re-establish the chain

That last item is the demonstration. If the video shows a drone dropping out and the mesh healing itself without a human touching anything, you have shown 60% of the rubric in about 20 seconds.

## Technical decisions to lock in week 1

**Gazebo or Gazebo Classic.** PX4 documents multi-vehicle for both. New Gazebo runs the first instance normally and every additional one with `PX4_GZ_STANDALONE=1`, distinct model poses and distinct system IDs, with `MicroXRCEAgent udp4 -p 8888` bridging to ROS 2. Gazebo Classic has `sitl_multiple_run.sh`, which defaults to 3 vehicles. Either works. Pick one on day 2 and stop reconsidering.

**Do not start from zero.** Two open bases worth reading before writing anything:

- **UAVros**, a ROS1 and ROS2 kit for PX4 multi-rotor and UGV swarm simulation
- **Aerostack2**, a ROS2 framework for multi-robot aerial systems with a plugin architecture

**Vehicle count.** MAVLink `MAV_SYS_ID` caps at 255 vehicles, so nothing near your range is a limit. Use 4 to 6. More drones look impressive and cost you debugging time you do not have.

**Where ROS 2 helps.** It runs on DDS, so publish and subscribe is decentralised by default. That is the right substrate for a mesh that has to tolerate losing members, and it is worth saying so explicitly in the proposal.

## Schedule, 32 days

Dates assume a start of 26 August 2026.

| Days | Dates | Goal | Done when |
| --- | --- | --- | --- |
| 1 to 4 | 26 to 29 Aug | Environment only | 4 vehicles airborne at once in SITL, ROS 2 topics visible |
| 5 to 11 | 30 Aug to 5 Sep | Survey mission | All drones fly a waypoint coverage pattern, telemetry lands in one node |
| 12 to 18 | 6 to 12 Sep | Comms layer | Range-gated delivery working, GCS reachable only through a multi-hop relay |
| 19 to 24 | 13 to 18 Sep | Fault recovery | Kill a relay mid-flight, watch the chain rebuild itself |
| 25 to 29 | 19 to 23 Sep | Proposal and video | 6 to 8 pages written, demo recorded |
| 30 to 32 | 24 to 26 Sep | Package and submit | Fresh-machine install test passes, email sent 26 Sep |

Submit on 26 September, a day early. Email submission means no upload confirmation and no portal to check, so leave a day of slack.

## Notes on each phase

**Environment.** Resist building anything during this week. Multi-vehicle SITL breaks in boring ways, and the only goal is four drones in the air simultaneously with ROS 2 seeing them.

**Comms layer.** This is your actual contribution. Implement it as a ROS 2 layer that gates message delivery on inter-node distance, then add packet loss that rises with range. Keep the model simple and defensible. A clean distance threshold you can explain beats a half-understood propagation model.

**Fault recovery.** Write failure injection into your own test harness now, not in November. Stage 2 hands you hidden disturbances including UAV failures and comms outages, and a swarm that only works on the happy path scores nothing there. Building the kill switch early means Stage 2 is an extension rather than a rewrite.

**Proposal.** Six to eight pages is short. Spend the space on architecture and on the comms and role-management design, since that is where the marks are. One page on flight is plenty.

**Video.** Show the failure and the recovery. Do not narrate the setup.

## Open items

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- Confirm team size, up to 5 members
- Check nobody on the team is attached to the PUSHPAK project, the Drone Centre or the organising institutions, which is a disqualifier

Presented by IISERB with IISER Bhopal and VJTI Mumbai. Full rules in `../_shared-timeline.md`, competition detail in `../brief.md`.
