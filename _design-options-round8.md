# Design options, round 8

## Problem 1: public message and runner contracts could drift quietly
**Where it bites** W1.1, W1.7 and every later scenario gate
**Found by** this round

### Option A: compare the five built interfaces exactly
Read the frozen message blocks from `architecture.md`, normalise comments and spacing, then compare every generated interface line by line. Also pass the runs directory on every runner call and assert the documented invalid-input exit. Cost: 4 hours and one small checker. It gives up tolerance for harmless field reordering. It fails if ROS prints a semantically equal type in a new textual form.

### Option B: generate messages and wrappers from one machine file
Move the contracts into a JSON or YAML source and generate both prose and ROS files. Cost: 10 to 14 hours plus a generator the build must carry. It gives up direct hand editing. It fails when generated files are not refreshed or reviewed.

### Option C: keep name and substring smoke checks
Resolve each type at runtime and look for selected fields, then rely on integration tests for the rest. Cost: 1 hour. It gives up exact widths, constants and ordering, which weakens every rubric row using packet identity or roles. It fails green when an incompatible definition still contains the searched words.

### Chosen: A
The contract is already small and frozen. Exact comparison is cheaper than a generator and says what the gate means. The cost is a deliberate sensitivity to representation.
**Fallback if it does not land:** B, if ROS interface output changes format and cannot be normalised without guessing.

## Problem 2: scenario files were accepted with ambiguous or impossible values
**Where it bites** W1.3 and all nine evidence runs
**Found by** this round

### Option A: reject at the scenario boundary
Require a non-empty matching name, UAV-only vehicle ids, finite values and events strictly before the end. Add focused rejected-input fixtures. Cost: 3 hours. It gives up permissive aliases such as `gcs` in the vehicle list. It fails if the implementation has a second parser that ignores this checker.

### Option B: let the runner repair input
Fill names from filenames, drop unknown vehicles and clamp late events. Cost: 2 hours. It gives up proof that the requested scenario is the scenario that ran. It fails by turning author mistakes into different experiments.

### Option C: validate only after simulation
Start the simulator and reject the final record when the values look wrong. Cost: 1 hour of code and many wasted run hours. It gives up fast feedback. It fails when a bad event never fires yet the runner exits cleanly.

### Chosen: A
Bad input should stop before PX4 starts. The stricter vehicle grammar matches the frozen design and costs no rubric credit.
**Fallback if it does not land:** C for an implementation-only rule that cannot be expressed in the file checker, while keeping the static checks.

## Problem 3: a schema-valid run could still have false time and provenance claims
**Where it bites** W1.7, W2.2 and all measured rubric rows
**Found by** this round

### Option A: pair the schema with semantic checks
Require `ros_sim_time`, exact RFC 3339 wall timestamps, finite JSON numbers, ordered wall time, unique vehicles, enough elapsed simulation and event times inside the run. Cost: 6 hours with fixtures. It gives up acceptance of loose ISO date forms. It fails if a new cross-field rule is added only to prose.

### Option B: move everything into a full JSON Schema engine
Use a system package with format and custom vocabulary support. Cost: 8 to 12 hours and another pinned dependency. It gives up the current dependency-free W1 validator. It fails on a clean target if the package is absent or behaves differently.

### Option C: trust `uavx_eval` only
Remove the W1 semantic pass and let the later evaluator own all checks. Cost: 2 hours. It gives up an independently testable W1 record contract. It fails because `uavx_eval` does not exist until W2.

### Chosen: A
The local validator stays small and usable before W2. The cost is maintaining a short semantic layer beside the schema.
**Fallback if it does not land:** B, once W1 can pin and install the chosen validator without adding setup risk.

## Problem 4: W1 called work owned by later W1 chunks
**Where it bites** chunks 1.3 to 1.7 and the first week schedule
**Found by** this round

### Option A: test components first and integrate last
Make 1.3 the scenario contract, 1.4 the injector, 1.5 graph capture, 1.6 resources and 1.7 the first complete runner. Add a docs check that no earlier chunk calls the runner. Cost: 5 hours of gate and plan edits. It gives up early end-to-end feedback before 1.7. It fails if the focused tests mock away the component boundary.

