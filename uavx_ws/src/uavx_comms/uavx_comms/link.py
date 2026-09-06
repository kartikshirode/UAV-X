"""Whether two points can talk, and with what odds.

stage-1/architecture.md section 2. For an ordered pair at 3D distance d:

    full   d <= r_full            delivered, probability 1
    fade   r_full < d <= r_max    delivered with p = (r_max - d) / (r_max - r_full)
    out    d > r_max              dropped, probability 0

Three things about this model are decisions rather than transcription, and W3
inherits all three.

Ordered pair. The band is symmetric in distance, but the model is asked per
direction and per message, because a radio failure is not symmetric: a
comms_blackout gates one vehicle's radio and every pair it belongs to, in both
directions, while the geometry is unchanged and every other pair keeps working.

The full band draws no random number. A link at or inside r_full is delivered
by definition, so consuming the stream there would make a fade-band experiment
depend on how much full-band traffic happened to run first, and a seeded replay
that depends on traffic volume is not a seeded replay.

Distance comes from ground truth. Only the link layer and the evaluator may
read it, which is why this module takes distances and identifiers and never
looks anything up for itself.
"""

import math
import random
from typing import Iterable, Optional, Sequence, Set, Tuple

from . import params

FULL = "full"
FADE = "fade"
OUT = "out"


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    """3D distance in metres. The frame is local ENU with the GCS at the origin."""
    return math.dist(tuple(a), tuple(b))


def band(distance_m: float) -> str:
    """Which band a distance falls in. The boundaries are closed on the left.

    d == r_full is full, not fade, and d == r_max is fade with probability
    zero, not out. Getting either boundary the other way round moves a frozen
    pair across a band, and the placement rule exists precisely so that no pair
    is near enough to a boundary for it to matter. It still has one right
    answer.
    """
    if distance_m <= params.R_FULL_M:
        return FULL
    if distance_m <= params.R_MAX_M:
        return FADE
    return OUT


def delivery_probability(distance_m: float) -> float:
    """The frozen table, as a number."""
    b = band(distance_m)
    if b == FULL:
        return 1.0
    if b == OUT:
        return 0.0
    span = params.R_MAX_M - params.R_FULL_M
    return (params.R_MAX_M - distance_m) / span


def is_usable(distance_m: float) -> bool:
    """Inside the limit a link the routing depends on must hold."""
    return distance_m <= params.USED_LINK_MAX_M


def is_absent(distance_m: float) -> bool:
    """Beyond the limit a link that must not exist must sit."""
    return distance_m >= params.UNUSED_LINK_MIN_M


def placement_holds(distance_m: float) -> bool:
    """No pair may sit between the two placement limits.

    A pair in the gap makes a code failure and an unlucky draw produce the same
    result. scripts/check_geometry.py enforces this over the frozen positions;
    this is the same predicate available at runtime.
    """
    return is_usable(distance_m) or is_absent(distance_m)


