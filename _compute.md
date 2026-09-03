# Compute: Baramati HPC

Rewritten 3 September 2026 from a read-only survey of the cluster and one probe
job on a compute node. Everything below is measured. The version before this was
assembled on 26 August from local config and old job scripts without touching
the machine, and it was wrong about most of what mattered.

## It is reachable

Laptop at 172.16.51.113, cluster at 172.16.100.105, ping under 1 ms. The old
note said off-LAN with no route, and that was true then. It is not true now.

```
Host baramati
    HostName 172.16.100.105
    User kartikshirode
    Port 22
    ServerAliveInterval 30
    ServerAliveCountMax 4
```

Key based, no prompt. `ssh baramati hostname` answers `aicoeserver01`.

## What is actually there

| Property | Measured |
| --- | --- |
| Login node | `aicoeserver01`, Rocky Linux 9.8, 128 cores, 503 GB RAM |
| Scheduler | Slurm 24.11.7 |
| Partitions | `gpu`, and nothing else |
| Time limit | infinite |
| Compute nodes | `aicoeserver03`, `04`, `05`, Rocky Linux 9.7 |
| Per node | 256 CPUs, 2 sockets of 64 cores at 2 threads, 1000 GB RAM |
| GPU | MIG slices, `1g.18gb` x14 on 03 and 04, `1g.24gb` x8 on 05 |
| Node state | all three idle when surveyed, queue empty |
| Home | 128 TB filesystem, 126 TB free, my home 60 GB, no quota enforced |
| `/data` | 128 TB, 127 TB free |
| Node local `/tmp` | 728 GB, 675 GB free |
| Modules | `cuda-12.8`, `anaconda3-22.5`, `miniconda3` |
| Conda envs | base, tensorflow, torch-gpu, torch-gpu-blackwell, torch-gpu-pip |

The partition is called `gpu` and that name is misleading. Each node is a 256
core machine with a terabyte of memory that happens to also carry MIG slices. A
CPU bound sweep asks for cores and no GRES and gets the whole thing.

## The one thing that decides everything

**Rootless podman works on a compute node.** Version 5.6.0. Under `sbatch` I
pulled `docker.io/library/alpine:3.20`, ran it, watched it print its own core
count, and removed the image. There is no `/etc/subuid` entry for this user and
it worked regardless, with warnings about a network file system backing store
and no systemd user session. Setting `XDG_RUNTIME_DIR` to a job local path under
`/tmp` is what made it go.

This matters because the nodes carry no ROS, no Gazebo, no PX4, no cmake and no
`g++`. Native building is not an option. A container is the only route for this
stack, and the route is open.

There is no apptainer and no singularity, which is what the old note expected to
find. Podman is the substitute and it is a good one.

## Internet, from inside a compute node

Full outbound. Measured from `aicoeserver03` inside a job:

| Target | Result |
| --- | --- |
| github.com | 200 in 0.12 s |
| registry-1.docker.io | 401, which is the auth challenge, so reachable |
| ghcr.io | 401, same |
| packages.ros.org | 200 in 0.72 s |

The old note said to assume no internet on compute nodes and stage everything.
That assumption came from the Vaani work and it does not hold here. An image can
be pulled or built on the node.

## Traps, kept and corrected

Carried from the earlier projects, plus two found today.

- **`--cpus-per-task` defaults to 1.** Not setting it silently single-threads
  the job. Still true and still the easiest way to waste a 256 core box.
- **The cgroup does not isolate CPUs.** A job asking for 8 CPUs still saw
  `cpuset.cpus.effective = 0-255`, and `nproc` inside it reported 256. Anything
  that autoscales, a container build above all, will grab the whole node. Pin
  the parallelism by hand.
- **CRLF kills `sbatch`.** Git on Windows converted job scripts once and the
  scheduler refused them. Write LF and check the bytes before submitting.
- **Windows ssh strips quotes.** Never send a python one-liner or a heredoc
  through `ssh`. Write the file, `scp` it, run it. This is the same failure mode
  that mangles heredocs through `wsl.exe`, and it has cost this project time on
  both machines.
- **`srun` was broken** on the older Slurm, failing with "Job credential
  expired". The binary is present under 24.11.7 and whether it still fails is
  being retested. Until that answer lands, everything goes through `sbatch`.
- **uv venvs on the cluster have no pip.** Call `.venv/bin/python` directly.
- **Only `torch-gpu` is built for sm_120.**

## Layout convention, unchanged

```
~/envs/<name>          python env for the project
~/<project>/repo       the synced subset
~/<project>/out/<jobid>/   one output file per array task
```

Per task scratch goes to node local `/tmp` and is deleted on exit, because array
tasks that mutate a shared tree stomp on each other. `~/aids/sweep.slurm` is the
working array job pattern to adapt.

## What this does for UAV-X

The old note's conclusion was "develop locally, sweep on the cluster", and that
survives contact with the real machine. What changes is the confidence.

**Development stays local.** Three reasons, and only one of them has weakened.
Gazebo development wants a display and the Stage 1 deliverable includes a demo
video. Iterating on a simulation is interactive and a submit-wait-read loop is
the wrong shape for it. Multi-vehicle SITL is CPU bound rather than GPU bound,
and for four vehicles the laptop is genuinely fine, with the advantage of a
screen. The third reason is the one that weakened: 256 cores per node is a lot
more CPU than 20 threads, so the cluster would be faster if the stack were on
it. It is not on it yet, and getting it there is its own piece of work.

**The sweep is where it pays.** Once the swarm works, the proposal wants
evidence across swarm sizes, failure timings, range thresholds and topologies.
Three idle 256 core nodes with a terabyte each turn that from a week of serial
runs into one array job. That was always the plan; the survey just makes it
concrete.

**The container is being proved now rather than in week 4.** The plan puts the
sweep in week 4, and a container that turns out not to work would be discovered
with no room left. Chunk 1.7 sits early in the plan for exactly this reason.
Feasibility work is running in parallel with week 2 and lands in
[stage-1/hpc/](stage-1/hpc/) when it has something to say.

Nothing in weeks 2 or 3 depends on the cluster, and nothing should be made to.

## What the survey answered

All six unknowns the previous version listed.

1. **Partitions.** Only `gpu`. There is no separate CPU partition, and none is
   needed, because the gpu nodes are the CPU machines.
2. **Nodes and slices.** Three, 256 CPUs and 1000 GB each. MIG slices as above.
3. **Compute node internet.** Yes, unrestricted as far as anything tested.
4. **Storage quota.** None enforced. 126 TB free on home.
5. **Apptainer or Singularity.** Neither. Podman, and it works rootless.
6. **MPI.** `mpiexec` on the login node, `mpirun` and `mpicc` absent, no MPI
   module. Nothing here is configured for a real MPI build. That decides
   nothing for UAV-X and matters for CycloProp Stage 2, where CFD would need it.

## CycloProp

Unchanged in shape and stronger in substance. Stage 1 is a design document and
needs no compute. Stage 2 is a full CAE package, and a 256 core node with a
terabyte of memory and no wall clock limit is a serious asset for CFD in
December. The missing MPI is the thing to resolve before then.
