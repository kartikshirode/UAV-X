# Plan review, round 1 (Claude, self-review)

Review of `UAV-X/stage-1/plan.md` and `CycloProp/stage-1/plan.md`, both written 26 August 2026.

This is the first pass of the cross-check. Written to be handed to Codex as the next reviewer, so it states weaknesses rather than defending the plans. Findings are ordered by how much damage they do if left alone.

## Machine baseline, checked

| Item | Value |
| --- | --- |
| CPU | Intel i7-13650HX, 14 cores, 20 logical |
| RAM | 23.7 GB |
| GPU | NVIDIA RTX 4060 Laptop (WMI reports 4 GB, which is the 32-bit field capping out; the part is normally 8 GB) |
| OS | Windows 11 |
| WSL | Installed, but the only distro is `docker-desktop`, stopped. No Ubuntu. |
| git | Present |
| gh | **Not installed** |
| docker | Present |
| Python | 3.12 |
| Node | Present |

The machine is comfortably strong enough for 4 to 6 vehicle PX4 SITL with Gazebo. That risk is closed.

## Finding 1, critical: the UAV-X environment week is understated

**The plan says** days 1 to 4 are "environment only, 4 vehicles airborne".

**The problem.** PX4 SITL, ROS 2 and Gazebo are Ubuntu-first. This machine is Windows with no Ubuntu WSL distro installed. `docker-desktop` is the Docker backend, not a development environment. So the real week 1 chain is:

1. Install WSL2 with Ubuntu 22.04 or 24.04
2. PX4 autopilot toolchain and its dependencies
3. ROS 2 (Humble on 22.04, Jazzy on 24.04) plus uXRCE-DDS agent
4. Gazebo, matched to the PX4 version
5. GUI and GPU working through WSLg, which is where this usually stalls

Every one of those steps has known failure modes. Four days is optimistic, and the plan presents it as slack time.

**Fix.** Either extend the environment phase to 6 days and compress the survey mission phase, or decide up front to run the stack in Docker instead of native WSL, which trades GUI friction for image size. Docker is already installed, so that option is live. Pick before day 1.

**Second-order.** Version coupling matters here. PX4, ROS 2 and Gazebo have to be a matched set. Choosing "latest of each" independently is the most common way this week goes wrong.

## Finding 2, critical: the CycloProp 408 g figure rests on an unverified reading

**The plan says** the module must come in under 408 g, derived from 10 N at T/W above 2.5, and builds the whole mass budget on it.

**The problem.** The brief says "cycloidal rotor module for drone applications, targeting at least 10 N thrust and a thrust-to-weight ratio greater than 2.5." I read T/W as module-level. It could equally be aircraft-level, in which case the module has to be far lighter than 408 g because it is carrying airframe, battery, avionics and payload too.

The arithmetic is right. The premise under it is an assumption I presented as settled.

**Fix.** This goes in the email to the organisers alongside the second-objective question. It changes the design target enough that sizing should not start until it is answered. Until then, size against both readings.

## Finding 3, significant: the CycloProp literature table came from search summaries, not papers

**The plan says** 6 blades at c/R 0.67 with NACA 0015 at 45 degrees and 700 RPM, a 4-blade config at 150 mm radius and 80 mm chord at "24 RPM", and a 1.3 inch radius unit at 4000 RPM.

**The problem.** Those numbers came out of search result summaries, not the source PDFs. The 700 RPM figure is low for a rotor of that scale, and 24 RPM is almost certainly a stripped or misread value, since a cyclorotor at 24 RPM produces essentially nothing. Presenting them in a clean table implied a confidence that is not there.

**Fix.** Week 1 of CycloProp is literature anyway. Pull the actual Sirohi and Benedict papers and rebuild the table from the PDFs. Treat nothing currently in that table as usable until it has been checked against a source. Nothing downstream should cite it.

## Finding 4, significant: the comms layer mechanism is hand-waved

**The plan says** "implement it as a ROS 2 layer that gates message delivery on inter-node distance".

**The problem.** That describes the intent, not the mechanism, and it is the single highest-value component of the entire submission. PX4 instances speak MAVLink over UDP; your ROS 2 code sits above that through the uXRCE-DDS bridge. Where you intercept determines what you can claim:

- **Gate at the ROS 2 application layer.** Straightforward, fully under your control, easy to explain in the proposal. Weaker claim, because the underlying MAVLink traffic still flows freely.
- **Shape actual UDP between instances.** Closer to real radio behaviour and a stronger claim, but more work and more ways to lose a week.

**Fix.** Force this decision in week 1 with the rest of the stack choices, not in week 3 when the phase starts. The honest application-layer version, clearly described and justified, is very likely the right call for Stage 1.

## Finding 5, significant: both schedules assume an unknown

Neither plan asks how many hours a day are available or how many people are on each team. They are written as though one person works on this daily. Teams can be up to 5.

If this runs on evenings around coursework, both schedules are fiction and need rebuilding around real availability. This is the input most likely to invalidate everything else, and it is the cheapest one to fix.

## Finding 6, moderate: no decision gates anywhere

Neither plan says what happens when a phase overruns. Specifically: if 4 vehicles are not flying together by end of week 1, does UAV-X continue, drop to 2 vehicles, or switch to Docker?

**Fix.** Add a go or no-go check at the end of each week with a stated fallback. Week 1 needs one most.

## Finding 7, moderate: no separate problem statement document exists

Checked. There is no PUSHPAK Grand Challenge problem statement PDF published anywhere findable, on techfest.org or through the PUSHPAK mission site. The competition page text is the whole specification.

That cuts both ways. More latitude in how you frame the solution, less guidance on what the evaluators want. Worth one line in the organiser email asking whether a detailed PS exists for either challenge, because if one does, it changes everything downstream.

## Finding 8, minor: gh is not installed

The workflow calls for each project pushing to its own GitHub repository. The GitHub CLI is not on this machine. Small, but it blocks the repo step whenever that starts.

## Questions for the organisers, one email

Send these together rather than in three separate mails:

1. Is the CycloProp thrust-to-weight target of 2.5 measured on the rotor module alone or on the complete aircraft?
2. The CycloProp FAQ references two objectives, then says only one is stated and leaves a note about confirming internally. What is the second objective?
3. Is there a detailed problem statement document for either UAV-X or CycloProp beyond the competition pages?

## What round 2 should check

For Codex, or whoever reviews next:

- Whether the revised week 1 is achievable on WSL2 versus Docker, with the specific version matrix named
- Whether an application-layer comms gate is defensible enough for Stage 1, or whether it undercuts the 25% communication resilience score
- Whether a 6 to 8 page proposal can carry architecture, comms design and role management without going thin, and what to cut if not
- Whether running both challenges survives contact with real weekly hours once those are known
