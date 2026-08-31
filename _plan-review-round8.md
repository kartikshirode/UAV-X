# Plan review, round 8 (Codex)

Reviewed and edited from HEAD `5ed1c26` to implementation HEAD `4e6bdc3`. Files changed: `.gitignore`, `INSTALL.md`, `THIRD-PARTY.md`, `context.md`, `research/techfest-uav-x.json`, both active schemas, `stage-1/architecture.md`, `stage-1/plan.md`, `stage-1/human-preflight.md`, setup verification and submission tooling, plus the contract, gate, seam, install, rehearsal, source-freeze and submission scripts and their fixtures under `scripts/`. `_design-options-round8.md`, this review and `.claude/review-status.json` are the review record committed after that implementation SHA.

## Still open from earlier rounds

- Round 7 finding 1 holds. W1 uses its local validator before W2 exists. This round also removed the hidden runner cycle by putting the first complete harness in 1.7.
- Round 7 finding 2 holds. The archive installer uses an isolated target and the actual delivered guide. This round made the root guide canonical and carried the disposable overlay into every later shell.
- Round 7 finding 3 holds. Evidence paths stay under `runs/` and must be delivered. This round added a byte comparison between the validated record and its delivered copy.
- Round 7 finding 4 holds. Positive traffic and observation counts remain in the gates, and the empty-set and low-rate package fixtures still reject.
- Round 7 finding 5 holds. The memory ceiling, no-swap rule and sample floor remain checked.
- Round 7 finding 6 holds. Outside-process allowlists reject undeclared topics and message types. The expanded seam suite passes 42 cases.
- Round 7 finding 7 holds. Exact message blocks, runner codes, examples and requirement grammar are now machine checked.
- Round 7 finding 8 holds. The observation ledger proves the outage set and both drain clocks. This round also requires one named custodian to hold the whole queue-drain backlog.
- Round 7 finding 9 holds. The four-week budget and chunk ownership still match the dated plan.
- Round 7 finding 10 holds. The fallback agrees across active documents, and active files no longer invent W5.

None remain open.

## What I changed

- `0de657a` froze the scenario and message contracts.
- `e83e526` closed the remaining seam allowlist gaps.
- `078b5e7` bound submission claims to measured evidence.
- `cf2d627` made fresh installs disposable and strict.
- `0a1ae8a` retained and checked recording rehearsal artifacts.
- `b1c79d9` made the W1 contracts independently testable and fixed their order.
- `1744ebf` kept install and recording rehearsals inside disposable targets.
- `21b7405` bound delivered evidence and installation instructions to checked bytes.
- `c617b96` recorded the 31 August organiser wording and moved spec freshness out of tracked state.
- `4e6bdc3` rejected invalid gate selectors before environment setup.

## Problems and choices

- Problem 1 chose exact comparison for the five generated messages and explicit runner arguments.
- Problem 2 chose strict scenario-boundary validation.
- Problem 3 chose schema validation plus small cross-field semantic checks.
- Problem 4 chose focused W1 component tests followed by integration in 1.7.
- Problem 5 chose exact per-process seam allowlists.
- Problem 6 chose an observation ledger with one named outage custodian.
- Problem 7 chose hashing each validated run and comparing its delivered bytes.
- Problem 8 chose direct type, path, archive and duplicate checks for package input.
- Problem 9 chose root `INSTALL.md` as the single committed guide.
- Problem 10 chose disposable environment paths passed through every target shell.
- Problem 11 chose one strict extracted shell for executable Markdown blocks.
- Problem 12 chose retaining and validating the full recording run and graph.
- Problem 13 chose resolved, narrow temp prefixes before recursive cleanup.
- Problem 14 chose executable Python probes and fatal byte-scan errors in the shell checker.
- Problem 15 chose a committed spec seed with ignored live freshness state.
- Problem 16 chose checking external-tool return codes and finite output.
- Problem 17 chose one accepted fixture baseline followed by one mutation per case.
- Problem 18 chose removing W5 from active files while leaving historical reviews intact.
- Problem 19 chose validating gate selectors before loading ROS.

The costs and rejected mechanisms are in `_design-options-round8.md`.

## Findings I did not fix

None.

## Verification

The final pass, after the last implementation change and review files, returned:

- `python3 scripts/check_geometry.py`, exit 0. Its built-in negative geometry proves the old fade-band placement, unsafe unconstrained slot and midpoint hop all fail the frozen rules.
- `python3 scripts/check_docs.py`, exit 0. I inserted an early `run_scenario.sh` call in chunk 1.3; it exited 1 and named the 1.3-before-1.7 dependency. The line was removed before the final pass.
- `python3 scripts/test_seam_fixtures.py`, exit 0. All 42 cases passed, including undeclared outside topics, blank endpoint types, stale provenance and tx/rx violations.
- `python3 scripts/test_submission_fixtures.py`, exit 0. Both accepted baselines and all 45 tamper mutations behaved correctly, for 47 checks total.
- `python3 scripts/test_dryrun_fixtures.py`, exit 0. The accepted rehearsal and all 18 mutations behaved correctly, for 19 checks total.
- `python3 scripts/test_gate_preflight.py`, exit 0. All eight online and offline decisions matched, and an unknown chunk was rejected before environment setup.
- `python3 scripts/test_require_grammar.py`, exit 0. The suite rejected wrong paths, missing keys, non-finite numbers, bad regular expressions and malformed expressions; all 113 gate requirements parsed.
- `bash scripts/check_shell.sh`, exit 0 under login Git Bash. I added a literal `\n` token to a tracked shell script first; the checker exited 1 and named its exact line. The token was removed before the final pass.
- `python3 scripts/check_competition_spec.py`, exit 0 with live network access. The first run exited 1 on a real `about` change, the removed IISERB presentation phrase. After reading the exact diff and updating `context.md` with the captured field, the checker matched all 18 binding fields and the unchanged PDF hash. Its ignored receipt left tracked state clean.
- `bash scripts/gate.sh chunks`, exit 0 under login Git Bash and listed all 25 chunks. A separate `9.9` probe is now in the preflight fixture and fails on the unknown id before ROS is loaded.

Extra focused checks also passed: `test_message_contract.py`, `test_record_contract.py`, `test_scenario_fixtures.py` and `test_install_md_fixtures.py`. Manual traversal values for both disposable-prefix scripts exited 1 before deletion and named the resolved unsafe path. `git diff --check` passed, and no authored review prose contains an em dash or en dash.

## Verdict

READY TO EXECUTE. The plan has 25 ordered chunks, each public contract now has a rejection path and the final package binds source, guide, runs, graph, video and delivery bytes. No frozen threshold moved. No implementation package or scenario YAML was added. The known human preflight still blocks execution by design; it is not a plan finding.

## For the next round

Run the new W1 component tests against the first real `uavx_ws` implementation, then run chunk 1.7 from a clean checkout. Watch the exact ROS interface rendering, target overlay selection and graph allowlists because those are the places implementation can disagree with a sound contract. Before the send, repeat the archive install in a disposable distro and keep that target until the package is recorded.

Findings: 0