### Option B: move the runner and all of its dependencies into 1.3
Build scenario parsing, injection, graph capture, resource sampling and record publishing in one chunk. Cost: 14 to 20 hours in one sitting. It gives up the chunk-size promise. It fails when that oversized chunk slips and blocks the week.

### Option C: keep the order and allow chunks to depend on leftovers
Document that 1.3 through 1.7 only run as a week. Cost: under 1 hour. It gives up standalone chunk gates. It fails when a resumed loop runs one chunk against stale `latest` artifacts.

### Chosen: A
This restores real dependency order without changing scope. The tradeoff is that live composition waits until the last W1 chunk.
**Fallback if it does not land:** B, but split its implementation across commits while keeping one acceptance gate.

## Problem 5: outside ROS processes could carry undeclared swarm traffic
**Where it bites** the tx/rx seam and the 25 percent communications row
**Found by** this round

### Option A: exact per-process endpoint allowlists
Give the runner, observer, radio and GCS only the topics and types each needs. Reject blank endpoint types, unknown helpers, namespace lookalikes and any `SwarmPacket` outside tx/rx. Cost: 7 hours including graph fixtures. It gives up wildcard convenience. It fails when a legitimate new endpoint lands without an allowlist update.

### Option B: deny known bad topics
Keep a list of simulator truth and shared swarm topics to reject. Cost: 2 hours. It gives up coverage for new side channels. It fails open on the first unlisted topic.

### Option C: isolate processes with ROS domains
Put the link layer and swarm nodes in separate domains with a bridge. Cost: 18 to 24 hours and more launch complexity. It gives up time from routing work. It fails if the bridge becomes an unmeasured second radio.

### Chosen: A
The graph is small enough for an allowlist. The maintenance cost is visible and safer than an open-ended deny list.
**Fallback if it does not land:** C only if the graph grows beyond a reviewable allowlist.

## Problem 6: queue success could be claimed with counts and split backlogs
**Where it bites** W4.4 and store-and-forward marks
**Found by** this round

### Option A: keep an id ledger and name one custodian
Record every observation id with creation and delivery times, then require the lowest-id disconnected member to hold the whole outage set. Recompute totals and drain clocks. Cost: 8 hours across schema, evaluator and fixtures. It gives up a distributed backlog design for this scenario. It fails if the custodian dies, which this frozen scenario does not inject.

### Option B: prove a distributed queue explicitly
Record custody transfers and per-node queue ledgers, then prove the union has no gap or duplicate. Cost: 18 to 24 hours and a larger evaluator. It gives up time from the relay and safety rows. It fails when clocks or transfer logs disagree across nodes.

### Option C: keep totals only
Assert generated, delivered and peak depth numbers. Cost: 2 hours. It gives up packet identity and the claim that one 512-entry queue is enough. It fails green on 450 wrong packets or two 225-entry queues.

### Chosen: A
One custodian matches the queue sizing argument and is cheap to explain. The system gives up distributed custody in this proof.
**Fallback if it does not land:** B if tests show the chosen custodian cannot receive the whole disconnected component within the fixed scenario.

## Problem 7: validated evidence and delivered evidence could be different bytes
**Where it bites** proposal claims and final delivery
**Found by** this round

### Option A: hash after validation and compare at delivery
Store the hash of each fully validated run, then compare the attachment or archived copy byte for byte. Cost: 4 hours and one split-root fixture. It gives up name-only flexibility. It fails if delivery happens through an unrecorded route.

### Option B: validate only files inside `submission/`
Copy every run first and point the evaluator there. Cost: 3 hours plus changed week workflows. It gives up validation in place and risks stale copies during development. It fails when the copy step is forgotten before a gate.

### Option C: put evidence inside the source archive
Archive all run records with source and validate archive entries. Cost: 5 hours and a larger archive. It gives up separation between source and generated evidence. It fails when large evidence pushes delivery over the recorded limit.

### Chosen: A
It keeps the existing run workflow while binding the final bytes. The extra hash map is small.
**Fallback if it does not land:** B if attachment routing becomes too varied to bind reliably.

## Problem 8: package containers and link entries trusted their shape
**Where it bites** chunk 4.8 and the final email
**Found by** this round

