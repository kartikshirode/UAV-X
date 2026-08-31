"""Contract tests for the process-group resource sampler, chunk 1.6.

The question the plan asks of this chunk is whether the sampler includes child
processes or reports a reassuring fraction of the load. Everything below hangs
off that. The process tree is injected, so the whole file runs on Windows and
in WSL with nothing built and no /proc needed.
"""

import math
import os

import pytest

from uavx_sim import resource_sampler
from uavx_sim.resource_sampler import (
    ProcessGone,
    ProcessMemory,
    ProcfsProbe,
    ResourceSampler,
    ResourceSamplerError,
)

MIB = 1024 * 1024

# The shape of a real harness_check run. The runner itself is small. gzserver
# and the PX4 instances underneath it are not.
RUNNER_MIB = 120.0
BIG_CHILD_MIB = 2600.0
WHOLE_GROUP_MIB = RUNNER_MIB + 3 * BIG_CHILD_MIB      # 7920.0

RUNNER = 100
CHILDREN = [201, 202, 203]


class FakeProcessTree:
    """A process tree held in dictionaries, standing in for psutil or /proc.

    children() hands back direct children only, so the recursion under test is
    the sampler's and not the fake's. A pid can be made to exit between being
    listed and being read, which is the race a real PX4 restart produces.
    """

    source = "fake"

    def __init__(self, children=None, rss_mib=None, swap_mib=None,
                 vanish_on_read=()):
        self._children = {
            int(k): [int(c) for c in v] for k, v in (children or {}).items()
        }
        self._rss_mib = {int(k): v for k, v in (rss_mib or {}).items()}
        self._swap_mib = {int(k): v for k, v in (swap_mib or {}).items()}
        self._vanish_on_read = set(int(p) for p in vanish_on_read)
        self.read_pids = []

    def set_rss(self, pid, mib):
        self._rss_mib[int(pid)] = mib

    def set_swap(self, pid, mib):
        self._swap_mib[int(pid)] = mib

    def vanish_next_read(self, pid):
        """This pid exits after the enumeration lists it, before it is read."""
        self._vanish_on_read.add(int(pid))

    def children(self, pid):
        if pid not in self._rss_mib:
            raise ProcessGone(pid)
        return list(self._children.get(pid, ()))

    def memory(self, pid):
        self.read_pids.append(pid)
        if pid in self._vanish_on_read:
            self._vanish_on_read.discard(pid)
            self._rss_mib.pop(pid, None)
            raise ProcessGone(pid)
        if pid not in self._rss_mib:
            raise ProcessGone(pid)
        swap_mib = self._swap_mib.get(pid, 0.0)
        swap_bytes = None if swap_mib is None else int(round(swap_mib * MIB))
        return ProcessMemory(int(round(self._rss_mib[pid] * MIB)), swap_bytes)


def flat_runner_tree(**kwargs):
    """One runner with three heavy children hanging off it."""
    rss = {RUNNER: RUNNER_MIB}
    for pid in CHILDREN:
        rss[pid] = BIG_CHILD_MIB
    return FakeProcessTree(children={RUNNER: CHILDREN}, rss_mib=rss, **kwargs)


# --------------------------------------------------------------- the group

def test_the_peak_is_the_whole_group_and_not_the_reassuring_root_only_figure():
    """The failure this chunk exists to prevent.

    A sampler that reads pid 100 and stops there answers 120 MiB. The machine
    is carrying 7920. Both numbers are below the 10500 MiB ceiling, so the
    wrong one passes every gate and every later week inherits it.
    """
    probe = flat_runner_tree()
    sampler = ResourceSampler(root_pid=RUNNER, probe=probe)
    sampler.sample(1.0)

    peak = sampler.summary()["peak_rss_mib"]

    # This one first, so a root only sampler is told what it did rather than
    # only that two numbers differ.
    assert peak != pytest.approx(RUNNER_MIB), (
        "the sampler reported the runner process on its own. 120 MiB reads as "
        "comfortable headroom while the three processes underneath it are "
        "holding 7800 MiB more, which is the reassuring fraction of the load "
        "the plan names for chunk 1.6."
    )
    assert peak == pytest.approx(7920.0), (
        "the peak must be the sum over the root and all of its descendants, "
        "120 + 3 * 2600 MiB."
    )
    assert peak == pytest.approx(WHOLE_GROUP_MIB)
    assert set(probe.read_pids) == {RUNNER, 201, 202, 203}, (
        "every process in the group has to be read, not a subset of them."
    )


