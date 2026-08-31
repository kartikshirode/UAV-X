# Codex context, UAV-X Stage 1

Everything a reviewer needs before reading a line of the plan. Written 29 August 2026, updated 31 August after round 8. Paste this first, then `_codex-prompt.md`.

Nothing here restates a threshold or a parameter. Those live in `scripts/gate.sh` and `stage-1/architecture.md`, and this file's job is to say what the project is and what has already been decided, so a round does not spend itself rediscovering the shape.

**Round 8 was a fix round, and it worked.** Rounds 2 through 7 reported findings and stopped; the author edited. Round 8 found 19 problems, laid out three approaches for each in `_design-options-round8.md`, made the changes itself and iterated to green. Round 9 runs the same way. `_codex-prompt.md` has the procedure.

## The competition

PUSHPAK Grand Challenge 2026, UAV-X: Resilient BVLOS Swarm Challenge. Run by the Drone Centre at IIT Bombay with IISER Bhopal, through Techfest.

Build an autonomous UAV swarm that surveys a disaster location, holds end-to-end communication with a ground control station through a multi-hop aerial network, and reconfigures as UAVs fail or lose connectivity. Simulation only at Stage 1 and Stage 2. Any open framework is allowed.

**Deadline: 27 September 2026**, by email to `pushpak_gc2026@aero.iitb.ac.in`. There is no portal and no form. The plan sends on the 26th, because email gives no upload confirmation and a day of margin costs nothing.

Five things go in that email: a 6 to 8 page technical proposal with the software architecture, a working proof-of-concept simulation, the source code, installation instructions, and a demo video.

Roughly 15 teams qualify from Stage 1, out of 55 registered as of 31 August. Stage 1 qualifiers get INR 1 Lakh each.

### How it is judged

| Criterion | Weight |
| --- | --- |
| Mission completion | 25% |
| Communication resilience | 25% |
| Autonomous relay and role management | 20% |
| Fault recovery and swarm reconfiguration | 15% |
| Safety and collision avoidance | 10% |
| Innovation and technical merit | 5% |

60% of that is comms, roles and recovery. None of the accepted simulators model radio: PX4, ArduPilot, Gazebo and AirSim let every vehicle talk to every other vehicle at any distance forever. So the mesh is the deliverable rather than configuration, and flying is the part the tooling nearly gives away.

### Three rules that are not in the rubric

Easy to skip and all three are cheap. The solution must comply with Indian aviation and safety law. The entrant is responsible for not infringing third-party IP. And the organisers may modify, postpone or cancel any stage, with changes communicated through official channels.

The published record is refetched and diffed every week by `scripts/check_competition_spec.py`. It has fired twice for real. On its first run it caught VJTI Mumbai being removed from the published collaborator list within 24 hours of the capture, which is the sentence the eligibility rule points at. On 30 August it caught a new problem statement published at a new URL, where Django had appended `_1` to the filename; the old URL still served the old bytes, and the document differed by one word. On 31 August it caught the `about` field dropping the words "and presented by IISERB". Three catches in six days, none of which moved an obligation, and the point is that nobody would have noticed any of them by hand.

## Who is building it

One person, 4 to 6 hours a day, roughly 30 hours a week. Windows 11 with WSL2, Ubuntu 22.04, and Docker Desktop installed on the same machine.

Execution is an autonomous agent loop. One tick runs one plan week; inside a week the agent works chunk by chunk. The agent cannot ask questions mid-week. It follows the plan, takes a stated fallback, or halts.

**That is why the plan has to be rigid.** Anything left ambiguous becomes a coin flip made by an agent with no context, and a wrong flip costs days out of 28. Judge everything by "can an agent execute this without guessing", not by "is this reasonable for a human".

## Where things stand on 30 August

Rounds 1 through 8 are done, 77 problems raised and all fixed. `.claude/review-status.json` holds the state; the documents deliberately do not carry it.

