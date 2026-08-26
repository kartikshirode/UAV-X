# UAV-X context

Everything the organisers have actually published, pulled 26 August 2026, plus what it means for the build. This is the grounding file. When the plan and this file disagree, this file wins, because it is sourced and the plan is a judgement call.

Anything marked **our reading** is interpretation, not organiser text. Keep the line between them intact.

## Where this came from

techfest.org is a React app that loads competition content from an API at runtime, so fetching the page HTML gets you an empty shell. The real record is at `https://techfest.org/api/compis/`, public JSON, no auth. Filter for `compi_id == "uav-x"`.

The saved copy is [research/techfest-uav-x.json](research/techfest-uav-x.json), and the linked problem statement is [research/UAV-X-problem-statement.pdf](research/UAV-X-problem-statement.pdf).

Two corrections to earlier notes in this repo:

- Round 1 of the plan review said no problem statement document exists. One does. It is a single page and it is a cover sheet, so the practical conclusion held, but the claim was wrong.
- There are three PUSHPAK challenges, not two. UAV-X, CycloProp, and Security of Drones (`secofdrones`), sharing the same INR 25,50,000 ceiling and the same contact address.

As of 26 August, 17 teams had registered for UAV-X.

## The challenge, in organiser words

> Teams develop autonomous UAV swarms capable of maintaining resilient beyond visual line-of-sight (BVLOS) communication while executing a disaster response mission, surveying disaster zones, relaying data through a multi-hop aerial network, and reconfiguring on the fly as UAVs fail or lose connectivity.

From the FAQ, the sharpest single statement of what gets built:

> An autonomous UAV swarm that surveys disaster locations, maintains end-to-end communication with a Ground Control Station through a resilient multi-hop aerial network, and reconfigures dynamically as UAVs fail or lose connectivity, all in simulation.

Funded by MeitY under PUSHPAK, the National Mission on Drone Technology. Hosted by IIT Bombay. Presented by IISERB with IISER Bhopal and VJTI Mumbai.

The organisers say plainly that this is not a hackathon. It runs about 4 months, through evaluation and mentoring, toward field-ready prototypes, and winners go into a Prototype Development Support Programme from December 2026.

## Dates, all three stages

| When | What |
| --- | --- |
| 22 Aug 2026 | Announcement, registrations open |
| 22 Aug to 27 Sep 2026 | Stage 1 work period |
| **27 Sep 2026** | **Stage 1 submission deadline, on or before** |
| 2 Oct 2026 | Stage 1 results |
| 3 Oct to 2 Dec 2026 | Stage 2 work period |
| 2 Dec 2026 | Stage 2 submission deadline |
| 6 Dec 2026 | Stage 2 results |
| 16 to 18 Dec 2026 | Grand Finale at Techfest, IIT Bombay |

We started on 26 August, 4 days into a 37 day Stage 1 window.

## What each stage wants

**Stage 1, preliminary design verification.** A 6 to 8 page technical proposal with software architecture, a working proof-of-concept simulation, source code, installation instructions, and a demo video. Around 15 teams qualify and get INR 1 lakh each.

**Stage 2, technical challenge.** Qualified teams get advanced disaster scenarios carrying hidden disturbances, named as UAV failures and communication outages. Deliverables are final source code, a technical report, simulation logs, and a demo video. Top 10 go through.

**Stage 3, grand finale.** Present the approach and execute a previously unseen disaster response scenario in front of the jury. Up to 5 teams win.

Submission is email only, to **pushpak_gc2026@aero.iitb.ac.in**. The organisers say it twice: no portals, no forms, every stage handled by email correspondence.

## Judging

| Criterion | Weight |
| --- | --- |
| Mission completion | 25% |
| Communication resilience | 25% |
| Autonomous relay and role management | 20% |
| Fault recovery and swarm reconfiguration | 15% |
| Safety and collision avoidance | 10% |
| Innovation and technical merit | 5% |

