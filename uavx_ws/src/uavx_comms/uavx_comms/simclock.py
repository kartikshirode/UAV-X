"""Chunk 4.2: a node on simulated time does nothing before its clock is live.

Found in the accepted `relay_required` run, where the ground station reported
a `control_queue_max_delay_s` of 113.3 s in a scenario with no outage in it.
Nothing was backed up. 113.3 s is the simulated time at which the radio came
up, and the packet that read it had been stamped 0.

Under `use_sim_time` a node's clock reads exactly zero until the first
`/clock` message reaches it, and a subscription can be matched before that
happens. So the first packet through the door is timestamped at the origin of
time, and every duration measured from it is the whole run. The routers showed
0.1 and 0.2 s, which is one tick and two, and the ground station showed the
age of the universe.

Week 3 never read the number, so it cost nothing. `queue_drain` asserts a
control queue delay at or under 50 ms and `link_loss` asserts a backlog drain
under 2.25 s, and both would have been decided by which subscription matched
first.

The rule is one line: until the clock is live, a node holds what arrives and
does not act on it. Holding rather than dropping, because a dropped
observation is a delivery ratio that is wrong in the direction of looking
worse, and a dropped HELLO is a neighbour that appears late for no reason
anybody could find later.

The same module carries the other end of a node's life on this clock. A run
ends when the runner signals every process at once, and a node that exits on
the signal leaves whatever it was carrying in flight. So the last thing a
node does is stop producing new work and keep carrying what it already has,
for a window measured on the same simulated clock.

    ClockGate   the question, the holding pen and the count of both
    Drain       how long a node keeps working after it is asked to stop
"""

from __future__ import annotations

import math
from typing import Any, List

# A clock reading at or under this is not a clock, it is the absence of one.
# Simulated time starts at zero and the first `/clock` message this stack
# publishes carries a positive time, so anything at zero means no message has
# arrived. Wall time is seconds since 1970 and is never near it.
LIVE_ABOVE_S = 0.0

# How much a node holds while it waits. The radio settles for 8 s before a
# scenario starts and the traffic in that window is discovery, so this is far
# more than the buffer ever needs. It exists so that a misconfigured node with
# no clock at all fills a bounded buffer and says so, rather than growing
# until the run dies of memory.
HOLD_CAPACITY = 512

HELD = "held"
DROPPED_FULL = "dropped_full"

# How long a node keeps carrying after it has been asked to stop.
#
# Derived rather than chosen. The deepest backlog the design is sized for is
# 450 observations at a forward rate of 200 a second, which is 2.25 s, and an
# observation is not finished until its acknowledgement has walked back down
# the path: four hops out and four back at 20 ms each is another 0.16 s. 2.41
# rounded up to 3.0 leaves the tick the node was in the middle of.
SHUTDOWN_DRAIN_S = 3.0

# The wall clock backstop. The window is measured in simulated seconds because
# that is what the packets move on, and a stack whose /clock has already
# stopped would otherwise wait for a reading that will never change. The
# runner allows each node 10 s to exit before it kills it, so this sits
# safely inside that.
DRAIN_WALL_CAP_S = 8.0

# Why a drain ended, in the ledger the node writes afterwards.
DRAIN_WINDOW = "window"
DRAIN_WALL_CAP = "wall_cap"
DRAIN_NO_CLOCK = "no_clock"