### Option A: reject malformed containers, paths and duplicates
Check top-level JSON types, archive parser errors, repeated archive paths, duplicate delivery names and local hashable copies for linked files. Cost: 6 hours with tamper fixtures. It gives up accepting loosely written manifests. It fails if a new archive format is added without the same checks.

### Option B: define schemas for every manifest
Add JSON Schemas for source, evidence, attachment, install and send receipts. Cost: 12 to 16 hours plus semantic checks still needed. It gives up speed of small direct checks. It fails when schema validation and later code read different interpretations.

### Option C: accept only output produced in one packaging process
Replace the separate files with one signed package command and opaque receipt. Cost: 16 hours and key handling. It gives up inspectable handoff files. It fails if the signing key or package command is unavailable near the deadline.

### Chosen: A
Direct checks cover the actual attack surface with no new tool. The price is some repeated type checking.
**Fallback if it does not land:** B if the manifest set grows again.

## Problem 9: the rehearsed install guide and emailed guide were separate artifacts
**Where it bites** installation instructions and judge setup
**Found by** round 7 finding 2, rechecked this round

### Option A: make root `INSTALL.md` canonical
Archive it from the frozen commit, copy it to `submission/` during freeze and compare the two byte for byte. Cost: 4 hours. It gives up independent formatting for the email copy. It fails if a later tool rewrites the copied file after the freeze.

### Option B: keep the guide only in `submission/`
Add that path to the source archive and run it there. Cost: 3 hours. It gives up a normal root entry point for a judge opening the source. It fails if submission artifacts are excluded from a source-only handoff.

### Option C: generate both copies from a template
Render root and submission guides during packaging. Cost: 7 hours and another generator. It gives up simple byte provenance from git. It fails when the template is committed but generated copies are stale.

### Chosen: A
One committed file is easy to audit and easy for a judge to find. Any post-freeze edit rightly invalidates the package.
**Fallback if it does not land:** B if organiser instructions require every deliverable to live only under `submission/`.

## Problem 10: the fresh install could depend on the host or an old overlay
**Where it bites** W3 rehearsal, chunk 4.8 and judge reproducibility
**Found by** round 7 finding 2 and this round

### Option A: isolate paths and pass them through every shell
Use a disposable target home, build, install and runs directory. Do not load ROS on the launcher host; source base and dependency workspaces inside the target. Cost: 8 hours. It gives up reuse of host setup stamps. It fails if the disposable distro cannot access the archive path.

### Option B: require a named disposable WSL distro only
Remove the local-prefix fallback. Cost: 4 hours of code and setup time for every rehearsal. It gives up use on Linux CI or a machine without imported WSL. It fails when distro creation is blocked close to submission.

### Option C: use a container image
Build and test the archive inside a pinned Ubuntu image. Cost: 20 to 30 hours because PX4, Gazebo and WSL GPU details need work. It gives up parity with the supported WSL route. It fails on nested virtualisation or missing device support.

### Chosen: A
It supports the stronger distro run and an honestly labelled local fallback. The cost is careful environment handoff code.
**Fallback if it does not land:** B for the final receipt, while leaving the local prefix for non-binding rehearsal.

## Problem 11: the Markdown install runner changed shell meaning
**Where it bites** the only guide a judge runs
**Found by** this round

### Option A: extract blocks into one strict script
Preserve lines, continuations, heredocs, sourced state and `cd`; ignore console output and run with `set -euo pipefail`. Cost: 5 hours with fixtures. It gives up support for prose that mixes output into bash fences. It fails on a Markdown construct outside the small supported fence grammar.

### Option B: require a separate executable installer
Make `INSTALL.md` call one checked shell script and forbid other commands. Cost: 3 hours. It gives up proving the written manual steps themselves work. It fails when the prose and installer diverge.

### Option C: execute each displayed command separately
Keep the old line runner and document that state cannot carry. Cost: 1 hour. It gives up ordinary shell instructions using `cd`, exports, multiline commands or `source`. It fails on the current guide.

### Chosen: A
The guide remains normal Markdown and executes like a reader's shell. The parser stays intentionally narrow.
**Fallback if it does not land:** B if the guide needs syntax the extractor cannot preserve safely.

