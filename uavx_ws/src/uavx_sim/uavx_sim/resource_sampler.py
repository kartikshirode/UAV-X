"""Peak memory and swap across the whole simulator process group.

Four PX4 instances, gzserver and our own nodes share roughly 11 GB of WSL
memory, and nobody had measured them under load. The measurement is only worth
something if it covers every process in the group. A sampler that reads the
runner and stops there reports a comfortable few hundred MiB while the box is
at its limit, and every week after this one inherits a number that means
nothing. So the group is the unit here, never the process that happens to be
holding the sampler.

Sampling is driven by the caller, one call per tick. A background thread would
sample on wall time, which makes peak_at_s a wall clock artifact rather than
the simulated time the record asks for, and the thread is the first thing the
scheduler starves when memory gets tight. It would thin out its readings at
exactly the moment the peak arrives.

Chunk 1.7 uses it like this:

    sampler = ResourceSampler(root_pid=runner_pid, extra_pids=[agent_pid])
    sampler.add_pids(launcher_reported_pids)     # any time, more than once
    ...
    sampler.sample(sim_time_s)                   # once per /clock tick
    record["resources"] = sampler.summary()

summary() raises instead of handing back a peak of zero. scripts/gate.sh asks
for resources.peak_rss_mib > 0, and round 7 finding 5 was that both gates
checked only that a peak had been recorded. The ceiling closed the top of that
hole; a sampler that never ran must not be able to answer the bottom of it.
"""

import math
import os
import warnings
from collections import namedtuple
from pathlib import Path

try:
    import psutil
except ImportError:  # the target WSL has 5.9.0; a bare checkout might not
    psutil = None


_MIB = 1024.0 * 1024.0


class ResourceSamplerError(RuntimeError):
    """The sampler cannot answer honestly, so it refuses to answer at all."""


class ProcessGone(Exception):
    """The process was there when we listed it and is not there now."""


# swap_bytes is None when the kernel would not tell us, which is a different
# thing from zero and is treated as one.
ProcessMemory = namedtuple("ProcessMemory", ("rss_bytes", "swap_bytes"))
ProcessMemory.__new__.__defaults__ = (None,)


def _to_mib(byte_count):
    """Bytes to MiB, where a nonzero byte count never rounds down to zero.

    On the RSS side rounding is cosmetic. On swap it decides what the gate
    reads: swap_used_mib == 0 is the claim that the box stayed inside its
    budget, so a rounding rule that can turn one swapped page into a clean zero
    is the defect this whole record exists to catch.
    """
    rounded = round(byte_count / _MIB, 3)
    if byte_count > 0 and rounded <= 0.0:
        return 0.001
    return rounded


class PsutilProbe:
    """Process enumeration and memory through psutil."""

    source = "psutil"

    def children(self, pid):
        try:
            return [child.pid for child in psutil.Process(pid).children()]
        except psutil.NoSuchProcess:
            raise ProcessGone(pid)
        except psutil.AccessDenied:
            # We can still account for the process itself, and a group we
            # cannot descend into is better reported short than not at all.
            return []

    def memory(self, pid):
        try:
            proc = psutil.Process(pid)
            rss = proc.memory_info().rss
        except psutil.NoSuchProcess:
            # ZombieProcess subclasses this. A zombie's pages are already
            # reclaimed, so gone is the right answer for it too.
            raise ProcessGone(pid)

        try:
            # Per process swap, not the system wide figure. See the note in
            # ResourceSampler.sample for why the two are not interchangeable.
            # Windows psutil has no swap field on this struct, so getattr
            # leaves it unknown rather than inventing a zero.
            swap = getattr(proc.memory_full_info(), "swap", None)
        except psutil.NoSuchProcess:
            raise ProcessGone(pid)
        except psutil.AccessDenied:
            swap = None
        return ProcessMemory(rss, swap)


