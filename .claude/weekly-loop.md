# weekly-loop config, UAV-X

Per-repo config for the `weekly-loop` skill. One tick runs one plan week. The supervisor never implements; it reads state, spawns a week-agent, then runs the gate in its own shell.

## Paths

| Key | Value |
| --- | --- |
| `repo_path` | `c:/Users/Kartik/Documents/Kartik/EDU/Local/Projects/Techfest/PUSHPAK-Grand-Challenge/UAV-X` |
| `plan` | `stage-1/plan.md` |
| `context` | `context.md` |
| `architecture` | `stage-1/architecture.md` |
| `decisions_locked` | `stage-1/decisions.md` |
| `progress_file` | `docs/progress/week-<N>.md` |
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

## The gate

There is one gate command per week and it is a script, not a list:

```
wsl.exe -d Ubuntu-22.04 -- bash -lc "cd '<repo_path_in_wsl>' && bash scripts/gate.sh <N>"
```

where `<repo_path_in_wsl>` is `/mnt/c/Users/Kartik/Documents/Kartik/EDU/Local/Projects/Techfest/PUSHPAK-Grand-Challenge/UAV-X`.

`scripts/gate.sh` is the only definition of what each week must satisfy. It runs preflight, builds the workspace, then the week's own checks. Round 2 finding 2: these commands used to be written out here, in the plan and in decisions.md, and the three had already drifted apart, with one version failing a correct implementation. Nothing in this file restates a threshold any more.

Three things about running it that are easy to get wrong, all confirmed on this machine:

- **Read the exit code from `wsl.exe` itself.** `wsl.exe -- bash -lc 'cmd; echo $?'` prints 0 no matter what happened. A gate checked that way passes forever.
- **Do not rely on `bash -lc` for the ROS environment.** Ubuntu's `.bashrc` returns on its second line for a non-interactive shell, so `ros2` is not on `PATH` and `AMENT_PREFIX_PATH` is unset. `gate-env.sh` loads it explicitly and asserts it landed.
- **Gates run headless.** PX4's own `sitl_multiple_run.sh` ignores `HEADLESS` and ends in an unconditional `gzclient`, which takes the whole WSL distro down. `scripts/sitl_multi.sh` is the launcher; the PX4 one must not be used.

Preflight refuses to run if a simulator is already up, because a gate inheriting someone else's gzserver is not measuring what it thinks.

## Rules digest

Handed to every week-agent. The repo's non-negotiables.

1. **The tx/rx seam is the submission.** Swarm nodes publish only to `/uavx/<own>/tx` and subscribe only to `/uavx/<own>/rx`. The allowlist is [stage-1/architecture.md](../stage-1/architecture.md) section 1. `scripts/check_seam.sh` enforces it over the source and over a captured graph, with a separate process manifest per scenario, and it runs again in W4 once roles code exists. Breaking it silently voids 25% of the rubric and nothing else notices.
2. **Never change a frozen value to pass a gate.** Every parameter in `architecture.md` and every threshold in `scripts/gate.sh` is fixed. Changing one so a run goes green is moving the goal. If a number is unreachable, stop and report BLOCKED with the arithmetic.
3. **Never quote a metric no run produced.** Every number in a doc traces to a JSONL under `runs/` that validates against `scenarios/run-record.schema.json`. No estimating, no carrying a figure forward without rerunning.
4. **Every run is seeded from its scenario and replays exactly.** A result nobody can reproduce is not evidence.
5. Assert on artifacts, not on metadata. `dpkg-query` reports a version for a package apt has merely heard of, and that let this repo pass a green check for hours on a machine with no simulator binaries. Check the file exists.
6. `grep -c`, never `grep -q`, inside a pipeline under `pipefail`. `grep -q` closes the pipe on its first match, the producer takes SIGPIPE, and the check inverts. It has bitten this repo five times.
7. Git: commit on `main`, never push, amend, force, branch or tag. No AI attribution, no `Co-Authored-By`, no emoji.
8. Prose in files follows the `humanizer` skill. Em dashes and en dashes never appear.
9. Third-party code stays in `~/ws_uavx`. Our packages live in `uavx_ws/src/` and are committed. Build output goes to ext4, never `/mnt/c`.
10. Each week drafts its own proposal section as its metric lands. W5 has no room to write six pages.

Review state, meaning which round has run and what it found, lives in `.claude/review-status.json`. Do not write it into a document; `scripts/check_docs.py` fails any document that does.

## Blocked triggers

Report BLOCKED rather than working around any of these:

- Sending the submission email, registering on techfest.org, or joining the WhatsApp group. All need the human. `check_submission.py` exits 2 for exactly this and the loop halts.
- A frozen parameter that appears unreachable. Report the arithmetic; do not retune it.
- A gate that cannot run headless. Headless is the documented path and the GUI binary crashes the distro.
- Anything needing the Baramati HPC. It is off-LAN and Stage 1 does not depend on it.
- `gh` is not installed. Nothing in Stage 1 needs it, since the submission goes by email and there is no portal, so this is a stop rather than a blocker: if a week finds a use for it, halt and say what for.
- A PX4, ROS or Gazebo version change. The set is pinned by SHA in `stage-1/setup/versions.lock` on purpose.

## Checkpoint

```
checkpoint_every: 1
```

Five weeks and W3 carries the submission, so a human read between every week is worth the pause. Drop to 2 only if the first two ticks come back clean.
