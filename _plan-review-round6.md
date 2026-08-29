# Plan review, round 6 (Codex)

Reviewed: `context.md`, `stage-1/plan.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `stage-1/setup/`, `stage-1/architecture.md`, every current file under `scripts/`, `scenarios/run-record.schema.json`, `submission/human-preflight.schema.json`, `.claude/review-status.json`, and `_plan-review-round1.md` through `_plan-review-round5.md`, at HEAD `612b46c6b320095c0ae6b6c1d29b1865837203db`. The Techfest API was fetched again on 29 August 2026: `uav-x` is live, the obligations are unchanged and registration count is 45.

## Still open from earlier rounds

- Round 5 finding 3 is partly open. The archive and licence files are now required, but the delivery check still does not require the demo file it decoded. See finding 3.
- Round 5 finding 5 is still open at the acceptance layer. The packet protocol is written down, but its new evidence is optional and the gate does not check the stated set equality or drain bound. See finding 5.
- Round 5 finding 6 is still open. The two rehearsal wrappers exist, but `gate_w3` never invokes them and the checker still accepts self-consistent files. See finding 4.
- Round 5 finding 7 is partly open. Empty endpoints fail now, but the snapshot is not tied to the run and command evidence is optional. See finding 6.

## Findings

### Finding 1, critical: W5 still accepts a fresh install that never happened

**The plan says** the binding W5 install runs against the frozen source archive on a fresh target. `fresh-install-receipt.json` is meant to prove that exact archive installed and ran.

**The problem** `check_submission.py` compares only `archive_sha256`, `commit_sha` and the string `result: pass`. There is no W5 archive-install wrapper in the repository, no target identity, no transcript and no smoke-run result. The positive fixture writes those three values directly and expects the package to pass. It now proves the false green: a JSON file is enough to certify the one install that matters. The W3 wrapper does not help because it rebuilds the working tree on the already-provisioned distro, before the W4 code and before the archive exists.

**Fix** make `gate_w5` run the archive install itself after `freeze_source.sh`. Unpack into a disposable WSL distribution or another named clean target, run the submitted `INSTALL.md` path, build, run `verify.sh` and finish with a four-vehicle smoke run. Microsoft documents separate imported WSL distributions, which gives this test a clean target without touching the working distro: [Install WSL](https://learn.microsoft.com/en-us/windows/wsl/install). Write the receipt atomically only after those commands exit 0. Bind it to the target identity, archive hash, commit, full transcript hash, installed version set and smoke run ID. The fixture can stub this executor at its process boundary, but a hand-written three-field receipt must be a negative case.

### Finding 2, critical: the media-tool setup fix never reaches the machine that runs the gates

**The plan says** `poppler-utils` and `ffmpeg` were added to setup because W3 and W5 need them.

**The problem** the real `Ubuntu-22.04` target currently reports `ffmpeg=MISSING`, `ffprobe=MISSING`, `pdftotext=MISSING` and `pdfinfo=MISSING`. Its `~/.uavx-setup/base` stamp is present. `01-base.sh` checks that stamp before the new install commands and exits 0, so `setup-all.sh` will skip the fix forever on this machine. `verify.sh` does not check any of the four tools and exits 0 anyway. The required submission fixture then exits 1 before reaching its positive oracle because it cannot create the video. This is the same dangerous direction as the old Gazebo check: the environment gate is green while a required artifact cannot be checked.

**Fix** move these packages into a new numbered setup step with its own stamp, such as `06-submission-tools.sh`, and call it from `setup-all.sh`. A versioned `base-v2` migration is also valid, but reusing `base` is not. Add `command -v pdftotext`, `ffmpeg` and `ffprobe` to `verify.sh`; `pdfinfo` too if any checker keeps using it. Run the migration on the actual distro, then require `test_submission_fixtures.py` to reach exit 0 there.

### Finding 3, critical: the sent demo can differ from the video the checker decoded

**The plan says** the delivery manifest contains exactly one checked demo video.

**The problem** the checker decodes the first local `demo.mp4` or `demo.mkv`, but delivery only asks for one listed name beginning with `demo.`. A valid local `demo.mp4` can be decoded while the manifest sends `demo.txt`. If both video extensions exist, the checker can decode the good MP4 while the manifest sends a corrupt MKV. Both cases reach the sent state with no checked video among the delivered bytes. Round 5 finding 3 was about exactly this class of omission.

**Fix** require the exact `video.name` selected and decoded to appear once in the union of attachment and link items. Reject any other `demo.*` file in the submission directory or delivery manifest. Better still, collect all supported demo candidates first, require exactly one on disk, decode that file and require the same path, size and SHA-256 in delivery. Add fixtures for `demo.txt`, an unlisted valid MP4 and a good MP4 beside a corrupt delivered MKV.

### Finding 4, significant: the W3 gate still checks rehearsal claims instead of running the rehearsals

**The plan says** W3 cannot pass until `rehearse_install.sh` and `rehearse_recording.sh` have done their work.

**The problem** `gate_w3` calls only `check_dryruns.py`. It never invokes either wrapper. The install half accepts a transcript containing five `[exit 0]` lines, a copied source hash and a receipt which says `kind: rebuild-rehearsal`. It does not check the named target still exists or that the five labels are the wrapper's exact commands. The recording half checks a clip hash and two non-empty strings, but never opens the named run record or compares its scenario and source. The wrapper says the run ID is burned into the frame, yet it learns the ID only after `run_scenario.sh` returns and passes no ID or overlay instruction into the recording command. A typed receipt and an unrelated 60 second clip still satisfy the gate once ffmpeg is installed.

**Fix** have `gate_w3` remove the two stale receipt paths, invoke both wrappers and only then run the checker. Generate the run ID before capture, pass it into the scenario runner and video overlay, then bind the clip, run record and graph snapshot in one receipt. The checker should open that run record and compare run ID, scenario, source hash, duration and graph hash. Add negative fixtures for a fabricated transcript, an absent target, a receipt naming no real run and a clip copied from another run.

### Finding 5, significant: the new run evidence is optional and split across incompatible shapes

**The plan says** the run record proves generated and uniquely delivered observation ID sets are equal, with zero expiry or eviction and backlog drained within 2.25 seconds. It also says the handback and relay-slot evidence is in the record.

**The problem** none of `observations`, `handback` or `relay_slot` is in the schema's top-level `required` list. The passing submission fixture omits all three. Inside `observations` there are counts and an optional `missing_ids`, but no generated and delivered ID sets from which equality can be recomputed. The architecture names flat fields such as `observations_generated`; the schema defines a nested object; `gate.sh` asks for older flat metrics such as `observations_evicted` and `observations_undelivered`. It never gates duplicates, expiry, peak depth or the 2.25 second drain. No accepted run holds the route down for the full 45 seconds used to size the queue. The protocol is now clear, but its evidence can still be absent while W4 and W5 pass. [RFC 9171](https://www.rfc-editor.org/rfc/rfc9171.html) is useful here because its source plus creation sequence identity and delivery reports separate unique data from transmission counts.

**Fix** choose one record shape and use it in architecture, schema, evaluator, gate and fixtures. Require the relevant blocks by scenario in `uavx_eval.check`; if this is expressed with JSON Schema conditionals, extend `jsonschema_mini.py` because it currently ignores `if` and `then`. The official conditional form is documented by [JSON Schema](https://json-schema.org/understanding-json-schema/reference/conditionals). Store generated and GCS-delivered IDs, or a trusted event log from which the evaluator computes both sets. Gate equality, unexpected IDs, duplicates, expired, evicted, peak depth, `backlog_drain_s<=2.25` and control-queue delay. Add a frozen 45 second outage case instead of testing only the current 28.0 and 32.5 second recoveries.

### Finding 6, significant: a stale seam graph can still certify a new run

**The plan says** every snapshot records its scenario, run ID, source hash and each `ros2 node info` return code, which ties the graph pass to the scenario that just ran.

**The problem** `uavx_invalidate_latest` deletes only `latest.jsonl`, not `latest-graph.json`. `read_snapshot` checks that four metadata strings are non-empty, then throws them away. It never compares `meta.scenario` with `--scenario`, never compares run ID or source hash with `latest.jsonl`, and no graph hash is stored in the run record. `commands` is optional, an empty object passes in both clean fixtures, and even a command entry with no return code is accepted. A runner that produces a new metric record but misses graph capture can therefore reuse a stale graph. Editing its four free-form strings is enough to relabel it.

**Fix** invalidate both latest files before launch and publish both by atomic rename. Make the seam command take the expected run record, then compare scenario, run ID, source hash and capture time. Require one successful `node list` command plus one `node info` result with `returncode: 0` for every captured node. Hash the snapshot into the run record and recheck it in W5. Add fixtures for missing commands, incomplete command coverage, wrong scenario, wrong run ID, wrong source, stale time and a snapshot left over when capture never ran.

### Finding 7, significant: no node owns the handback after the disconnected component reconnects

**The plan says** the lowest member of the disconnected component is coordinator. Later, "the coordinator" observes a non-relay route, sends `PREPARE_RELEASE` and finally releases the relay.

**The problem** the coordinator rule ends when the component is no longer disconnected. After `uav_3` restores contact, the design never says whether its recovery epoch keeps an owner, the coordinator is recomputed over the merged graph, or the GCS takes over. There is a second ambiguity if `uav_3` remains owner: it is the relay, so every route from itself contains the relay as its source. The alternate route belongs to staying member `uav_4`, not to the coordinator. An agent can implement three reasonable readings and only one will make the frozen handback fire. The make-before-break order itself is right; [RFC 3753](https://www.rfc-editor.org/rfc/rfc3753.html) defines that as making the new connection before breaking the old one. The missing part is transaction ownership.

**Fix** keep an explicit `epoch_owner` from election through release, even after graph components merge. State that this owner evaluates the cheapest route for each staying member, excluding the relay as an intermediate node, and that `uav_4` must hold the named non-relay path for two computations. Define who renews the relay lease during this state and how ownership transfers if the owner dies. Record `epoch`, `epoch_owner`, `staying_member`, prepared route, confirmed observation ID and release sender, then assert that exact trace in `link_loss`.

### Finding 8, moderate: the documented offline preflight path always fails

**The plan says** W1 through W4 may continue offline when a genuine online check is less than seven days old. W5 must be online.

**The problem** `check_competition_spec.py --allow-offline` returns 3 for that recent-offline state. `gate_preflight` runs it with `|| gdie`, so every non-zero code, including 3, stops the week. The fallback added for the known WSL DNS drops is unreachable. This is not a safety hole, but it is a direct contradiction in the only gate contract and it can halt an unattended week for a transient network fault.

**Fix** capture the return code in preflight and accept 0 or 3 for weeks 1 through 4, while still rejecting 1 and 2. Pass an explicit strict mode in W5 and accept only 0 there. Add a fixture checker that returns each documented code and assert the gate's four decisions.

### Finding 9, moderate: the relay surcharge is not a pure tie-break once a path has two relays

**The plan says** `0.5` is below one hop, so it breaks equal-hop ties and can never make routing choose a longer path.

**The problem** the claim is true only when a path contains at most one temporary relay. A three-hop path with two temporary relays costs `3 + 2(0.5) = 4`, tying a four-hop path with none. With more relays it loses. `check_geometry.py` proves the one-relay frozen handback, then prints the broader claim from only `relay_surcharge < 1`. Stage 1 currently uses one relay, so this does not break its seven scenarios. It does make the frozen routing rule and proposal wrong as soon as a later disturbance creates a chain of relay roles.

**Fix** use a lexicographic route key `(hop_count, temporary_relay_count)` instead of a scalar surcharge. Dijkstra can compare that pair directly: fewer hops always wins, and relay count breaks only equal-hop ties. Keep the current path enumeration, then add a synthetic two-relay topology which proves a genuinely shorter path cannot lose.

## Verdict

NOT READY. The topology, vertical safety band and positive seam oracle are much stronger, and the live Techfest record still agrees with the capture. The acceptance path still has three submission-level false greens, though. W5 can certify an archive it never installed and a demo it never decoded, while the real WSL is missing the tools needed to run the positive submission suite and its verifier reports success. The observation, seam and handback gaps can cost the communication and recovery marks even after those release blockers are fixed. Geometry, documents and all 29 seam fixtures exit 0; the submission fixture exits 1 before its oracle because ffmpeg is absent on both the target WSL and Windows.

## For the next round

First show the setup migration running on the actual WSL, `verify.sh` checking the media commands and the complete submission fixture reaching both exit 2 and exit 0. Then make W3 and W5 invoke their evidence-producing wrappers rather than accepting receipts. Add negative fixtures for a wrong delivered demo and stale seam metadata. Finally, make observation and handback blocks mandatory for their scenarios, run the 45 second queue case and show a trace naming the handback epoch owner from prepare through release.

Findings: 9