**The environment is up and pinned.** Ubuntu 22.04.5, ROS 2 Humble, Gazebo Classic 11.10.2, PX4 v1.15.4, uXRCE-DDS agent v2.4.3, every one by SHA in `stage-1/setup/versions.lock`. `scripts/sitl_multi.sh` brings up 4 vehicles headless with a ROS namespace each.

**The acceptance harness is finished and is most of what exists.** 33 scripts and nine test suites: 42 seam fixtures, 49 submission checks, 20 rehearsal checks, 8 preflight decisions, the scenario, message, record and install-guide contracts, and a grammar suite that parses all 113 `--require` expressions in the gate.

**No implementation code exists.** No `uavx_ws`, no `uavx_eval`, no scenario files, no `run_scenario.sh`. That imbalance is the current situation, not an oversight: the plan was reviewed before being built and the reviews kept finding real defects.

**The plan is four weeks and 25 chunks**, 30 August to 26 September. It was five weeks until 29 August; the first four days produced no implementation, so the packaging tail was distributed into the weeks rather than cutting scope. `stage-1/plan.md`, section "Why four weeks and not five", has the reasoning. Every chunk has its own gate: `bash scripts/gate.sh 1.3` runs one, `bash scripts/gate.sh chunks` lists all 25.

**Human steps still block everything.** `gate_preflight` refuses to start any week without `submission/human-preflight.json`, which needs registration, the WhatsApp clarification channel, the organiser email, an eligibility declaration, a delivery route and a compliance sign-off. None are done. This is the author's to do and is not a finding.

## What the eight rounds were mostly about

One failure mode, over and over: **a check that reports success on a broken system.**

The template is the Gazebo case. The osrfoundation apt repo serves Gazebo Garden on jammy and `gz-tools2` conflicts with `gazebo`, so apt installed the Gazebo Classic libraries and silently skipped the binaries. `verify.sh` asserted on package metadata rather than on the artifact and passed for hours on a machine that could not have run a simulation.

Since then, in this repo:

- A seam checker returning "respect the seam" for a 13-node graph where every endpoint list was empty.
- The same checker, one round later, returning "respect the seam" for a graph where the metrics collector published swarm payload on `/swarm/sidechannel`. It was a deny list naming what to refuse and permitting everything else.
- A submission fixture suite where every tamper case was mutated from a package the checker already rejected, so none of them proved anything.
- A geometry check for the handback that asked about the topology before the handback.
- `check_seam.sh` printing its header, dying on a `grep` under `pipefail`, and checking nothing.
- A W5 fresh-install receipt with three fields that no script in the repository produced.
- An install step labelled "the submitted INSTALL.md path" that ran a different script, against a file that was not in the archive.
- Evidence paths resolved as `REPO / rel`, where an absolute path escapes the repository entirely.
- bash 5.1, which is what the target runs, exiting 0 when a script fails to parse after one successful command. `bash -n` exits 0 on the same file.

Round 8 added nine more to that list. Two are worth naming. A literal `\n` sat where a line continuation was meant, and bash reads that as an argument called `n`, so `bash -n` and every other syntax check stayed green while the called program failed on an option it never received. And a Python shim on PATH could be found but not run, so two byte scans failed silently under a green header.

**Several of those were introduced by the fix for an earlier round.** Round 5 finding 1 and round 7 finding 1 were both self-inflicted by the preceding two commits, and so were two of round 8's, the literal `\n` and the W1 chunk order. Assume the same of anything added in round 8, and assume it of your own edits in this round.

## The environment, and why it is a review topic

Getting the stack up on 26 August took seven failed runs. Four of the seven presented as something other than their cause and two passed a green check while broken:

- DNS dropped when Docker Desktop reconfigured the WSL NAT.
- A long build running off the `/mnt/c` 9P mount lost its connection and died.
- WSL terminated the distro whenever no client was attached, killing detached builds.
- `gazebo --version` crashes the entire WSL distribution through the dxg GPU shim.
- The Gazebo Garden and `gz-tools2` conflict above.
- The Micro XRCE-DDS Agent version the PX4 docs name pins a Fast DDS branch that no longer exists upstream.
- `ffmpeg`, `ffprobe`, `pdftotext` and `pdfinfo` were absent from the target distro while `verify.sh` reported a healthy stack, because they had been added under a setup stamp the machine already had.
- `stage-1/setup/04-px4.sh` carried CRLF in the working tree, so it would have died on line 1 under bash while `git ls-files --eol` showed the index was clean.

Four PX4 instances plus gzserver plus our nodes have to fit in roughly 11 GB of WSL memory. Measured at 800 MB idle for the stack, never under load, which is why memory sampling is a W1 chunk with a 10500 MiB ceiling.

## The files, and what each one owns

| File | Owns |
| --- | --- |
| `context.md` | what the organisers published. Outranks everything. |
| `stage-1/plan.md` | what gets built when, and what proves it. Four weeks, 25 chunks. |
| `stage-1/architecture.md` | the frozen design. Every parameter, protocol, message and coordinate. |
| `stage-1/decisions.md` | the locked calls, the capacity budget and the one fallback. |
| `scripts/gate.sh` | **the only acceptance contract.** No other file restates a threshold. |
| `.claude/weekly-loop.md` | the automation config and the rules digest handed to each week-agent. |
| `.claude/review-status.json` | which round has run and what is outstanding. |
| `scenarios/run-record.schema.json` | the provenance contract every run must satisfy. |
| `submission/human-preflight.schema.json` | the human steps that block the loop. |

Round 2 found the gates written out in three documents that had already drifted, with one version failing a correct implementation. Prose cannot be the source of truth for something a machine enforces. `scripts/check_docs.py` now fails any document that restates a threshold, contradicts the gate, names a deliverable the gate does not check, or states a different W4 fallback from the other two documents.

## The design in one page

Four vehicles and a ground station. `uav_1` anchors near the GCS, `uav_2` and `uav_4` survey, `uav_3` is the spare.

**The tx/rx seam.** Swarm nodes publish only to `/uavx/<own>/tx` and subscribe only to `/uavx/<own>/rx`. Everything between those topics is the link layer, which is the only thing that knows about range. Break the seam and the vehicles are talking over a perfect simulator radio, which voids the 25% communication row while every test still passes. `scripts/check_seam.sh` enforces it over the source and over a ROS graph captured during a run, with an allowlist per outside process and a ban on swarm payload anywhere but tx and rx.

**Routing.** Each node floods its neighbour table, builds a graph and runs Dijkstra to `gcs`. Route key is `(hop_count, temporary_relay_count)`, so fewer hops always wins and a relay only breaks an equal-hop tie. A new route must win twice before it replaces the installed one.

**Roles.** On a disconnection the component elects a relay by distance, assigns it a slot in a reserved altitude band no mission corridor enters, and holds the role on a lease. On reconnection the epoch owner runs a make-before-break handback: the relay keeps forwarding until the GCS acknowledges an observation over the new path.

**Store and forward.** Observations carry `(origin_id, sequence, created_at, expires_at)`. Delivery is a set comparison on `(origin_id, sequence)`, not a count, so "delivered once" is provable rather than asserted. The record carries the outage window and what was generated inside it, so a queue claim cannot be met by producing the data before the route went down.

**Nine scenarios**, all with frozen coordinates in `architecture.md` section 6, all derived by `scripts/check_geometry.py` rather than asserted. Three of the nine are controls: one where the relay must fail, one where the yield rule is off, one where forwarding is off. A recovery that is never shown failing has not been shown. A tenth, `harness_check.yaml`, exists only to prove the harness and is never cited as evidence.

## What this project is not doing

Stage 2 and Stage 3 are out of scope. So is hardware; there is none in the challenge. `gps_degrade` and `comms_blackout` are named as the intended Stage 2 upgrade and are deliberately absent here.