## Problem 12: the recording rehearsal kept too little proof
**Where it bites** W3.6 and the demo-video deliverable
**Found by** this round

### Option A: retain the complete run and graph
Copy the run record and graph into `submission/`, validate the full schema, bind both hashes and run the seam checker against them. Sanitize every receipt path. Cost: 7 hours plus video fixtures. It gives up a small receipt-only artifact. It fails if the recording tool cannot overlay the run id reliably.

### Option B: keep only the clip and a signed digest
Sign the clip hash and trust the signer for run context. Cost: 5 hours plus key custody. It gives up independently readable run and graph evidence. It fails if the signing environment is compromised or the key is lost.

### Option C: treat any decodable 60 second clip as rehearsal
Keep duration and decode checks only. Cost: 1 hour. It gives up proof that the clip shows a complete current scenario inside the seam. It fails green on an unrelated video.

### Chosen: A
The files already exist after the run, so retaining them is cheaper than inventing trust. Storage cost is minor.
**Fallback if it does not land:** B only for the clip identity, while still retaining the run and graph.

## Problem 13: disposable-prefix checks could be bypassed before recursive deletion
**Where it bites** `fresh_install.sh` and `rehearse_install.sh`
**Found by** this round

### Option A: resolve then match a narrow prefix
Use `realpath -m`, require a non-empty suffix under `/tmp/uavx-fresh-*` or `/tmp/uavx-rehearse-*`, and clear stale receipts before work. Cost: 3 hours. It gives up custom build locations. It fails if `realpath` is missing, in which case it refuses to delete.

### Option B: create every target with `mktemp -d`
Ignore caller paths and let the OS choose. Cost: 2 hours. It gives up predictable targets needed for keeping final evidence available. It fails when a second shell cannot find the generated location.

### Option C: never delete automatically
Require the operator to provide an empty directory. Cost: 2 hours and repeated manual cleanup. It gives up a provably fresh prefix. It fails when unnoticed old output remains.

### Chosen: A
The target stays inspectable and traversal is removed before the match. Refusing without `realpath` is an acceptable hard stop.
**Fallback if it does not land:** B for rehearsals, with the chosen directory written to the receipt.

## Problem 14: shell checks could print green after their scans failed
**Where it bites** every target-side script
**Found by** this round

### Option A: execute interpreter probes and check scan status
Choose Python by running an import, make failed scans fatal and reject literal `\n` tokens as well as CRLF and parse stderr. Cost: 4 hours. It gives up silent fallback when Python is broken. It fails if a valid shell line intentionally contains an unquoted token of that exact shape.

### Option B: run shell checks only inside Ubuntu
Remove Git Bash support and call WSL bash 5.1 for every check. Cost: 3 hours and a working WSL service. It gives up fast Windows-side checks. It fails when WSL is unavailable even though the source could still be inspected.

### Option C: use ShellCheck only
Install and run a standard analyser. Cost: 3 hours plus a pinned package. It gives up the byte-level CRLF check and the known bash 5.1 stderr quirk. It fails green on the machine-specific cases already observed.

### Chosen: A
It checks the exact failures this repo has hit on both sides. A missing interpreter now looks like a failure, which is honest.
**Fallback if it does not land:** B for the final gate and A as a development check.

## Problem 15: the live-spec receipt dirtied the tree before source freeze
**Where it bites** strict preflight immediately before chunk 4.8 freeze
**Found by** this round

### Option A: keep a committed seed and an ignored live receipt
Read the ignored current receipt first, fall back to the tracked seed on a new checkout and write freshness only to ignored runtime state. Cost: 3 hours. It gives up version history for each routine online check. It fails if ignored state is deleted while offline.

### Option B: commit every successful online check
Keep the receipt tracked and make the operator commit it before freezing. Cost: about 10 minutes per check plus noisy history. It gives up unattended chunk execution. It fails when the check runs after the final commit and dirties the tree again.

### Option C: put freshness in the source manifest
Have freeze perform the online check and record it only after archive creation. Cost: 5 hours. It gives up weekly offline fallback state. It fails if the network drops between archive and receipt creation.

### Chosen: A
Mutable liveness is runtime state. The committed seed still lets a checkout use the documented short offline fallback.
**Fallback if it does not land:** C for the final package, while weekly checks use a user cache.

