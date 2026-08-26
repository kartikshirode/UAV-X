# weekly-loop config, UAV-X

Per-repo config for the `weekly-loop` skill. One tick runs one plan week. The supervisor never implements; it reads state, spawns a week-agent, then runs every gate below in its own shell.

## Paths

| Key | Value |
| --- | --- |
| `repo_path` | `c:/Users/Kartik/Documents/Kartik/EDU/Local/Projects/Techfest/PUSHPAK-Grand-Challenge/UAV-X` |
| `plan` | `stage-1/plan.md` |
| `context` | `context.md` |
| `decisions_locked` | `stage-1/decisions.md` |
| `progress_dir` | `docs/progress/` |
| `progress_file` | `docs/progress/week-<N>.md` |
| `audit_dir` | `docs/audits/` |
| `audit_file` | `docs/audits/week-<N>.md` |
| `decisions_log` | `docs/decisions.md` |
| `journal` | `docs/journal.md` |
| `handoff` | `handoff.md` |
| `sentinel` | `.claude/.weekly-loop-sentinel` |
| `runs_dir` | `runs/` |

## Markers

Literal strings the supervisor greps for. No judgment reads.

| Marker | Where | Literal |
| --- | --- | --- |
| week done | progress file | `WEEK-<N>-DONE` |
| audit complete | audit file | `AUDIT-COMPLETE` |
| next week | handoff | `NEXT-WEEK: <N+1>` |

## Shell

Every gate runs inside WSL, because none of this stack exists on Windows. The supervisor runs each gate as:

```
wsl.exe -d Ubuntu-22.04 -- bash -lc 'cd /mnt/c/Users/Kartik/Documents/Kartik/EDU/Local/Projects/Techfest/PUSHPAK-Grand-Challenge/UAV-X && <gate>'
```

Sourcing is the week-agent's job, not the gate's. `~/.bashrc` already sources `/opt/ros/humble/setup.bash` and `~/ws_uavx/install/setup.bash`, and `bash -lc` picks both up.

Scenario gates run headless. `HEADLESS=1` is set inside `run_scenario.sh`, so no gate needs a display.

## Gates

Two apply every week, then the week's own.

**Every week:**

```
bash stage-1/setup/verify.sh
colcon build --symlink-install --base-paths uavx_ws
```

**W1**

```
bash scripts/run_smoke.sh --vehicles 4
```

**W2**

```
colcon test --packages-select uavx_mission uavx_eval && colcon test-result --verbose
bash scripts/run_scenario.sh scenarios/survey_baseline.yaml
python3 -m uavx_eval.check runs/latest.jsonl --require "coverage_fraction>=0.95"
```

**W3**

```
colcon test --packages-select uavx_comms uavx_msgs && colcon test-result --verbose
bash scripts/run_scenario.sh scenarios/relay_required.yaml
python3 -m uavx_eval.check runs/latest.jsonl --require "delivery_ratio>=0.95" --require "mean_hop_count>1.0"
bash scripts/run_scenario.sh scenarios/direct_only.yaml
python3 -m uavx_eval.check runs/latest.jsonl --require "delivery_ratio<0.5"
```

**W4**

```
colcon test --packages-select uavx_roles uavx_sim && colcon test-result --verbose
bash scripts/run_scenario.sh scenarios/relay_kill.yaml
python3 -m uavx_eval.check runs/latest.jsonl --require "time_to_reconnect_s<=30" --require "delivery_ratio_after_recovery>=0.90" --require "separation_violations==0" --require "role_changes>=1"
```

**W5**

```
python3 scripts/check_submission.py
```

`colcon test` alone exits 0 even when tests fail, which is why `colcon test-result --verbose` follows it every time. Dropping that turns the test gate into decoration.

## Rules digest

Handed to every week-agent. These are the repo's non-negotiables.

1. **The tx/rx seam is the submission.** Swarm nodes publish only to `/uavx/<id>/tx` and subscribe only to `/uavx/<id>/rx`. No swarm node ever subscribes to another vehicle's topics. A test enforces this. Breaking it silently voids 25% of the rubric and nothing else in the repo will notice.
2. **Never quote a metric that no run produced.** Every number in a doc traces to a JSONL under `runs/`. No estimating, no "roughly", no carrying a figure forward from an earlier week without rerunning.
3. **Every run is seeded from its scenario file and replays exactly.** A result nobody can reproduce is not evidence.
4. Git: commit on `main`, never push, never amend, never force, never branch, never tag. No AI attribution, no `Co-Authored-By`, no emoji. Messages read as kartikshirode wrote them.
5. Prose written to any file follows the `humanizer` skill, and em dashes and en dashes never appear in file prose.
6. Third-party code stays in `~/ws_uavx` and out of the repo. Our packages live in `uavx_ws/src/` and get committed.
7. Fallbacks are in [stage-1/decisions.md](../stage-1/decisions.md). Take a stated fallback rather than inventing a new direction mid-week.

## Blocked triggers

Report BLOCKED rather than working around any of these:

- A gate needs a display and WSLg is not providing one. Headless is the documented path; a gate that genuinely cannot run headless is a plan bug, not something to fake.
- Registering on techfest.org, joining the WhatsApp group, or sending the organiser email. All need the human.
- Anything requiring the Baramati HPC. It is off-LAN and `srun` is broken there. Stage 1 does not depend on it.
- `gh` is not installed, so anything needing the GitHub CLI stops until the human installs it.
- A PX4, ROS or Gazebo version bump. The set is pinned in decisions.md on purpose; changing it is a human call.

## Checkpoint

```
checkpoint_every: 1
```

Five weeks total and W3 carries the submission, so a human read between every week is worth the pause. Drop this to 2 only if the first two ticks come back clean.