class ProcfsProbe:
    """The same two questions asked of /proc directly.

    The root is a parameter so the parsing can be tested against a synthetic
    tree on a machine that has no /proc of its own.
    """

    source = "procfs"

    def __init__(self, root="/proc", page_size=None):
        self._root = Path(root)
        if page_size is None:
            sysconf = getattr(os, "sysconf", None)
            page_size = sysconf("SC_PAGE_SIZE") if sysconf else 4096
        self._page_size = int(page_size)

    def children(self, pid):
        proc_dir = self._root / str(pid)
        if not proc_dir.is_dir():
            raise ProcessGone(pid)

        kids = []
        found_interface = False
        task_dir = proc_dir / "task"
        if task_dir.is_dir():
            for tid in sorted(task_dir.iterdir()):
                try:
                    text = (tid / "children").read_text()
                except OSError:
                    continue
                found_interface = True
                kids.extend(int(field) for field in text.split())
        if found_interface:
            return kids
        # CONFIG_PROC_CHILDREN is not universal, and without it the children
        # file is simply absent. That would look like a childless runner and
        # hand back the root only number this module exists to avoid.
        return self._children_by_scan(pid)

    def _children_by_scan(self, pid):
        kids = []
        try:
            entries = sorted(self._root.iterdir())
        except OSError:
            return kids
        for entry in entries:
            if not entry.name.isdigit():
                continue
            ppid = self._field(entry / "status", "PPid:")
            if ppid is not None and int(ppid) == pid:
                kids.append(int(entry.name))
        return kids

    def memory(self, pid):
        try:
            fields = (self._root / str(pid) / "statm").read_text().split()
        except OSError:
            raise ProcessGone(pid)
        if len(fields) < 2:
            raise ProcessGone(pid)
        rss = int(fields[1]) * self._page_size

        swap_kb = self._field(self._root / str(pid) / "status", "VmSwap:")
        swap = None if swap_kb is None else int(swap_kb) * 1024
        return ProcessMemory(rss, swap)

    @staticmethod
    def _field(path, key):
        """First numeric token on the line starting with key, or None."""
        try:
            text = path.read_text()
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith(key):
                rest = line[len(key):].split()
                if rest:
                    return rest[0]
        return None


def default_probe(procfs_root="/proc"):
    """psutil when it imports, /proc when it does not, loudly either way."""
    if psutil is not None:
        return PsutilProbe()
    if Path(procfs_root).is_dir():
        warnings.warn(
            "psutil did not import, so resource sampling fell back to reading "
            "{0} directly. The numbers are the same; this warning exists so "
            "the fallback shows up in the run log instead of passing "
            "unnoticed.".format(procfs_root),
            RuntimeWarning,
            stacklevel=2,
        )
        return ProcfsProbe(procfs_root)
    raise ResourceSamplerError(
        "no way to read process memory: psutil did not import and there is no "
        "{0}. Sampling nothing and reporting zero would tell the gate the run "
        "was cheap when it was never measured.".format(procfs_root)
    )


