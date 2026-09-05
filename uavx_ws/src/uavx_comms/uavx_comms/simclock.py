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

    ClockGate   the question, the holding pen and the count of both
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
