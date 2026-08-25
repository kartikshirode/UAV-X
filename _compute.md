# Compute: Baramati HPC

Assembled 26 August 2026 from the local config and from job scripts in two earlier projects that ran on these same servers. Nothing here came from touching the cluster, which is off-LAN right now.

## Access

```
Host baramati
    HostName 172.16.100.105
    User kartikshirode
    Port 22
    ServerAliveInterval 30
    ServerAliveCountMax 4
```

Key is `~/.ssh/id_ed25519`, comment `kartik@baramati`. Host keys for .105 are already in `known_hosts`, so this has connected before. Vendor is Benchmark Computer Solutions. X11 forwarding is present but commented out in the config and needs MobaXterm or VcXsrv running locally.

`172.16.100.105` is RFC1918, so the cluster is reachable only from its own network. Off that network there is no route, which is the current state. Nothing in the Stage 1 plan should have a hard dependency on it.

## What the cluster is, from prior job scripts

| Property | Value | Evidence |
| --- | --- | --- |
| Scheduler | Slurm | `sbatch`, `squeue`, array jobs throughout |
| Partition | `gpu` | `#SBATCH -p gpu` in every script found |
| Nodes seen | `aicoeserver03`, `aicoeserver05` | IDS README, Vaani DEPLOY |
| GPU | MIG slices | `--gres=gpu:1g.18gb:1` and `gpu:1g.24gb:1` |
| CPUs per task used | 8, 12, 16 | across the four job scripts |
| Memory used | 24G, 32G, 48G, 96G | same |
| Walltime used | 15 min to 12 h | `--time=12:00:00` was accepted |
| Conda | `/home/apps/codes/miniconda3/etc/profile.d/conda.sh` | IDS `sweep.slurm` |

## Traps already paid for

These come out of the earlier projects. They cost time once and should not cost it again.

- **`srun` is broken.** It fails with "Job credential expired". Everything goes through `sbatch`. Expect a fight if you want an interactive shell.
- **`--cpus-per-task` defaults to 1.** Not setting it silently single-threads the job.
- **CRLF kills `sbatch`.** Git on Windows converted slurm scripts once and the scheduler refused them. Force LF with `.gitattributes` and run `dos2unix` or `sed -i "s/\r$//"` on job files after any sync anyway.
- **Windows ssh strips quotes.** Never send a python one-liner or a heredoc through `ssh`. Write the file, `scp` it, run it.
- **uv venvs on the cluster have no pip.** Call `.venv/bin/python` directly.
- **Only the `torch-gpu` conda env is built for sm_120.**
- **Compute node outbound internet is not assumed.** The Vaani work shipped a probe job specifically to test it and set `HF_HUB_OFFLINE=1` in the real jobs. Assume no internet on compute nodes until proven otherwise, and stage everything you need.
- **Stage the repo, do not clone on the cluster.** Both prior projects tar and scp a subset rather than cloning, so what runs matches what you have locally.

## Layout convention from prior projects

```
~/envs/<name>          python env for the project
~/<project>/repo       the synced subset
~/<project>/out/<jobid>/   one output file per array task
```

Per-task scratch goes to node-local `/tmp` and is deleted on exit, because array tasks that mutate a shared tree stomp on each other.

## What this actually does for us

### UAV-X: useful later, not now

Worth being blunt, because the instinct is to assume a cluster solves the environment problem. It does not, for three reasons.

1. **Gazebo development wants a display.** It can run headless, but building a swarm simulation without watching it is slow going, and the Stage 1 deliverable includes a demo video.
2. **No interactive jobs.** `srun` being broken means batch submission only. Iterating on a simulation is inherently interactive, and a submit-wait-read loop is the wrong shape for the build phase.
3. **Multi-vehicle SITL is CPU bound, not GPU bound.** The laptop has 14 cores, 20 threads and 23.7 GB of RAM. For 4 to 6 vehicles that is genuinely the better machine, because it also has a screen.

Where the cluster earns its place is **after** the simulation works. Once there is a working swarm, the proposal wants evidence across many scenarios: swarm sizes, failure timings, range thresholds, topologies. That is an embarrassingly parallel sweep and it is exactly the shape of the existing `sweep.sh` array-job pattern from the IDS project, which can be adapted rather than rewritten.

**So: develop locally, sweep on the cluster.** Local WSL2 Ubuntu is still required and the cluster does not remove that. And since the cluster is off-LAN today, week 1 cannot depend on it at all.

### CycloProp: nearly irrelevant for Stage 1, important for Stage 2

Stage 1 is a design document. Literature, sizing arithmetic, a weight budget and writing. It needs no compute.

Stage 2 is where this matters, and it matters a lot: a full CAE package with CAD, kinematic and aerodynamic analysis and structural assessment. A 16-core, 96 GB, 12-hour job on the `gpu` partition is a real asset for CFD then.

Worth noticing the shape of that: the compute barely helps the thing due in 32 days and helps a great deal with the thing due in December.

## Unknowns worth resolving when the cluster is next reachable

None of these block Stage 1.

- Full partition list. Only `gpu` has been observed, and there may be a CPU partition better suited to SITL sweeps and CFD.
- Node count, cores per node, and what the MIG slices sit on.
- Whether compute nodes have outbound internet.
- Storage quota per user.
- Whether Apptainer or Singularity exists, which would be the clean way to carry a PX4 and ROS 2 stack across.
- Whether MPI is configured, which decides how CFD gets parallelised for CycloProp Stage 2.
