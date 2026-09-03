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
from typing import Iterable, Sequence

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