The FAQ calls this "seven weighted criteria" and then lists six. The six sum to exactly 100, so the count is almost certainly a slip. Worth one line in the organiser email anyway, in case a seventh exists that nobody published.

**Our reading.** Communication resilience, relay and role management, and fault recovery come to 60%. Flight and mission execution are 25%. Safety is 10%.

That ratio decides the project, because of a fact the rubric never states: PX4, ArduPilot, Gazebo and AirSim model no radio. No range limit, no packet loss, no link budget. Every vehicle talks to every other vehicle at any distance, always. So the multi-hop mesh this challenge is named after does not exist in any of the accepted frameworks and cannot be switched on. It has to be built, and it carries 60% of the marks. Flying is 25% and the tooling gives it away nearly free.

## Money

| Stage | What you get |
| --- | --- |
| Stage 1 qualifiers | INR 1 lakh each, roughly 15 teams |
| Stage 2 qualifiers | Domestic travel and accommodation, per IIT Bombay guidelines |
| Stage 3 winners | INR 1.5 lakh to 3 lakh |

"Upto INR 25,50,000" is the aggregate across the programme, not a single prize. The disbursement process gets announced later.

## Eligibility, and the rule that ends a run

Open to UG and PG students in aerospace, robotics, computer science, electronics and communication, electrical or mechanical engineering; PhD and postdoctoral researchers in unmanned aerial systems, swarm robotics or wireless networking; and startups or early-stage teams. Up to 5 members.

The disqualifier, stated in the rules and again in the FAQ:

> Project staff, research staff, consultants, interns, or other personnel directly engaged with the PUSHPAK Project, Drone Centre, or the organizing/host institutions are not eligible, whether participating individually or as part of a team.

"May be disqualified at any stage." We are entering solo, so this is one check rather than five.

Other rules that bite: participants own their IP and must not infringe anyone else's; solutions must comply with Indian aviation law and safety rules; all expenses fall on the team; the evaluation committee's decision is final; the organisers can modify or cancel any stage.

## Tooling, as permitted

> No. The challenge is simulation-first, keeping the barrier to entry low. Participants can use any open-source framework such as PX4 SITL, ArduPilot SITL, Gazebo, AirSim, Isaac Sim, or ROS/ROS 2.

No physical drones in Stage 1 or Stage 2. Our stack, PX4 SITL with Gazebo Classic and ROS 2 Humble, sits inside that list, so there is nothing to ask and nothing to defend. Version set and reasoning: [stage-1/decisions.md](stage-1/decisions.md).

## What nobody has published

Gaps in the official material. Each one we either decide ourselves or ask about.

- No scenario specification. Nothing on disaster zone size, terrain, mission duration, or what counts as surveyed.
- No swarm size, minimum or maximum.
- No radio model, no range figures, no link budget. The threshold we pick is ours to justify.
- No measurable definition of "mission completion", despite it carrying 25%.
- No video length or format limit.
- Nothing on whether source code is attached or linked.
- Nothing on whether the 6 to 8 pages include references and appendices.

The last three are in the drafted organiser email. The first four are design freedom, and **our reading** is that a submission defining its own metrics and reporting them scores better than one leaving them implicit. Evaluators working through 17 or more entries reward anything they can grade quickly.

## Channels

- Email, and the actual submission route: pushpak_gc2026@aero.iitb.ac.in
- WhatsApp group for UAV-X: https://chat.whatsapp.com/EdOZigIfR4s0LvBl4N49XB
- Registration: techfest.org, Competitions, PUSHPAK Grand Challenge

Clarifications will land in the WhatsApp group first. Worth joining before mailing anything, since the answer may already be sitting there.

## Re-fetching this

```bash
curl -sSL https://techfest.org/api/compis/ -o compis.json
python -c "import json;d=json.load(open('compis.json'));print([x for x in d if x['compi_id']=='uav-x'][0])"
```

Rerun it before submitting. The organisers reserve the right to change the rules or the timeline, and this is the only way we would find out.
