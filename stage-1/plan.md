# UAV-X Stage 1 plan

**Deadline:** 27 September 2026, by email to pushpak_gc2026@aero.iitb.ac.in
**Working assumption on starting point:** Python solid, ROS and PX4 at tutorial level, networking conceptual, nothing built yet.
**Capacity:** one person, 4 to 6 hours a day. Roughly 160 hours across the 32 days.

Week 1 forks are closed. See [decisions.md](decisions.md) for the stack, the comms interception layer, the vehicle count and the go/no-go gate at the end of every phase.

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

**Gazebo or Gazebo Classic.** Settled on 26 August: Gazebo Classic, on WSL2 Ubuntu 22.04 with ROS 2 Humble and PX4 v1.15. Multi-vehicle goes through `sitl_multiple_run.sh`, and `MicroXRCEAgent udp4 -p 8888` bridges to ROS 2. Reasoning and the version table are in [decisions.md](decisions.md).

**Do not start from zero.** Two open bases worth reading before writing anything:

- **UAVros**, a ROS1 and ROS2 kit for PX4 multi-rotor and UGV swarm simulation
- **Aerostack2**, a ROS2 framework for multi-robot aerial systems with a plugin architecture

**Vehicle count.** Four. One anchored near the GCS, one relay, two surveying. MAVLink `MAV_SYS_ID` caps at 255 so the ceiling is nowhere near us; the limit is debugging hours.

**Where ROS 2 helps.** It runs on DDS, so publish and subscribe is decentralised by default. That is the right substrate for a mesh that has to tolerate losing members, and it is worth saying so explicitly in the proposal.

## Schedule, 32 days

Dates assume a start of 26 August 2026.

| Days | Dates | Goal | Done when |
| --- | --- | --- | --- |
| 1 to 6 | 26 to 31 Aug | Environment only | 4 vehicles airborne at once in SITL, ROS 2 topics visible |
| 7 to 12 | 1 to 6 Sep | Survey mission | All drones fly a waypoint coverage pattern, telemetry lands in one node |
| 13 to 19 | 7 to 13 Sep | Comms layer | Range-gated delivery working, GCS reachable only through a multi-hop relay |
| 20 to 25 | 14 to 19 Sep | Fault recovery | Kill a relay mid-flight, watch the chain rebuild itself |
| 26 to 30 | 20 to 24 Sep | Proposal and video | 6 to 8 pages written, demo recorded |
| 31 to 32 | 25 to 26 Sep | Package and submit | Fresh-machine install test passes, email sent 26 Sep |

Environment gets 6 days rather than 4. Round 1 finding 1: nothing is installed, the machine is Windows, and every step of the PX4 chain has a known way of going wrong. The survey phase absorbed the difference because that phase is 25% of the rubric and the tooling does most of it.

Submit on 26 September, a day early. Email submission means no upload confirmation and no portal to check, so leave a day of slack.

## Notes on each phase

**Environment.** Resist building anything during this week. Multi-vehicle SITL breaks in boring ways, and the only goal is four drones in the air simultaneously with ROS 2 seeing them.

**Comms layer.** This is your actual contribution. A ROS 2 node gates delivery on inter-node distance, with packet loss rising through a band near the threshold. Keep the model simple and defensible; a clean distance threshold you can explain beats a half-understood propagation model. The gate sits behind an interface that a UDP shaper could implement later, so Stage 2 has somewhere to go.

**Fault recovery.** Write failure injection into your own test harness now, not in November. Stage 2 hands you hidden disturbances including UAV failures and comms outages, and a swarm that only works on the happy path scores nothing there. Building the kill switch early means Stage 2 is an extension rather than a rewrite.

**Proposal.** Six to eight pages is short. Spend the space on architecture and on the comms and role-management design, since that is where the marks are. One page on flight is plenty.

**Video.** Show the failure and the recovery. Do not narrate the setup.

## Open items

- Register on techfest.org under Competitions, then PUSHPAK Grand Challenge
- Check nobody on the team is attached to the PUSHPAK project, the Drone Centre or the organising institutions, which is a disqualifier

Team size is settled: solo entry.

Presented by IISERB with IISER Bhopal and VJTI Mumbai. Full rules in `../_shared-timeline.md`, competition detail in `../brief.md`.
