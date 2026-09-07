# Software architecture

Drafted in week 1 from the runs week 1 produced, and owed since then. Standing
rule 5: a section a week, so the submission tail is a freeze and a send. Every
number below names the run it came off.

Target length in the assembled proposal: about 1.5 of the 6 to 8 pages. It goes
first, because the two sections after it are claims about a system and this is
the description of the system they are claims about.

## The shape of it

Seven ROS 2 packages, and the split between them is the argument.

| Package | What it holds |
| --- | --- |
| `uavx_msgs` | 5 message types, frozen. Nothing else in the workspace defines one |
| `uavx_mission` | The survey planner, the strip partition, the executor, the coordinate frames |
| `uavx_comms` | The radio model, the router, link-state routing, the election, the yield rule |
| `uavx_gcs` | The ground station, and the definition of what counts as delivered |
| `uavx_roles` | Role grants, leases and the state machine that moves the relay |
| `uavx_eval` | Coverage, separation, and the provenance check over every run record |
| `uavx_sim` | The harness: scenario loading, fault injection, graph capture, the record writer |

Four of the seven contain no ROS at all. The planner, the routing, the ledger
and the coverage grid are arithmetic over tuples, and they are proved with no
simulator running and no graph up. What is left in the node files is wiring,
which is the part a unit test cannot honestly cover, so there is as little of it
as the job allows. Week 2 is the evidence for that split being worth having: 24
mutations of the planner and 52 of the comms logic were each written wrong on
purpose and each caught by a test that needs no simulator.

## The one rule the design is built on

**PX4 and Gazebo model no radio.** There is no range limit, no packet loss and
no link budget anywhere in the simulator. Every vehicle can talk to every other
at any distance, always. So a swarm built on ROS topics between vehicles is not
a mesh being tested; it is four processes sharing a bus, and it would report a
delivery ratio of 1.0 in every scenario including the ones where the relay is
dead.

The radio is therefore a thing this project builds, and the boundary is
enforced rather than described:

- A swarm node publishes only to `/uavx/<own>/tx` and subscribes only to
  `/uavx/<own>/rx`. Its own id builds both, so no file in the swarm ever names
  a second vehicle's endpoint.
- One link layer process stands outside the swarm, holds every vehicle's
  position, and decides what crosses. It is the only process allowed to read
  simulator ground truth.
- The ground station is a node in that graph like any other, at the origin,
  with one transmit and one receive endpoint.

Two passes hold the line. A static pass over the source refuses any file that
names two vehicles' endpoints, reads ground truth or opens a service between
swarm nodes. A second pass reads the live ROS graph captured mid-run and
compares every publisher and subscriber against a manifest written per
scenario. The static pass has 53 fixtures behind it, each a file that should
pass or should fail for one named reason.

## Two frames, and both of them are silent when wrong

Everything in the design is frozen in one frame: local ENU metres, origin at the
ground station, x east, y north, z up. PX4 reports and accepts a different one:
NED metres, origin at the vehicle's own home, x north, y east, z down.

Two things differ at once and each of them fails quietly. The vehicles spawn on
a line centred on the world origin, so every one of them has a different home,
and a survey plan handed straight to PX4 flies the box relative to wherever that
vehicle happened to start. Four vehicles then fly four different boxes that all
look plausible in their own logs. The axis half is worse, because a square box
with x and y swapped comes back a square box. It is the wrong square, rotated
into the wrong part of the world, and nothing about its shape says so.

So the conversion is one function that does both, in one order, in one place,
and no other file in the workspace may subtract a home or reorder an axis. The
home itself is never derived: the launcher writes where it put each vehicle when
it places them, and the runner carries that into every record.

## What a result is

A number in this proposal is a number some run produced, and the run says which
source produced it.

Every record carries `source_tree_sha256`, a digest over `uavx_ws/`,
`scenarios/`, `scripts/` and `stage-1/setup/` taken as git's own object ids, so
a hash computed from a working tree and one computed from a clean checkout of
the same commit are identical. Editing a sentence in a design document does not
invalidate a week of runs, and editing the router does. The record also carries
the scenario file's own hash, the seed, the version of every component in the
stack, and the sha256 of the ROS graph snapshot captured during the run.

The provenance check reads all of that before it reads a single metric, and it
refuses a record whose source hash is not the tree, whose scenario file has
moved, whose commit is not real, that was cut short, that did not complete,
whose injected fault never fired, or that has a zero denominator. Each of those
13 rules was disabled in turn and its test watched failing. Two checkers in this
repository had never been shown to fail before that exercise, which is why it
was done.

## The harness, and what it cost to trust it

`harness_check_20260901T111030Z` is the run week 1 was accepted on: 4 vehicles,
60 s of simulated time, 836 pose samples, one injected fault requested and
observed, a peak of 504.262 MiB with no swap. It was made against the tree
committed at `3415c3d` and its source hash is the digest of that commit.

Six runs sit behind that one, and the altitudes are why there are six.

| Run | uav_1 | uav_2 | uav_3 | uav_4 | Preparation |
| --- | --- | --- | --- | --- | --- |
| `...095331Z` | 30.18 | 40.23 | **11.98** | 60.23 | 121.1 s |
| `...104152Z` | 30.41 | 40.44 | 50.45 | 60.45 | 26.2 s |
| `...111030Z` | 30.38 | 40.44 | 50.44 | 60.45 | 25.8 s |

The first row is a vehicle that was told to climb to 50 m and levelled off at
12. The four aircraft spawn on a line and a vehicle placed too near its
neighbour fights a failsafe the whole way up, which also explains the 121 s of
preparation. Widening the spacing fixed both, and the first row is kept because
a table with only the working runs in it proves nothing about the fix.

Week 1 found 14 defects in the acceptance harness itself and week 2 found 7
more, and 5 of those 7 would have failed correct code rather than passed broken
code. Two examples give the flavour. A `grep -q` inside a pipeline under
`pipefail` closes the pipe on its first match, the producer takes SIGPIPE, and
the check inverts; that pattern had bitten this repository 5 times before it was
banned outright. And `bash` exits 0 when a script fails to parse after one
successful command, so a shell script saved with Windows line endings dies on
line 1 and reports success. The setup verifier did exactly that and printed "all
checks passed" having run none of them.

The stack is pinned by SHA and apt version: Ubuntu 22.04.5, ROS 2 Humble, Gazebo
Classic 11.10.2, PX4 v1.15.4, uXRCE-DDS agent v2.4.3. Picking the newest of each
independently is the most common way this kind of week goes wrong, and the
install notes say which 7 failed attempts produced each pin.