def test_a_descendant_two_levels_down_is_counted():
    # Launchers nest. gzserver under the launcher under the runner is normal,
    # and a walk that stops at direct children loses the largest process here.
    probe = FakeProcessTree(
        children={100: [200], 200: [300], 300: [400]},
        rss_mib={100: 50.0, 200: 60.0, 300: 2400.0, 400: 90.0},
    )
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(2600.0)


def test_a_child_that_exits_mid_sample_neither_crashes_nor_zeroes_the_reading():
    probe = flat_runner_tree()
    sampler = ResourceSampler(root_pid=RUNNER, probe=probe)
    sampler.sample(1.0)

    # 202 is listed by the enumeration and gone by the time it is read.
    probe.vanish_next_read(202)
    surviving = sampler.sample(2.0)

    assert surviving == int(round((RUNNER_MIB + 2 * BIG_CHILD_MIB) * MIB))
    assert surviving > 0
    got = sampler.summary()
    assert got["samples"] == 2
    assert got["peak_rss_mib"] == pytest.approx(WHOLE_GROUP_MIB)
    assert got["peak_at_s"] == 1.0


def test_a_cycle_in_the_reported_tree_terminates():
    # A reparenting race can briefly make the tree look circular. The walk must
    # stop rather than spin the run loop it is called from.
    probe = FakeProcessTree(
        children={100: [200], 200: [100, 300], 300: [200]},
        rss_mib={100: 10.0, 200: 20.0, 300: 30.0},
    )
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(60.0)
    assert sorted(probe.read_pids) == [100, 200, 300]


# ------------------------------------------------------------- extra pids

