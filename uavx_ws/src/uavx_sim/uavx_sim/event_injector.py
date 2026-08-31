"""Fire the scenario's events on simulated time, and record when they landed.

Chunk 1.4. `scripts/gate.sh` asserts `injected_event_observed` and
`injected_event_count`, not the length of the scenario's event list, and that
difference is the reason this module exists. An injector that logs "killed
uav_2" and never touches uav_2 satisfies any check that only asks whether the
event was listed. Every fault scenario in week 4 rests on this one mechanism:
if the kill never happened, `time_to_reconnect_s` is timing the recovery of a
swarm that was never broken, and that number is worse than a missing one
because it looks like evidence.

So each event carries two times into the run record, and they come from
different places:

    requested_t   what the scenario asked for, the event's at_s
    observed_t    when the effect was actually seen on the target, or null

`observed_t` is stamped by polling the target through `effect_visible`, never
by the code that asked for the effect. An event whose effect never becomes
visible keeps `observed_t` at None, stays out of `count_observed()`, and makes
`all_observed()` false. An unobserved event is reported as unobserved rather
than counted as a success.

Nothing here imports rclpy and nothing here reads a clock. Simulated time
arrives as an argument to `tick` and `poll_observations`, and the world is
reached through two callables the runner supplies, so the decision logic is
testable with no ROS graph and no simulator behind it. Wall time would also
break seeded replay, because a run's event times would then depend on when it
was launched.

The runner in chunk 1.7 owns the two callables and the record it writes:

    injector = EventInjector(scenario["injected_events"], apply, visible)
    ...
    injector.tick(t)                  # every control iteration
    injector.poll_observations(t)     # every control iteration, after tick
    ...
    record["injected_events"] = injector.records()
    record["injected_event_count"] = injector.count_observed()
    record["injected_event_observed"] = injector.all_observed()
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

# architecture.md section 1b freezes these three. A fourth spelling is either a
# typo in a scenario or a Stage 2 disturbance nobody has implemented, and both
# have to stop the run before a simulator starts rather than at t=30 with four
# vehicles in the air.
EVENT_TYPES: Tuple[str, ...] = ("kill", "comms_blackout", "gps_degrade")

# The keys scenarios/run-record.schema.json requires of every injected event.
RECORD_KEYS: Tuple[str, ...] = ("type", "target", "requested_t", "observed_t")


def _finite_time(value: Any, what: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    return number


@dataclass(frozen=True)
class PendingEvent:
    """One event the scenario asked for, before anything has been done about it.

    Frozen because the scenario is the authority on what was requested. If the
    injector could edit `at_s` then `requested_t` would stop being a record of
    what was asked for and become a second record of what happened, which is
    the conflation this module exists to prevent.
    """

    type: str
    target: str
    at_s: float

    def __post_init__(self) -> None:
        # Construction time, not fire time. A scenario carrying a bad event
        # type must fail while it costs nothing, not thirty seconds into a run
        # that has already put four vehicles in the air.
        if self.type not in EVENT_TYPES:
            raise ValueError(
                f"unknown event type {self.type!r}. architecture.md section 1b "
                f"allows {', '.join(EVENT_TYPES)}"
            )
        if not isinstance(self.target, str) or not self.target.strip():
            raise ValueError(
                f"event {self.type!r} needs a target vehicle id, got {self.target!r}"
            )
        at_s = _finite_time(self.at_s, f"at_s of the {self.type!r} event")
        if at_s < 0:
            raise ValueError(
                f"at_s of the {self.type!r} event is {at_s}, and scenario time "
                "starts at zero"
            )
        object.__setattr__(self, "at_s", at_s)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PendingEvent":
        """Build one event from a loaded scenario's dict."""
        missing = [key for key in ("type", "target", "at_s") if key not in mapping]
        if missing:
            raise ValueError(
                f"injected event is missing {', '.join(missing)}. Every event "
                "has type, target and at_s"
            )
        return cls(
            type=mapping["type"],
            target=mapping["target"],
            at_s=mapping["at_s"],
        )


@dataclass
class _Tracked:
    """A pending event plus the two things that happen to it during a run."""

    event: PendingEvent
    fired_at_s: Optional[float] = None
    observed_t: Optional[float] = None