class Drain:
    """The window one node stays up for after it is asked to stop.

    The policy lives here rather than in the spin loop so it can be tested
    without ROS. The loop asks `carrying` and does nothing else.
    """

    def __init__(self, window_s: float = SHUTDOWN_DRAIN_S,
                 wall_cap_s: float = DRAIN_WALL_CAP_S) -> None:
        if not isinstance(window_s, (int, float)) or window_s < 0:
            raise ValueError(
                f"window_s is {window_s!r}; a negative drain is a node that "
                f"stops before it was asked to")
        if not isinstance(wall_cap_s, (int, float)) or wall_cap_s <= 0:
            raise ValueError(
                f"wall_cap_s is {wall_cap_s!r}; without a backstop a node "
                f"whose clock has stopped never exits")
        self.window_s = float(window_s)
        self.wall_cap_s = float(wall_cap_s)
        self.started = False
        self.sim_start: float = float("nan")
        self.wall_start: float = float("nan")
        self.sim_now: float = float("nan")
        self.wall_now: float = float("nan")
        self.reason = ""

    @staticmethod
    def _usable(value) -> bool:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return math.isfinite(float(value))

    def start(self, sim_now: float, wall_now: float) -> bool:
        """Open the window. False when there is no clock to measure it on.

        A node whose simulated clock never went live has nothing to carry:
        every packet it holds is still in the gate, unstamped. It exits.
        """
        if self.started:
            return self.reason == ""
        self.started = True
        if not self._usable(sim_now) or float(sim_now) <= LIVE_ABOVE_S:
            self.reason = DRAIN_NO_CLOCK
            return False
        if not self._usable(wall_now):
            self.reason = DRAIN_NO_CLOCK
            return False
        self.sim_start = float(sim_now)
        self.wall_start = float(wall_now)
        self.sim_now = self.sim_start
        self.wall_now = self.wall_start
        return self.window_s > 0.0

    def carrying(self, sim_now: float, wall_now: float) -> bool:
        """Whether the node should keep working, and why it stopped."""
        if not self.started or self.reason:
            return False
        if self._usable(sim_now):
            self.sim_now = float(sim_now)
        if self._usable(wall_now):
            self.wall_now = float(wall_now)
        if self.wall_now - self.wall_start >= self.wall_cap_s:
            self.reason = DRAIN_WALL_CAP
            return False
        if self.sim_now - self.sim_start >= self.window_s:
            self.reason = DRAIN_WINDOW
            return False
        return True

    @property
    def drained_s(self) -> float:
        if not self.started or math.isnan(self.sim_start):
            return 0.0
        return max(0.0, self.sim_now - self.sim_start)

    def as_record(self) -> dict:
        wall = (0.0 if math.isnan(self.wall_start)
                else max(0.0, self.wall_now - self.wall_start))
        return {
            "drain_window_s": self.window_s,
            "drained_sim_s": round(self.drained_s, 3),
            "drained_wall_s": round(wall, 3),
            "drain_stopped_by": self.reason or None,
        }


class ClockGate:
    """Whether the clock is live, and what arrived before it was.

    One instance per node. `sample` is given every reading the node takes, so
    the gate learns the clock is live from the same number the node is about
    to use, rather than from a second source that could disagree with it.
    """

    def __init__(self, capacity: int = HOLD_CAPACITY) -> None:
        if not isinstance(capacity, int) or capacity < 1:
            raise ValueError(
                f"capacity is {capacity!r}; a gate that can hold nothing "
                f"drops the first packet of every run")
        self.capacity = capacity
        self.live = False
        self.live_at: float = float("nan")
        self.held: List[Any] = []
        self.held_total = 0
        self.dropped_full = 0
        self.readings_before_live = 0

    def sample(self, now: float) -> bool:
        """Take one clock reading. True once the clock is live.

        Once live, always live. A clock that went back to zero would be a
        simulator restarting under a node that is still holding the previous
        run's state, and treating that as a fresh start would let two runs
        share a timeline.
        """
        if self.live:
            return True
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            self.readings_before_live += 1
            return False
        value = float(now)
        if not math.isfinite(value) or value <= LIVE_ABOVE_S:
            self.readings_before_live += 1
            return False
        self.live = True
        self.live_at = value
        return True

    def hold(self, item: Any) -> str:
        """Keep one thing that arrived too early. Never silently."""
        if len(self.held) >= self.capacity:
            self.dropped_full += 1
            return DROPPED_FULL
        self.held.append(item)
        self.held_total += 1
        return HELD

    def release(self) -> List[Any]:
        """Everything held, in arrival order, once and once only."""
        if not self.held:
            return []
        out = self.held
        self.held = []
        return out

    def as_record(self) -> dict:
        return {
            "clock_live": self.live,
            "clock_live_at_s": (None if math.isnan(self.live_at)
                                else round(self.live_at, 3)),
            "readings_before_clock": self.readings_before_live,
            "held_before_clock": self.held_total,
            "dropped_waiting_for_clock": self.dropped_full,
        }
