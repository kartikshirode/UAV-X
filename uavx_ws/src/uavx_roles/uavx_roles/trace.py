"""Chunk 4.1: what one vehicle's role did, in the words the record uses.

The gate asks four questions of a recovery run and every one of them is about
a vehicle rather than about the swarm:

    relay_role_moved            did the role actually go somewhere
    relay_role_holder           to whom
    relay_role_released         did it come back
    mover_returned_to_station   and did the aircraft come back with it

A swarm-level answer to any of those can be true while the aircraft sat still.
`relay_role_moved` computed from an election result says a message was sent.
Computed here it says a vehicle was granted the role, flew to the slot and got
there, and the run record can be read against the flight log.

The trace records times and nothing else derived. Whether 30 s was fast enough
is the gate's question, and a module that also decided it would be marking its
own work.
"""

from __future__ import annotations

from typing import Optional, Tuple

Point = Tuple[float, float, float]


class RoleTrace:
    """One vehicle's role history across a run, in simulated seconds."""

    def __init__(self, node_id: str, home_role_name: str = "survey") -> None:
        self.node_id = node_id
        self.home_role_name = home_role_name

        self.epoch: Optional[int] = None
        self.granted_at: Optional[float] = None
        self.slot: Optional[Point] = None
        self.arrived_at: Optional[float] = None
        self.released_at: Optional[float] = None
        self.returned_at: Optional[float] = None
        self.lapsed_at: Optional[float] = None
        self.grants = 0
        self.renewals = 0

    # ------------------------------------------------------------- the role
    def granted(self, epoch: int, slot: Optional[Point], now: float) -> None:
        """This vehicle was made the relay.

        A second grant in a later epoch starts the story again: the arrival,
        the release and the return all belong to the epoch that granted them,
        and carrying an older epoch's arrival forward would report a vehicle
        as having reached a slot it was never sent to.
        """
        if self.epoch is not None and epoch == self.epoch:
            return
        self.epoch = epoch
        self.granted_at = now
        self.slot = tuple(slot) if slot is not None else None
        self.arrived_at = None
        self.released_at = None
        self.returned_at = None
        self.lapsed_at = None
        self.grants += 1

    def renewed(self) -> None:
        self.renewals += 1

    def reached_slot(self, now: float) -> None:
        if self.epoch is not None and self.arrived_at is None:
            self.arrived_at = now

    def released(self, now: float) -> None:
        if self.epoch is not None and self.released_at is None:
            self.released_at = now

    def lapsed(self, now: float) -> None:
        """The lease ran out rather than the owner releasing it.

        Recorded apart from a release because they mean opposite things. A
        release is the swarm deciding it is done with the relay. A lapse is
        the swarm having stopped talking, and a run where the vehicle came
        home for that reason has not demonstrated the handback.
        """
        if self.epoch is not None and self.lapsed_at is None:
            self.lapsed_at = now

    def returned_home(self, now: float) -> None:
        if self.released_at is not None and self.returned_at is None:
            self.returned_at = now

    # ----------------------------------------------------------- the record
    @property
    def moved(self) -> bool:
        return self.epoch is not None and self.arrived_at is not None

    @property
    def gave_it_back(self) -> bool:
        return self.released_at is not None

    @property
    def came_home(self) -> bool:
        return self.returned_at is not None

    def as_record(self) -> dict:
        def stamp(value):
            return None if value is None else round(value, 3)

        return {
            "node": self.node_id,
            "home_role": self.home_role_name,
            "epoch": self.epoch,
            "grants": self.grants,
            "renewals": self.renewals,
            "slot_commanded": list(self.slot) if self.slot else None,
            "granted_at": stamp(self.granted_at),
            "arrived_at": stamp(self.arrived_at),
            "released_at": stamp(self.released_at),
            "lapsed_at": stamp(self.lapsed_at),
            "returned_at": stamp(self.returned_at),
            "moved": self.moved,
            "released": self.gave_it_back,
            "returned_to_station": self.came_home,
        }


def swarm_trace(rows) -> dict:
    """The four role fields of the run record, from every vehicle's own row.

    Reads the vehicles' files rather than asking the process that ran the
    election, for the same reason the delivery ratio is read off the ledgers:
    the node that would benefit from the answer being yes is not the one that
    gets to give it.
    """
    holders = [row for row in rows if row.get("moved")]
    if len(holders) > 1:
        raise ValueError(
            "two vehicles report having taken the relay role: "
            + ", ".join(sorted(str(row.get("node")) for row in holders))
            + ". One relay slot was computed and one vehicle was assigned to "
              "it, so a second holder means an epoch was decided twice")

    holder = holders[0] if holders else None
    out = {
        "relay_role_moved": holder is not None,
        "relay_role_holder": holder.get("node") if holder else None,
        "relay_role_released": bool(holder and holder.get("released")),
        "mover_returned_to_station": bool(holder
                                          and holder.get("returned_to_station")),
    }
    if holder is not None:
        out["relay_role_epoch"] = holder.get("epoch")
        out["relay_role_granted_at_s"] = holder.get("granted_at")
        out["relay_role_arrived_at_s"] = holder.get("arrived_at")
        if holder.get("released_at") is not None:
            out["relay_role_released_at_s"] = holder.get("released_at")
        if holder.get("returned_at") is not None:
            out["mover_returned_at_s"] = holder.get("returned_at")
        if holder.get("lapsed_at") is not None:
            out["relay_lease_lapsed_at_s"] = holder.get("lapsed_at")
    return out