class EventInjector:
    """Fires scenario events on simulated time and observes that they landed.

    Built from the scenario's event list plus two callables the runner owns:

        apply_effect(event_type, target) -> None
            do the thing. Kill the vehicle, cut its radio, degrade its fix.

        effect_visible(event_type, target) -> bool
            is the effect visible on the target right now. This is the honest
            half. It has to look at the target, not at whether apply_effect was
            called, or the record it feeds is a record of intentions.

    They are two separate arguments so the second cannot quietly be written as
    "we asked for it, therefore it happened".
    """

    def __init__(
        self,
        events: Iterable[Any],
        apply_effect: Callable[[str, str], None],
        effect_visible: Callable[[str, str], bool],
    ) -> None:
        if not callable(apply_effect):
            raise TypeError("apply_effect must be callable")
        if not callable(effect_visible):
            raise TypeError("effect_visible must be callable")
        self._apply_effect = apply_effect
        self._effect_visible = effect_visible
        self._tracked: List[_Tracked] = [
            _Tracked(event=self._as_event(item)) for item in events
        ]

    @staticmethod
    def _as_event(item: Any) -> PendingEvent:
        if isinstance(item, PendingEvent):
            return item
        if isinstance(item, Mapping):
            return PendingEvent.from_mapping(item)
        raise TypeError(
            "an injected event is a PendingEvent or a mapping with type, target "
            f"and at_s, got {type(item).__name__}"
        )

    # -------------------------------------------------------------- firing
    def tick(self, sim_time_s: float) -> Tuple[PendingEvent, ...]:
        """Fire every event whose at_s has passed and which has not fired.

        `sim_time_s` is ROS simulated time with zero at scenario start. Returns
        the events fired on this call, in scenario order, for the runner to log.
        """
        now = _finite_time(sim_time_s, "sim_time_s")
        fired = []
        for tracked in self._tracked:
            if tracked.fired_at_s is not None:
                continue
            if now < tracked.event.at_s:
                continue
            # Marked before the call, so a callable that raises cannot be
            # retried on the next tick. Injecting the same fault twice would
            # leave the scenario in a state no record describes.
            tracked.fired_at_s = now
            self._apply_effect(tracked.event.type, tracked.event.target)
            fired.append(tracked.event)
        return tuple(fired)

    # --------------------------------------------------------- observation
    def poll_observations(self, sim_time_s: float) -> Tuple[PendingEvent, ...]:
        """Stamp observed_t on every fired event whose effect is now visible.

        Call it every iteration, after `tick`. An effect that takes three
        iterations to show up gets the time it showed up, not the time it was
        requested. Returns the events observed for the first time on this call.
        """
        now = _finite_time(sim_time_s, "sim_time_s")
        observed = []
        for tracked in self._tracked:
            if tracked.fired_at_s is None or tracked.observed_t is not None:
                continue
            if not self._effect_visible(tracked.event.type, tracked.event.target):
                continue
            earliest = max(tracked.event.at_s, tracked.fired_at_s)
            if now < earliest:
                # Never write an observation that precedes its own request. The
                # only route here is a clock that ran backwards, and a silently
                # clamped timestamp would make that unfindable afterwards.
                raise ValueError(
                    f"cannot observe {tracked.event.type} on {tracked.event.target} "
                    f"at t={now}, earlier than the t={earliest} it was requested "
                    "and fired at. Simulated time went backwards."
                )
            tracked.observed_t = now
            observed.append(tracked.event)
        return tuple(observed)

    # ---------------------------------------------------------- reporting
    def records(self) -> List[dict]:
        """The `injected_events` block of the run record, in scenario order.

        Every event the scenario asked for is here, including the ones that
        never fired and the ones whose effect was never seen. Those carry
        observed_t None, which is what the schema means by null.
        """
        return [
            {
                "type": tracked.event.type,
                "target": tracked.event.target,
                "requested_t": tracked.event.at_s,
                "observed_t": tracked.observed_t,
            }
            for tracked in self._tracked
        ]

    def unobserved(self) -> List[dict]:
        """The events whose effect was never seen, in the same record shape.

        For the runner's failure message. An event that did not land should be
        named in the log, not merely absent from a count.
        """
        return [row for row in self.records() if row["observed_t"] is None]

    def count_observed(self) -> int:
        """How many events were seen to land. This is injected_event_count."""
        return sum(1 for tracked in self._tracked if tracked.observed_t is not None)

    def count_fired(self) -> int:
        """How many events were fired at all.

        Higher than count_observed() means effects were requested and never
        seen, which separates a broken injector from a broken observation.
        """
        return sum(1 for tracked in self._tracked if tracked.fired_at_s is not None)

    def all_observed(self) -> bool:
        """Every event landed, and there was at least one. injected_event_observed.

        An injector holding no events reports false rather than a vacuous true.
        A run that injected nothing has not observed an injected event, and an
        empty set satisfying the claim is the same hole this module closes.
        """
        return bool(self._tracked) and all(
            tracked.observed_t is not None for tracked in self._tracked
        )

    def __len__(self) -> int:
        return len(self._tracked)
