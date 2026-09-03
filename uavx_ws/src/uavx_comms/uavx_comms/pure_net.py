"""The deterministic network the unit tests drive. No ROS, no simulator, no clock.

This object stands in for two things at once, and both of them sit outside the
swarm: the link layer, which is the only process allowed to hold every
vehicle's position, and the ROS graph, which is the only thing that carries a
packet from one node's tx endpoint to another node's rx endpoint. That is why
it holds the positions and the routers do not.

It is not a timing oracle. Time advances in fixed steps and a hop costs one
step, so every duration it reports is quantised to the step. It is a logic
oracle: what gets elected, what route installs, which identities arrive and how
many times. The real timing is measured by a scenario run under the harness
uavx_sim owns, against the frozen budgets, and nothing here is ever quoted as a
run metric.

W3 replaces this object and nothing else. Every Router keeps the same two
methods, wired to the two topics in the allowlist.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import election, link, packet as pk, params
from .router import Router

# One step per pose sample, which is the rate the frozen design evaluates links
# at. A hop costs one step because the latency is shorter than a step, so a
# packet handed to the radio in one step is readable in the next.
DEFAULT_DT = 1.0 / params.POSE_SAMPLE_HZ


class Vehicle:
    """A router, where it is, and where it has been told to go."""

    def __init__(self, router: Router, position: Sequence[float]) -> None:
        self.router = router
        self.position = tuple(position)
        self.target: Optional[Tuple[float, float, float]] = None
        self.speed = params.CRUISE_SPEED_MPS
        self.alive = True
        self.observing = False
        self._next_observation_at = 0.0

    def step(self, dt: float) -> None:
        if self.target is None:
            return
        remaining = math.dist(self.position, self.target)
        if remaining <= 1e-9:
            self.target = None
            return
        travel = min(remaining, self.speed * dt)
        fraction = travel / remaining
        self.position = tuple(self.position[i]
                              + fraction * (self.target[i] - self.position[i])
                              for i in range(3))
        if math.dist(self.position, self.target) <= 1e-9:
            self.position = self.target
            self.target = None


class PureNet:
    """Routers over a link model, stepped by hand."""

    def __init__(self, positions: Dict[str, Sequence[float]],
                 roles: Optional[Dict[str, int]] = None,
                 seed: int = 1,
                 destination: str = params.GCS_ID,
                 forwarding: bool = True,
                 elections_enabled: bool = True,
                 dt: float = DEFAULT_DT,
                 queue_capacity: int = params.QUEUE_CAPACITY) -> None:
        roles = roles or {}
        self.dt = dt
        self.now = 0.0
        self.destination = destination
        self.link = link.LinkModel(seed)
        self.vehicles: Dict[str, Vehicle] = {}
        for node_id in sorted(positions):
            router = Router(node_id, positions[node_id],
                            role=roles.get(node_id, election.ROLE_SURVEY),
                            destination=destination,
                            forwarding=forwarding,
                            elections_enabled=elections_enabled,
                            queue_capacity=queue_capacity)
            self.vehicles[node_id] = Vehicle(router, positions[node_id])
        self._inflight: List[Tuple[float, str, pk.Packet]] = []
        self.transmissions = 0
        self.receptions = 0

    # -- the world ----------------------------------------------------------

    def router(self, node_id: str) -> Router:
        return self.vehicles[node_id].router

    def position(self, node_id: str) -> Tuple[float, float, float]:
        return self.vehicles[node_id].position

    def live_ids(self) -> List[str]:
        return [n for n, v in sorted(self.vehicles.items()) if v.alive]

    def kill(self, node_id: str) -> None:
        """The vehicle is gone. It stops flying, stops talking and never returns."""
        self.vehicles[node_id].alive = False
        self.link.kill(node_id)

    def blackout(self, node_id: str) -> None:
        """The radio is gated both ways. The vehicle is still flying."""
        self.link.gate_radio(node_id)

    def restore(self, node_id: str) -> None:
        self.link.restore_radio(node_id)

    def send_to(self, node_id: str, target: Sequence[float],
                speed: float = params.CRUISE_SPEED_MPS) -> None:
        vehicle = self.vehicles[node_id]
        vehicle.target = tuple(target)
        vehicle.speed = speed

    def start_observing(self, node_id: str, at: float = 0.0) -> None:
        vehicle = self.vehicles[node_id]
        vehicle.observing = True
        vehicle._next_observation_at = at

    def stop_observing(self, node_id: str) -> None:
        self.vehicles[node_id].observing = False

    # -- the step -----------------------------------------------------------

    def step(self) -> None:
        self.now += self.dt
        self._deliver_arrived()
        self._fly()
        self._observe()
        self._tick()
        self._transmit()

    def run_for(self, seconds: float) -> None:
        end = self.now + seconds - 1e-9
        while self.now < end:
            self.step()

    def run_until(self, when: float) -> None:
        while self.now < when - 1e-9:
            self.step()

    def _deliver_arrived(self) -> None:
        due = [item for item in self._inflight if item[0] <= self.now + 1e-9]
        self._inflight = [item for item in self._inflight
                          if item[0] > self.now + 1e-9]
        for _, receiver, incoming in due:
            vehicle = self.vehicles.get(receiver)
            if vehicle is None or not vehicle.alive:
                continue
            self.receptions += 1
            vehicle.router.on_rx(incoming, self.now)

    def _fly(self) -> None:
        for node_id in sorted(self.vehicles):
            vehicle = self.vehicles[node_id]
            if not vehicle.alive:
                continue
            # A vehicle that has been given a relay slot flies to it. The role
            # machine names the slot; the autopilot is what actually goes.
            slot = vehicle.router.roles.slot_target
            if slot is not None and vehicle.target is None \
                    and math.dist(vehicle.position, slot) > 1e-9:
                vehicle.target = tuple(slot)
                vehicle.speed = params.CRUISE_SPEED_MPS
            vehicle.step(self.dt)
            vehicle.router.set_position(vehicle.position)

    def _observe(self) -> None:
        period = 1.0 / params.APP_PACKET_RATE_HZ
        for node_id in sorted(self.vehicles):
            vehicle = self.vehicles[node_id]
            if not vehicle.alive or not vehicle.observing:
                continue
            while vehicle._next_observation_at <= self.now + 1e-9:
                vehicle.router.observe(vehicle._next_observation_at)
                vehicle._next_observation_at += period

    def _tick(self) -> None:
        for node_id in sorted(self.vehicles):
            vehicle = self.vehicles[node_id]
            if vehicle.alive:
                vehicle.router.tick(self.now, self.dt)

    def _transmit(self) -> None:
        """Every transmission goes into the medium and every node in range hears it.

        There is no addressing at this layer. The link model is asked once per
        ordered pair per message, which is what makes a one-way radio failure
        expressible and what keeps the model honest about a broadcast being a
        broadcast.
        """
        for sender_id in sorted(self.vehicles):
            sender = self.vehicles[sender_id]
            if not sender.alive:
                continue
            for outgoing in sender.router.drain_tx(self.now):
                self.transmissions += 1
                for receiver_id in sorted(self.vehicles):
                    if receiver_id == sender_id:
                        continue
                    receiver = self.vehicles[receiver_id]
                    if not receiver.alive:
                        continue
                    distance = math.dist(sender.position, receiver.position)
                    if not self.link.deliver(sender_id, receiver_id, distance):
                        continue
                    self._inflight.append((self.now + params.HOP_LATENCY_S,
                                           receiver_id, outgoing.copy()))

    # -- what a test asks it ------------------------------------------------

    def installed_route(self, node_id: str) -> Optional[List[str]]:
        return self.router(node_id).route.installed

    def role_of(self, node_id: str) -> int:
        return self.router(node_id).roles.role

    def relay_holders(self) -> List[str]:
        return sorted(n for n, v in self.vehicles.items()
                      if v.alive and v.router.roles.role == election.ROLE_RELAY)

    def delivered_ids(self) -> List[str]:
        sink = self.router(self.destination)
        return sorted("{0}:{1}".format(o, s) for (o, s) in sink.accepted)

    def generated_ids(self) -> List[str]:
        out: List[str] = []
        for node_id in sorted(self.vehicles):
            out.extend(self.vehicles[node_id].router.generated_ids)
        return sorted(out)

    def observations(self) -> dict:
        """The observation block, in the shape architecture.md section 3 names.

        The set comparison is the point. `generated: 450, unique_delivered: 450`
        is satisfied by delivering the wrong 450 packets, and counting
        deliveries alone lets a relay pass by sending the same packet twice.
        """
        sink = self.router(self.destination)
        generated = self.generated_ids()
        delivered = self.delivered_ids()
        peak = max((v.router.store.peak for v in self.vehicles.values()),
                   default=0)
        return {
            "generated_ids": generated,
            "delivered_ids": delivered,
            "generated": len(generated),
            "unique_delivered": len(delivered),
            "duplicated": sink.duplicated,
            "expired": sum(v.router.store.expired
                           for v in self.vehicles.values()),
            "evicted": sum(v.router.store.evicted
                           for v in self.vehicles.values()),
            "unexpected_count": len(set(delivered) - set(generated)),
            "peak_queue_depth": peak,
            "control_queue_max_delay_s": max(
                (v.router.control_max_delay_s
                 for v in self.vehicles.values()), default=0.0),
            "set_equal": set(generated) == set(delivered),
            "ledger": list(sink.ledger),
        }