class ResourceSampler:
    """Peak resident memory over a root process and everything under it."""

    def __init__(self, root_pid=None, extra_pids=(), probe=None):
        self._root_pid = os.getpid() if root_pid is None else int(root_pid)
        self._probe = default_probe() if probe is None else probe
        self.probe_source = getattr(self._probe, "source", "unknown")

        self._extra_pids = []
        self._samples = 0
        self._peak_rss_bytes = 0
        self._peak_swap_bytes = 0
        self._peak_at_s = None
        self._swap_unreadable = set()
        self.add_pids(extra_pids)

    @property
    def samples(self):
        return self._samples

    @property
    def root_pid(self):
        return self._root_pid

    def add_pids(self, pids):
        """Include processes the launcher owns but the runner did not fork.

        gzserver and the PX4 instances are children of the runner in the normal
        case. A launcher that daemonizes or reparents them breaks that, and the
        group would quietly shrink to whatever is still attached. Anything the
        caller knows about goes in here, duplicates and all.
        """
        for pid in pids:
            pid = int(pid)
            if pid != self._root_pid and pid not in self._extra_pids:
                self._extra_pids.append(pid)

    def group_pids(self):
        """The root, every extra pid, and every descendant of both.

        Breadth first with a seen set, so a reparenting race that briefly makes
        the tree look circular cannot spin here, and a pid reachable twice is
        counted once.
        """
        ordered = []
        seen = set()
        pending = [self._root_pid] + list(self._extra_pids)
        while pending:
            pid = pending.pop(0)
            if pid in seen:
                continue
            seen.add(pid)
            ordered.append(pid)
            try:
                kids = self._probe.children(pid)
            except ProcessGone:
                continue
            for kid in kids:
                kid = int(kid)
                if kid not in seen:
                    pending.append(kid)
        return ordered

    def sample(self, sim_time_s):
        """Read the whole group once and fold it into the running peak.

        Swap is per process, summed over the group, taken from the kernel's own
        accounting for each process (VmSwap in the status file, which is the
        same number psutil reports as memory_full_info().swap). The alternative
        is the system wide figure from meminfo or psutil.swap_memory, and the
        two answer different questions. System wide swap counts pages some
        unrelated program left out weeks ago, so the gate's swap_used_mib == 0
        would fail a healthy run on a busy laptop and pass a sick one on a
        quiet server. Per process swap says whether the simulator itself got
        pushed out of memory, and that is what makes a graded number like
        time_to_reconnect_s meaningless.

        Summing RSS across the group counts a page shared between two PX4
        instances twice, so the total is an upper bound rather than the exact
        footprint. That is the safe direction against a ceiling: it can fail a
        run that would have fit, and it cannot pass one that would not.

        Returns this sample's group total in bytes, for a live log line.
        """
        moment = float(sim_time_s)
        if not math.isfinite(moment) or moment < 0.0:
            raise ValueError(
                "sim_time_s must be a finite time at or after zero, got "
                "{0!r}. peak_at_s goes into the run record as simulated "
                "time.".format(sim_time_s)
            )

        rss_total = 0
        swap_total = 0
        for pid in self.group_pids():
            try:
                mem = self._probe.memory(pid)
            except ProcessGone:
                # A child can exit between enumeration and the read, and with
                # PX4 restarts that is ordinary rather than rare. Its pages are
                # already gone so it owes this sample nothing. Failing here
                # would crash a healthy run, and throwing the sample away would
                # hide the peak the surviving processes are holding right now.
                continue
            rss_total += int(mem.rss_bytes)
            if mem.swap_bytes is None:
                self._swap_unreadable.add(pid)
            else:
                swap_total += int(mem.swap_bytes)

        self._samples += 1
        # Strictly greater, so the earliest sample that reached the peak owns
        # peak_at_s. A later sample tying the maximum did not cause it.
        if rss_total > self._peak_rss_bytes:
            self._peak_rss_bytes = rss_total
            self._peak_at_s = moment
        if swap_total > self._peak_swap_bytes:
            self._peak_swap_bytes = swap_total
        return rss_total

    def summary(self):
        """The resources block of the run record, as numbers."""
        if self._samples == 0:
            raise ResourceSamplerError(
                "summary() was called before any sample was taken. Reporting a "
                "peak here would let a sampler that never ran satisfy "
                "resources.peak_rss_mib > 0."
            )
        if self._peak_rss_bytes <= 0:
            raise ResourceSamplerError(
                "{0} sample(s) taken and the group never held a single "
                "resident byte, so the probe read nothing rather than the "
                "machine being empty. Check root_pid {1} and the extra "
                "pids.".format(self._samples, self._root_pid)
            )
        if self._swap_unreadable:
            raise ResourceSamplerError(
                "swap could not be read for pid(s) {0}, so this group's swap "
                "is unknown. Reporting 0 would tell the gate the run never "
                "swapped when the truth is that nobody looked.".format(
                    sorted(self._swap_unreadable)
                )
            )
        return {
            "peak_rss_mib": _to_mib(self._peak_rss_bytes),
            "swap_used_mib": _to_mib(self._peak_swap_bytes),
            "samples": int(self._samples),
            "peak_at_s": float(self._peak_at_s),
        }
