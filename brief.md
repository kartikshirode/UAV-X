# UAV-X: Resilient BVLOS Swarm Challenge

**Track:** PUSHPAK Grand Challenge 2026
**Prize pool:** Upto INR 25,50,000
**Page:** https://techfest.org/competitions/uav-x
**Status:** open, Stage 1 closes 27 September 2026

Shared timeline, rules and funding ladder are in `../_shared-timeline.md`.

Develop autonomous UAV swarms that hold resilient beyond visual line-of-sight communication while running a disaster response mission: survey the disaster zone, relay data back through a multi-hop aerial network, and reconfigure on the fly as UAVs fail or lose connectivity.

Presented by IISERB in collaboration with IISER Bhopal and VJTI Mumbai.

## Eligibility and team

- UG and PG students in aerospace, robotics, computer science, electronics and communication, electrical or mechanical engineering
- PhD and postdoctoral researchers in unmanned aerial systems, swarm robotics or wireless networking
- Startups and early-stage teams building drones or autonomy stacks

Up to 5 members per team.

## Structure

### Stage 1, preliminary design verification

Submit a 6 to 8 page technical proposal with software architecture, a working proof of concept simulation, source code, installation instructions and a demo video. Up to 15 teams qualify at INR 1 lakh each.

### Stage 2, technical challenge

Qualified teams get advanced disaster scenarios carrying hidden disturbances such as UAV failures and communication outages. Submit final source code, a technical report, simulation logs and a demo video. Top 10 reach the finale.

### Stage 3, grand finale

Present your approach and execute a previously unseen disaster response scenario before the jury at Techfest. Up to 5 winning teams are selected for further funding, mentoring and prototype support.

## Judging

| Criterion | Weight |
| --- | --- |
| Mission completion | 25% |
| Communication resilience | 25% |
| Autonomous relay and role management | 20% |
| Fault recovery and swarm reconfiguration | 15% |
| Safety and collision avoidance | 10% |
| Innovation and technical merit | 5% |

## No hardware required

The challenge is simulation-first, which the organisers say is deliberate to keep the barrier low. No physical drones. Accepted frameworks include PX4 SITL, ArduPilot SITL, Gazebo, AirSim, Isaac Sim, and ROS or ROS 2.

## Research notes

### This is a distributed systems problem

Add up communication resilience, relay and role management, and fault recovery, and 60% of your score sits in networking and role reassignment rather than flight. The flying is table stakes. What is being tested is whether your mesh survives nodes dropping out and reassigns roles without a human.

### Existing scaffolding worth starting from

- **UAVros**, an open ROS1 and ROS2 kit for PX4 multi-rotor and UGV swarm simulation, with modules for multi-UAV target tracking.
- **Aerostack2**, a ROS2 framework for multi-robot aerial systems with a modular plugin architecture and platform independence.
- ROS 2 runs on DDS, giving you decentralised publish-subscribe out of the box. That is the right substrate for a mesh that has to tolerate losing members.
- There is also published ROS2 simulation work on cyberphysical security analysis of UAVs if you want to borrow failure injection patterns.

### Where Stage 2 will break you

Stage 2 introduces disturbances you have not seen: drones failing mid-mission, comms blackouts, unfamiliar scenarios. A swarm that only works on the happy path scores nothing. Build failure injection into your own test harness from the first week rather than bolting it on in November.

### Verdict

The one PUSHPAK challenge a strong student team could plausibly take on without a lab behind it. No airframe budget, no flight permissions, no crash costs. If you have people comfortable in ROS, this is reachable.

## Contact

pushpak_gc2026@aero.iitb.ac.in