## Problem 16: external tools could fail while their empty output was trusted
**Where it bites** proposal extraction, video duration and shell byte scans
**Found by** this round

### Option A: check return codes and finite outputs
Treat non-zero `pdftotext` and `ffprobe` exits as failures, reject NaN or infinity and require scan subprocesses to complete. Cost: 3 hours. It gives up treating a tool's empty output as a valid empty artifact. It fails if a tool warns on stderr while still producing sound output and the caller treats all stderr as fatal.

### Option B: inspect file formats in Python
Replace command-line tools with PDF and media libraries. Cost: 12 to 20 hours and large dependencies. It gives up parity with tools installed for the final package. It fails on codec or PDF features the libraries do not support.

### Option C: trust file extensions and sizes
Skip extraction and decoding when the file is non-empty. Cost: under 1 hour. It gives up nearly all proof of proposal and video usability. It fails green on corrupt files.

### Chosen: A
The tools already own format parsing. Reading their status is the missing part and costs little.
**Fallback if it does not land:** B for the one format a command-line tool cannot read consistently.

## Problem 17: several negative suites started from weak or leaking fixtures
**Where it bites** install, dry-run and submission checker confidence
**Found by** this round

### Option A: build one accepted baseline, clone, mutate one thing
Use the full example record and clean graph, copy the canonical guide, run fixtures in their temporary directory and add wrong-type cases. Cost: 8 hours. It gives up very small fixtures. It fails when the baseline depends on a tool missing from the test host.

### Option B: mock checker internals
Call helper functions with hand-built objects. Cost: 4 hours. It gives up process boundaries, file paths and real tool exits. It fails to catch integration mistakes.

### Option C: test only a real final package
Wait for W4 artifacts and manually tamper copies. Cost: 6 hours each run and no early feedback. It gives up repeatability before implementation exists. It fails when the final package arrives too late to fix the checker.

### Chosen: A
The baseline proves the oracle before any mutation counts. FFmpeg remains a real prerequisite, so the suite fails loudly if it cannot exercise video.
**Fallback if it does not land:** C for one tool-specific case, keeping all file and contract fixtures automated.

## Problem 18: active documents still named a fifth week
**Where it bites** chunk ownership and the final four-day schedule
**Found by** this round

### Option A: rename active references and ban `W5`
Use `4.8`, final package or submission tail in current docs and scripts. Keep historical reviews untouched. Cost: 2 hours. It gives up the old shorthand. It fails if a new active file is not part of the docs scan.

### Option B: restore a five-week plan
Move packaging back to W5 and change dates. Cost: more than the remaining calendar permits. It gives up the fixed four-week schedule. It fails because there is no fifth execution week.

### Option C: define W5 as an alias for 4.8
Keep both names and add a glossary. Cost: 1 hour. It gives up one-to-one chunk naming. It fails when an agent schedules W5 after W4.

### Chosen: A
The schedule has four weeks. One name per piece of work removes an avoidable guess.
**Fallback if it does not land:** C only in historical notes that cannot be rewritten, never in active instructions.

## Problem 19: invalid gate selectors failed for the wrong reason
**Where it bites** command-line recovery and unattended chunk dispatch
**Found by** this round

### Option A: validate the selector before loading the environment
Accept only weeks 1 to 4, `preflight`, the two list words and ids returned by `chunk_fn`, then source ROS. Cost: 1 hour with a process fixture. It gives up nothing from valid gates. It fails if a new chunk is added to dispatch but not to `chunk_fn`.

### Option B: keep late validation and improve the ROS error
Mention that the selector might also be wrong when environment setup fails. Cost: under 1 hour. It gives up an exact diagnosis. It fails because a bad id still depends on machine state.

### Option C: parse commands in a separate wrapper
Use a small Python CLI to validate ids before it invokes the shell gate. Cost: 4 hours and a second entry point. It gives up the single-script gate contract. It fails when callers bypass the wrapper.

### Chosen: A
The valid set already lives in `chunk_fn`. Checking it early is small and makes errors deterministic.
**Fallback if it does not land:** C if selector syntax grows beyond a simple shell case.