class Blackout:
    """One radio gate with an end time, kept apart from the radio itself.

    The scenario names the moment the gate goes on and the moment it comes
    off. The first is injected by the runner, because a fault has to land at a
    time the record can attribute to a request. The second is not: the radio
    restores itself after a frozen hold, so nothing in the recovery depends on
    the harness noticing that the vehicle is back. A swarm that only recovers
    when a test tells it to has not recovered.

    The hold is a duration and not a moment, which is what makes it usable.
    The nodes count in simulated seconds since the simulator came up and the
    scenario counts from its own zero, and a duration means the same thing in
    both.

    The start is armed rather than duration based, because the runner does
    know where the scenario's zero sits once the run is going and can hand
    over an instant in the clock both of them read. `arm` takes that instant
    and `start_due` gates the radio when the clock reaches it, which is the
    same shape as `restore_due`. `start` is still there for the scenario that
    begins in a blackout and for anything that wants the gate now.
    """

    def __init__(self, hold_s: float = 0.0) -> None:
        if isinstance(hold_s, bool) or not isinstance(hold_s, (int, float)):
            raise ValueError(f"hold_s is {hold_s!r}, not a number of seconds")
        if not math.isfinite(float(hold_s)) or hold_s < 0:
            raise ValueError(
                f"hold_s is {hold_s!r}; a blackout lasts a length of time or "
                f"lasts until something lifts it, and a negative one is "
                f"neither")
        self.hold_s = float(hold_s)
        self.nodes: Set[str] = set()
        self.started_at: Optional[float] = None
        self.restored_at: Optional[float] = None
        # The gate that has been scheduled and has not fired. Kept apart from
        # `nodes`, which is what is gated right now: a radio that reported
        # itself off the moment it was armed would put the fault at the time
        # the runner spoke rather than the time the scenario asked for.
        self.armed: Set[str] = set()
        self.armed_at_s: Optional[float] = None

    @property
    def live(self) -> bool:
        return bool(self.nodes)

    def start(self, nodes: Iterable[str], now: float) -> Tuple[str, ...]:
        """Gate these radios. Returns the ones this call actually gated.

        The first start is the one the record reports. A second naming the
        same vehicle changes nothing, because the run is describing one
        outage and a restarted clock would shorten it.
        """
        wanted = {str(n) for n in nodes if str(n).strip()}
        fresh = tuple(sorted(wanted - self.nodes))
        if not fresh:
            return ()
        self.nodes |= wanted
        if self.started_at is None:
            self.started_at = float(now)
            self.restored_at = None
        return fresh

    def arm(self, nodes: Iterable[str], at_s: float) -> Tuple[str, ...]:
        """Schedule these radios to gate themselves at `at_s`.

        `at_s` is read in whatever clock `start_due` is later called with, so
        the caller and this object have to be counting from the same zero. The
        runner arms with an absolute simulated time because that is the clock
        every node in the run already shares; the scenario's own zero is an
        offset only the runner knows.

        Arming a vehicle that is already gated does nothing. The run is
        describing one outage and a second schedule over the top of it would
        give the record two answers about when the fault landed.
        """
        if isinstance(at_s, bool) or not isinstance(at_s, (int, float)):
            raise ValueError(f"at_s is {at_s!r}, not a simulated time")
        if not math.isfinite(float(at_s)):
            raise ValueError(
                f"at_s is {at_s!r}. A gate scheduled for no particular moment "
                f"is a gate that never lands, and the run would report an "
                f"outage nothing caused")
        wanted = {str(n) for n in nodes if str(n).strip()} - self.nodes
        if not wanted:
            return ()
        self.armed = wanted
        self.armed_at_s = float(at_s)
        return tuple(sorted(wanted))

    def start_due(self, now: float) -> Tuple[str, ...]:
        """Gate the armed radios if the clock has reached the armed instant.

        Answered once, like `restore_due`. The hold starts counting from here
        and not from the arming, so a gate armed early still lasts exactly as
        long as the scenario said.
        """
        if not self.armed or self.armed_at_s is None:
            return ()
        if float(now) < self.armed_at_s:
            return ()
        nodes, self.armed, self.armed_at_s = self.armed, set(), None
        return self.start(nodes, now)

    def restore_due(self, now: float) -> Tuple[str, ...]:
        """The radios whose hold has run out. Answered once.

        A hold of zero is a gate nothing lifts, which is what a scenario that
        starts in a blackout wants.
        """
        if not self.nodes or self.hold_s <= 0 or self.started_at is None:
            return ()
        if float(now) - self.started_at < self.hold_s:
            return ()
        out = tuple(sorted(self.nodes))
        self.nodes = set()
        self.restored_at = float(now)
        return out

    def as_record(self) -> dict:
        return {
            "blackout_hold_s": self.hold_s,
            "blackout_started_at": self.started_at,
            "blackout_restored_at": self.restored_at,
            "blackout_nodes": sorted(self.nodes),
            # Non empty at the end of a run means a fault was scheduled and
            # the run finished before it landed, which is a scenario whose
            # duration and whose event list disagree.
            "blackout_armed": sorted(self.armed),
            "blackout_armed_at": self.armed_at_s,
        }


class LinkModel:
    """The radio. Seeded per run from the scenario file, so a run replays.

    `radio_off` holds the vehicles whose radio is gated, which is what a
    comms_blackout injects. A gated vehicle is still flying and still occupies
    airspace; it just cannot be heard and cannot hear. `absent` holds the
    vehicles that are gone, which is what a kill injects. The two are different
    faults and the organisers name both.
    """

    def __init__(self, seed: int, radio_off: Iterable[str] = (),
                 absent: Iterable[str] = ()) -> None:
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self.radio_off = set(radio_off)
        self.absent = set(absent)
        self.draws = 0
        self.delivered = 0
        self.dropped = 0

    # -- fault injection, the two the challenge names -----------------------

    def gate_radio(self, node_id: str) -> None:
        self.radio_off.add(node_id)

    def restore_radio(self, node_id: str) -> None:
        self.radio_off.discard(node_id)

    def kill(self, node_id: str) -> None:
        self.absent.add(node_id)

    def is_live(self, node_id: str) -> bool:
        return node_id not in self.absent and node_id not in self.radio_off

    # -- the model ----------------------------------------------------------

    def deliver(self, tx_id: str, rx_id: str, distance_m: float) -> bool:
        """Does this one message get from tx to rx, right now.

        Called once per ordered pair per message. A drop here is the radio, not
        a queue: nothing above this layer may treat it as a permanent failure,
        because the same pair may deliver the next message.
        """
        if not self.is_live(tx_id) or not self.is_live(rx_id):
            self.dropped += 1
            return False
        p = delivery_probability(distance_m)
        if p >= 1.0:
            self.delivered += 1
            return True
        if p <= 0.0:
            self.dropped += 1
            return False
        self.draws += 1
        got = self._rng.random() < p
        if got:
            self.delivered += 1
        else:
            self.dropped += 1
        return got