def test_an_extra_pid_that_is_not_a_descendant_is_included():
    # A launcher may daemonize gzserver, which reparents it away from the
    # runner. It still belongs to the budget.
    probe = FakeProcessTree(
        children={100: [200]},
        rss_mib={100: 100.0, 200: 200.0, 900: 3000.0},
    )
    sampler = ResourceSampler(root_pid=100, extra_pids=[900], probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(3300.0)


def test_the_descendants_of_an_extra_pid_are_included_too():
    probe = FakeProcessTree(
        children={100: [], 900: [901]},
        rss_mib={100: 100.0, 900: 200.0, 901: 1500.0},
    )
    sampler = ResourceSampler(root_pid=100, extra_pids=[900], probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(1800.0)


def test_pids_added_after_construction_are_included():
    probe = FakeProcessTree(rss_mib={100: 100.0, 900: 400.0})
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.add_pids([900])
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(500.0)


def test_a_pid_that_is_both_a_descendant_and_an_extra_is_counted_once():
    probe = FakeProcessTree(
        children={100: [200]},
        rss_mib={100: 100.0, 200: 700.0},
    )
    sampler = ResourceSampler(root_pid=100, extra_pids=[200, 200, 100],
                              probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(800.0)
    assert sorted(probe.read_pids) == [100, 200]


# ------------------------------------------------------------------ peaks

def test_the_peak_is_the_maximum_across_samples_and_not_the_last_one():
    probe = FakeProcessTree(rss_mib={100: 1000.0})
    sampler = ResourceSampler(root_pid=100, probe=probe)

    sampler.sample(0.0)
    probe.set_rss(100, 4000.0)
    sampler.sample(10.0)
    probe.set_rss(100, 1500.0)
    sampler.sample(20.0)

    got = sampler.summary()
    assert got["peak_rss_mib"] == pytest.approx(4000.0)
    assert got["peak_rss_mib"] != pytest.approx(1500.0)


def test_peak_at_s_carries_the_simulated_time_of_the_peak_sample():
    probe = FakeProcessTree(rss_mib={100: 1000.0})
    sampler = ResourceSampler(root_pid=100, probe=probe)

    sampler.sample(0.0)
    probe.set_rss(100, 4000.0)
    sampler.sample(37.5)
    probe.set_rss(100, 4000.0)
    sampler.sample(41.0)
    probe.set_rss(100, 900.0)
    sampler.sample(60.0)

    got = sampler.summary()
    # The first sample to reach the maximum owns it. A later tie did not cause
    # the peak and must not move the timestamp.
    assert got["peak_at_s"] == 37.5
    assert isinstance(got["peak_at_s"], float)


def test_samples_counts_every_sample_taken():
    probe = FakeProcessTree(rss_mib={100: 500.0})
    sampler = ResourceSampler(root_pid=100, probe=probe)
    for tick in range(12):
        sampler.sample(float(tick))

    assert sampler.samples == 12
    assert sampler.summary()["samples"] == 12
    # The gate wants at least 10 of them.
    assert sampler.summary()["samples"] >= 10


def test_sim_time_must_be_a_real_time_at_or_after_zero():
    probe = FakeProcessTree(rss_mib={100: 500.0})
    sampler = ResourceSampler(root_pid=100, probe=probe)

    with pytest.raises(ValueError):
        sampler.sample(-1.0)
    with pytest.raises(ValueError):
        sampler.sample(float("nan"))


# ------------------------------------------------------------- the floor

def test_a_sampler_that_never_sampled_cannot_report_a_passing_summary():
    sampler = ResourceSampler(root_pid=100, probe=FakeProcessTree(
        rss_mib={100: 500.0}))

    with pytest.raises(ResourceSamplerError) as caught:
        sampler.summary()
    assert "sample" in str(caught.value)
    assert sampler.samples == 0


def test_a_group_that_read_nothing_cannot_report_a_peak():
    # Every read failed, which means the probe is wrong, not that four PX4
    # instances were free. Reporting 0.0 here would fail the gate anyway;
    # raising says which of the two happened.
    probe = FakeProcessTree(children={}, rss_mib={})
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    with pytest.raises(ResourceSamplerError):
        sampler.summary()


# ---------------------------------------------------------------- swap

def test_swap_is_summed_across_the_group_and_kept_at_its_peak():
    probe = FakeProcessTree(
        children={100: [200]},
        rss_mib={100: 100.0, 200: 900.0},
        swap_mib={100: 1.0, 200: 3.0},
    )
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)
    probe.set_swap(200, 0.0)
    sampler.sample(1.0)

    assert sampler.summary()["swap_used_mib"] == pytest.approx(4.0)


def test_swap_the_kernel_would_not_report_is_not_written_down_as_zero():
    # Unknown and clean read identically in a record, and the gate treats
    # swap_used_mib == 0 as proof the run stayed inside its budget.
    probe = FakeProcessTree(
        children={100: [200]},
        rss_mib={100: 100.0, 200: 900.0},
        swap_mib={200: None},
    )
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    with pytest.raises(ResourceSamplerError) as caught:
        sampler.summary()
    assert "swap" in str(caught.value)
    assert "200" in str(caught.value)


def test_a_single_swapped_page_does_not_round_down_to_a_clean_zero():
    probe = FakeProcessTree(rss_mib={100: 500.0})
    probe.set_swap(100, 4096.0 / MIB)            # one page
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["swap_used_mib"] > 0.0


def test_even_one_swapped_byte_survives_the_rounding():
    probe = FakeProcessTree(rss_mib={100: 500.0})
    probe.set_swap(100, 1.0 / MIB)
    sampler = ResourceSampler(root_pid=100, probe=probe)
    sampler.sample(0.0)

    assert sampler.summary()["swap_used_mib"] > 0.0


# -------------------------------------------------------- the record shape

def test_summary_is_exactly_the_block_the_run_record_asks_for():
    probe = flat_runner_tree()
    sampler = ResourceSampler(root_pid=RUNNER, probe=probe)
    for tick in range(10):
        sampler.sample(float(tick))
    got = sampler.summary()

    assert set(got) == {"peak_rss_mib", "swap_used_mib", "samples",
                        "peak_at_s"}
    assert isinstance(got["samples"], int)
    for key in ("peak_rss_mib", "swap_used_mib", "peak_at_s"):
        assert isinstance(got[key], float), key
        assert not isinstance(got[key], str)
    # What scripts/gate.sh asserts on this block, spelled out.
    assert got["peak_rss_mib"] > 0
    assert got["peak_rss_mib"] < 10500
    assert got["swap_used_mib"] == 0
    assert got["samples"] >= 10


# ------------------------------------------------------------ the probes

def write_fake_proc(root, pid, rss_pages, ppid=0, children=(), swap_kb=0,
                    with_children_file=True):
    """One process directory in the shape the kernel writes it."""
    proc_dir = root / str(pid)
    task_dir = proc_dir / "task" / str(pid)
    task_dir.mkdir(parents=True)
    proc_dir.joinpath("statm").write_text(
        "2048 {0} 100 10 0 200 0\n".format(rss_pages))
    status = "Name:\tfake\nPPid:\t{0}\n".format(ppid)
    if swap_kb is not None:
        status += "VmSwap:\t{0} kB\n".format(swap_kb)
    proc_dir.joinpath("status").write_text(status)
    if with_children_file:
        task_dir.joinpath("children").write_text(
            " ".join(str(child) for child in children) + "\n")


def test_the_procfs_probe_reads_the_tree_through_the_kernel_children_file(
        tmp_path):
    write_fake_proc(tmp_path, 100, rss_pages=256, children=[201])
    write_fake_proc(tmp_path, 201, rss_pages=512, ppid=100)
    sampler = ResourceSampler(
        root_pid=100, probe=ProcfsProbe(root=str(tmp_path), page_size=4096))
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(3.0)


def test_the_procfs_probe_falls_back_to_scanning_parent_pids(tmp_path):
    # Kernels built without CONFIG_PROC_CHILDREN have no children file, and
    # believing them would make every runner look childless.
    write_fake_proc(tmp_path, 100, rss_pages=256, with_children_file=False)
    write_fake_proc(tmp_path, 201, rss_pages=512, ppid=100,
                    with_children_file=False)
    sampler = ResourceSampler(
        root_pid=100, probe=ProcfsProbe(root=str(tmp_path), page_size=4096))
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(3.0)


def test_the_procfs_probe_reads_per_process_swap(tmp_path):
    write_fake_proc(tmp_path, 100, rss_pages=256, children=[201], swap_kb=512)
    write_fake_proc(tmp_path, 201, rss_pages=256, ppid=100, swap_kb=512)
    sampler = ResourceSampler(
        root_pid=100, probe=ProcfsProbe(root=str(tmp_path), page_size=4096))
    sampler.sample(0.0)

    assert sampler.summary()["swap_used_mib"] == pytest.approx(1.0)


def test_the_procfs_probe_skips_a_pid_whose_directory_vanished(tmp_path):
    write_fake_proc(tmp_path, 100, rss_pages=256, children=[201, 999])
    write_fake_proc(tmp_path, 201, rss_pages=512, ppid=100)
    sampler = ResourceSampler(
        root_pid=100, probe=ProcfsProbe(root=str(tmp_path), page_size=4096))
    sampler.sample(0.0)

    assert sampler.summary()["peak_rss_mib"] == pytest.approx(3.0)


def test_a_missing_process_directory_is_reported_as_gone(tmp_path):
    probe = ProcfsProbe(root=str(tmp_path), page_size=4096)
    with pytest.raises(ProcessGone):
        probe.memory(4242)
    with pytest.raises(ProcessGone):
        probe.children(4242)


def test_the_fallback_away_from_psutil_is_announced(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_sampler, "psutil", None)
    (tmp_path / "1").mkdir()

    with pytest.warns(RuntimeWarning):
        probe = resource_sampler.default_probe(procfs_root=str(tmp_path))

    assert probe.source == "procfs"


def test_a_machine_with_no_way_to_read_memory_refuses_rather_than_reports_zero(
        tmp_path, monkeypatch):
    monkeypatch.setattr(resource_sampler, "psutil", None)

    with pytest.raises(ResourceSamplerError):
        resource_sampler.default_probe(procfs_root=str(tmp_path / "absent"))


@pytest.mark.skipif(not os.path.isdir("/proc"),
                    reason="a real reading needs a Linux /proc")
def test_one_real_reading_of_this_process_group():
    # Deliberately no assertion about the size. Real memory moves, and a test
    # that pins it is a test that fails for the wrong reason.
    sampler = ResourceSampler(root_pid=os.getpid())
    for tick in range(3):
        sampler.sample(float(tick))
    got = sampler.summary()

    assert math.isfinite(got["peak_rss_mib"])
    assert got["peak_rss_mib"] > 0.0
    assert math.isfinite(got["swap_used_mib"])
    assert got["swap_used_mib"] >= 0.0
    assert got["samples"] == 3
    assert 0.0 <= got["peak_at_s"] <= 2.0
