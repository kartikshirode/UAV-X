# Mission and coverage

Drafted in week 2 from the runs week 2 produced, and owed since then. Standing
rule 5: a section a week. Every number below names the run it came off.

Target length in the assembled proposal: about 1 of the 6 to 8 pages. This is
the mission completion row of the rubric, worth 25%, and the tooling hands most
of it to you. What it does not hand you is a coverage figure anybody should
believe, which is what the second half of this section is about.

## The claim

Four vehicles flying a 200 m by 200 m box covered all 400 of its cells in 420 s
of simulated time. The figure is taken from where the aircraft actually were,
not from where the planner meant to send them, and the record says so in a field
beside it.

Run `survey_baseline_20260904T022321Z`. 16,692 sampled poses, 4,269 ground truth
frames, a peak of 947.5 MiB with no swap, `coverage_fraction` 1.0 and
`coverage_source` `pose_samples`.

## The plan

The box is split into vertical strips, one per surveying vehicle, and each
vehicle flies a boustrophedon over its own strip: down one lane, across by one
lane spacing, back up the next. Lane spacing comes from the sensor footprint, so
the strips tile the box exactly and the lanes cover the strip they are in.

Two properties are worth stating because they are the ones that go wrong.

**The partition is deterministic and it is by id.** A vehicle looks up its own
strip by name rather than by position in a list, so a vehicle that starts late,
or is restarted, flies the same strip it would have flown. Nothing depends on
the order the runner happened to launch them in.

**The executor never advances to the nearest waypoint.** It advances only when
the vehicle actually arrives at the waypoint it was flying to, one at a time, in
order. Nearest-waypoint looks equivalent and is not: a vehicle blown across its
strip is nearer the far end of the next lane than the near end of the one it is
on, so it skips the rest of that lane. The ground under it is never flown,
coverage drops by the width of one swath, and the plan the run reports is the
plan that was never flown.

Progress through a strip is a distance and not a waypoint count. A lane is the
height of the box and a turn is one lane spacing, so counting waypoints reports
the same progress for a 200 m lane and a 17 m turn.

The planner knows nothing about any particular scenario. Handed the frozen
`mission_integrated` box it reproduces, to the digit, the 4 lane positions the
design freezes and the geometry checker flies. Two independent routes to the
same 4 numbers is the one thing a mirror test cannot be.

## What a coverage figure is allowed to mean

A cell counts when its centre fell inside the sensor footprint of at least one
sampled pose. The footprint is a horizontal disc under the vehicle, so altitude
does not enter it, and the denominator is the number of cells the box tiles
into. A box that does not tile exactly is refused outright, because the leftover
strip would be a denominator nobody wrote down.

The poses come from ground truth, sampled by a process that is not part of the
swarm and does not read any vehicle's plan. That is the whole point. A planner
reporting its own intentions scores the same whether the vehicles flew or sat on
the pavement, and the record carries `coverage_source` so a reader can tell
which of the two they are looking at without trusting a sentence.

It took 4 attempts to get a figure that meant anything, and the first 3 are the
reason this section exists.

## What flying it found

Chunk 2.4 was the first time this project started its own nodes inside a
simulator. Four defects surfaced in the first four minutes, and every one of
them sat in code that already had tests passing over it.

**The topic the coverage metric is scored from did not exist.** The Gazebo
plugin that publishes model states is a WorldPlugin, and `gzserver -s` loads
SystemPlugins, so the launcher's attempt to load it produced one line in a log
nobody was reading and no ground truth at all. The collector had 64 passing
tests because they hand it poses directly. The first survey flew the whole run
and reported nothing covered. The captured graph is the proof: `/gazebo`
published the clock, its own performance metrics and nothing else.

**The vehicles landed halfway through and kept reporting offboard.** The
offboard heartbeat was published inside the position callback, so it depended on
another topic's cadence and stopped outright when the plan finished, which is
exactly when the aircraft is airborne and needs to hold. PX4's own SITL
configuration drops offboard after half a second without one. The log reads:
offboard granted, failsafe activated, matching flight task was not able to run,
landing detected, disarmed by landing. The heartbeat has its own timer now, well
above the rate PX4 asks for and independent of everything else.

**The mission node could not construct itself.** `rclpy.node.Node` defines
`executor` as a property whose setter calls `add_node` on whatever it is handed,
so `self.executor = MissionExecutor(...)` raised in every vehicle. An attribute
named after the thing the class is about had walked into a name the base class
already owned. There is a test now that reads the reserved names out of rclpy
and the assignments out of the source tree, so neither side of the comparison is
a copy that can drift, and it scans every package rather than the one that had
the bug.

**The collector's final payload could never publish.** `rclpy.init` installs
signal handlers that shut the context down before the exception reaches any
`finally`, so the one payload marked final, the one the record is built from,
always failed with an invalid context.

The pattern is the same in all four: arithmetic proved with no simulator, wiring
never once executed. That is a fair argument for proving the arithmetic
separately, and a fair argument for not calling it done.

## The limit worth stating

Gazebo publishes its clock at 10 Hz on this stack and nothing sampling against
it can go faster. The survey took its 4,269 ground truth frames at exactly
10.0 Hz against a 20 Hz target, and the same ceiling has been attacked 3
different ways. What it costs is resolution rather than coverage: a cell missed
between two frames at 10 Hz would have to be missed by a vehicle moving faster
than 6 m per frame, and the survey lanes are flown at 3.

The separation floor and the altitude layer spacing are both 10 m, which means
two vehicles on adjacent layers passing one above the other sit exactly on the
floor and any altitude error at all breaches it. PX4 holds altitude to about a
tenth of a metre. This run recorded a closest approach of 9.90 m and 1 frame
under the floor, which is that arithmetic rather than a control failure. The
scenarios that assert separation put the vehicles further apart than one layer,
and the yield rule in week 4 is what handles the case where they do not.
