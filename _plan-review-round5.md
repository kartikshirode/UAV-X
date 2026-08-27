# Plan review, round 5 (Codex)

Reviewed: `context.md`, `stage-1/plan.md`, `stage-1/decisions.md`, `.claude/weekly-loop.md`, every file under `stage-1/setup/`, `stage-1/architecture.md`, every current file under `scripts/`, `scenarios/run-record.schema.json`, `submission/human-preflight.schema.json`, `.claude/review-status.json`, and `_plan-review-round1.md` through `_plan-review-round4.md`, at HEAD `8a80c44bafad5a09faab7e9096f8d43c3115206b`.

## Still open from earlier rounds

- Round 4 finding 1 is still partly open. `gate_w3` now calls `check_dryruns.py`, but that checker accepts a self-written receipt and an unrelated video. The gate can still pass without either promised rehearsal having happened. See finding 6.

## Findings

### Finding 1, critical: link-loss handback cannot satisfy its own release and no-outage rules

**The plan says** that when `uav_2` returns, `uav_4` has an alternate path through it, `uav_3` is released, and no second outage occurs. The same section then says both paths cost three hops and route hysteresis keeps the route already installed through `uav_3`. A relay may only release after the component has held a route that does not pass through that relay.

**The problem** those statements cannot all be true. The installed route is `uav_4 -> uav_3 -> uav_1 -> gcs`. The recovered route is `uav_4 -> uav_2 -> uav_1 -> gcs`. Hop-count Dijkstra sees a tie, and the frozen hysteresis rule keeps the first route. The release predicate therefore never becomes true. If an implementation interprets the mere existence of the alternate as enough and moves `uav_3` anyway, it breaks the installed next hop before the alternate is selected. That creates the second outage which `outage_count_after_release==0` forbids. The [make-before-break definition in RFC 3753](https://www.rfc-editor.org/rfc/rfc3753.html) requires the new connection before the old one is removed. [OLSRv2](https://www.rfc-editor.org/info/rfc7181/) also warns against treating every link as equal-cost hop count without checking that the metric fits the job. Current products attack the same failure from different angles. [Elsight Halo](https://www.elsight.com/products/halo-for-commercial-use/) keeps paths active in parallel and uses soft handoffs. [Doodle Labs Sense](https://doodlelabs.com/news/sense-interference-avoidance-release/) monitors link health and moves to another channel or band. [Silvus StreamCaster](https://silvustechnologies.com/applications/unmanned-systems/) uses a self-forming mesh around moving unmanned systems. These are vendor descriptions, not independent performance proof, but each puts path readiness or link health ahead of a disconnect.

**Fix** freeze one make-before-break policy before W4. Three workable choices:

1. Add `PREPARE_RELEASE`, install the recovered next hop, send observations through it for one stability window, wait for a GCS acknowledgement naming that path, then issue `RELEASE` and move `uav_3`.
2. Add a temporary role cost or willingness metric. Once `uav_2` is healthy, the path through it must beat the relay path for two computations. Keep `uav_3` still until the routing generation and a delivered packet both prove the change.
3. Keep primary and backup next hops. Duplicate a short sequence across both during handback, deduplicate at the GCS, then remove the old route after the backup has delivered the complete sequence.

Whichever one is chosen, add `handback_path`, `handback_confirmed_at`, `release_at` and a unique-packet gap count to the run record. The gate should prove the new path carried data before the old relay moved. Do not use geometry to make one equal-hop route accidentally win, because the same bug returns with the next topology.

### Finding 2, critical: the submission fixture suite has never seen a valid package

**The plan says** `test_submission_fixtures.py` proves the W5 checker accepts an untouched package and rejects each tampered one for the right reason.

**The problem** its baseline is incomplete by construction. `REQUIRED_RUNS` contains eight scenarios, including `link_loss`, while `build_package()` creates only seven run records. It also creates no proposal or video. The clean case calls any nonzero return code success, and the current test prints `ok untouched package rc=1`. There is no assertion that a complete unsent package reaches the documented exit 2, or that adding a valid sent receipt reaches exit 0. An always-broken W5 checker can survive this suite. Negative cases are being mutated from a package that was never known to work.

**Fix** make the oracle positive first:

1. Build all eight run records plus small valid PDF, video and licence fixtures, stub only the external scenario validator at its process boundary, and require exact exit 2 before any mutation.
2. Add a sent receipt bound to that baseline's real attachment-manifest hash and require exact exit 0.
3. Clone the passing baseline for every negative case, require exact exit 1 plus the intended diagnostic, and fail if any unrelated baseline diagnostic appears.

Keep the current byte-tamper cases. They are useful once the suite proves both good states as well as bad ones.

### Finding 3, critical: W5 can record a send that omits the source and demo, and the promised licence files are outside the archive

**The plan says** five deliverables go in one email. The attachment manifest lists the exact sent files, and the sent receipt binds to it. It also says `LICENSE` and `THIRD-PARTY.md` sit inside the archive beside the code.

**The problem** `check_submission.py` requires only `proposal.pdf` and `INSTALL.md` in the attachment manifest. The source archive and demo must exist on disk, but neither has to be sent. A receipt for an email carrying two of the deliverables reaches the submitted state. Separately, `freeze_source.sh` archives only `uavx_ws`, `scenarios`, `scripts` and `stage-1`, so root `LICENSE` and `THIRD-PARTY.md` cannot be included as planned. The licence check accepts an empty file at any depth whose name has the right suffix. That proves neither ownership terms nor third-party notices. PX4's own [BSD 3-Clause licence](https://github.com/PX4/PX4-Autopilot/blob/main/LICENSE) requires its notice to be retained in source distributions, while the [ROS 2 developer guide](https://github.com/ros2/ros2_documentation/blob/rolling/source/The-ROS2-Project/Contributing/Developer-Guide.rst) says packages can carry Apache 2.0 or another permissive licence. One guessed label for the whole stack is not enough.

**Fix** close all delivery routes, not just email attachments:

1. Require the manifest to contain `proposal.pdf`, `INSTALL.md`, the archive named by `source-manifest.json` and exactly one checked demo file.
2. If a file is sent by a shared link, model it as a delivery item with route, final URL, byte size, SHA-256 and an access-test receipt. It must not disappear from the manifest because it is not attached.
3. Add the exact root licence files to `ARCHIVE_PATHS`, require them at `uavx-source/LICENSE` and `uavx-source/THIRD-PARTY.md`, reject empty content, and require the third-party file to inventory each locked dependency, version, upstream URL and licence identifier. Check the notices against the pinned source, not against memory.

### Finding 4, significant: the new slot-clearance rule is neither exercised nor safe for a moving silent vehicle

**The plan says** a relay slot is raised until it stays 15 m from every airborne vehicle, with 5 m reserved for stale position data. It describes `link_loss` as the scenario which proves this matters.

**The problem** no accepted scenario makes the correction run. In `mission_integrated`, the vehicle 6.8 m from the raw slot is dead before the slot is computed. In `link_loss`, the live vehicle is already 39.1 m from the raw slot, so no raise occurs. Only `check_rejected()` applies the correction to the 6.8 m counterexample. That proves the checker can do the arithmetic; it does not prove the future role implementation calls the rule. The 5 m stale-position allowance is also not derived. At the frozen 10 m/s cruise speed, a vehicle can move 30 m during `neighbour_timeout` alone. Election, convergence and the relay flight make the last known pose older still unless the slot is updated. Research on [UAV separation under message delay, loss and position uncertainty](https://rfly.buaa.edu.cn/pdfs/2022/How_far_two_UAVs_should_be_subject_to_communication_uncertainties.pdf) treats that uncertainty as part of the safety radius. Relay-placement work also adds positions when needed to keep the relay trajectory feasible, rather than checking only the final radio balance point; see [Yanmaz, Positioning aerial relays to maintain connectivity during drone team missions](https://www.sciencedirect.com/science/article/pii/S1570870522000178).

**Fix** choose one safety model and force it in a run:

1. Add a moving blackout scenario where the raw slot violates clearance, require the commanded raised slot and keep the current uncorrected negative control.
2. Replace the flat 5 m margin with a time-expanded flight tube using last pose, last velocity, bounded acceleration, pose error and age. Test the whole mover trajectory against that tube, not just the destination.
3. Reserve relay altitude cells or corridors that cannot intersect another vehicle's frozen mission corridor. If no cell clears both the flight tubes and radio bounds, emit `RELAY_INFEASIBLE`.

The station-keeping `link_loss` run may stay as the fault comparison, but it cannot be the only evidence for a rule intended to protect moving vehicles.

### Finding 5, significant: store-and-forward has capacity but no delivery semantics or drain budget

**The plan says** 512 packets per node covers a 45 second outage, and observations generated in the outage are delivered once the route returns.

**The problem** the arithmetic only sizes storage. Two survey origins at 5 Hz generate 450 packets in 45 seconds, leaving 62 slots, but nothing fixes packet size, forwarding service rate, retry policy, acknowledgement, expiry or deduplication. There is no packet identity in the run schema. A relay can count duplicate deliveries, empty its queue after a failed send, or still hold a backlog when the run ends while the metrics claim success. The implementing agent has to invent what `delivered once` means. [Bundle Protocol Version 7](https://www.rfc-editor.org/rfc/rfc9171.html) is much bigger than this project needs, but its source and timestamp identity, lifetime, hop count, retention state and delivery reports show the missing minimum. ROS 2's [QoS settings](https://docs.ros.org/en/humble/Concepts/Intermediate/About-Quality-of-Service-Settings.html) provide deadline, lifespan and liveliness controls, but the default volatile history does not create application-level outage recovery by itself.

**Fix** freeze a small protocol, not just a queue length. At least three valid shapes exist:

1. Give each observation `(origin_id, sequence, created_at, expires_at)`, retain it until an end-to-end GCS acknowledgement, retry after route recovery and deduplicate at the GCS.
2. Use a BPv7-inspired envelope with identity, lifetime, hop limit and delivery status, without implementing the rest of BPv7.
3. Duplicate backlog traffic over primary and backup paths during recovery, deduplicate by observation ID, and count extra transmissions separately.

For any choice, state payload bytes and forwarding rate, derive a worst-case backlog drain time, and run a full 45 second outage. Gate set equality between generated and uniquely delivered observation IDs, zero expired or evicted records, duplicate count, peak queue depth and time to drain. Current commercial UAV links also expose traffic priority as a first-class control; [Elsight's product description](https://www.elsight.com/products/halo-for-commercial-use/) is one example. Control and HELLO traffic should not wait behind 450 survey observations.

### Finding 6, significant: the W3 rehearsal gate still accepts written claims instead of rehearsals

**The plan says** W3 cannot go green until a fresh install and a 60 second recording have happened against the current source.

**The problem** the install half accepts JSON with `result: pass`, a copied source hash and any five strings in `steps_run`. It does not run the installer, check a target, read exit codes or verify a smoke run. The recording half accepts any decodable local clip of at least 55 seconds. Its receipt does not hash the clip and the clip need not show Gazebo, four vehicles, the current scenario or the current source. Both halves can be satisfied by writing files. This is the same dangerous direction as round 4 finding 1, now one layer deeper.

**Fix** make the gate own the evidence:

1. Have a rehearsal wrapper create a clean target, invoke all setup steps itself, capture their return codes and write the receipt atomically only after `verify.sh` and a smoke run pass.
2. Bind that receipt to target identity, command transcript hash, installed source hash, stack versions, start and end times, and smoke run ID. Keep the transcript as an artifact the checker rehashes.
3. Have the recording wrapper launch a named scenario and capture command, then bind clip SHA-256, graph snapshot and run ID in one receipt. Put the run ID and source hash on screen or in a short slate so an unrelated clip fails.

If a genuinely clean install is too expensive for W3, rename it to a rebuild rehearsal and keep the real archive install in W5. Do not call a receipt-only check a fresh install.

### Finding 7, significant: the seam graph accepts every expected node with no endpoints

**The plan says** the graph pass proves each swarm process respects the tx/rx seam, with resolved remaps and exact process names.

**The problem** process presence is checked, but required endpoints are not. Calling `seam_graph.check()` with all 13 expected `mission_integrated` node names and four empty endpoint lists returns no violations. `read_live()` also ignores the return code and stderr from every `ros2 node info` call, so discovery errors become empty endpoint lists and pass. The three outside processes are optional because only swarm processes are in `expected`. A hand-written or partial snapshot can therefore report a clean seam while proving no communication graph at all.

**Fix** make graph completeness part of the contract:

1. Put mandatory publisher and subscriber templates per process in `seam_manifests.json`, including exact tx/rx ownership and the minimum PX4 endpoints each mission process needs.
2. Reject nonzero `ros2 node info`, unparsed sections and an expected node with no meaningful endpoints. Record capture timestamp, scenario run ID and every command status in the snapshot, then bind its hash into the run record.
3. Require the exact outside nodes for a running scenario and add fixtures for all-empty endpoints, one missing mandatory endpoint, a missing outside node and a failed `node info`. Add one real positive graph after W3 and again after W4.

### Finding 8, significant: the live-spec check can miss cancellation, a replaced PDF and official clarifications

**The plan says** the weekly preflight and W5 compare every published obligation with the 26 August capture.

**The problem** the checker compares twelve API fields, not the published artifact set. A change to a non-listed field such as `live` is printed as a note and still exits 0, so `live: false` can pass. `probStatement` compares only the URL string. Replacing the PDF at the same URL is invisible. Weekly preflight uses `--allow-offline`, which converts no check into success, and neither API pass reads the WhatsApp or email channels where the rules say changes will be communicated. The live run on 27 August did reach the API and found registrations had moved again, from the saved 17 to 36; that proves the request works, not that these blind spots are closed.

**Fix** use three layers:

1. Treat `live != true`, unknown nonvolatile field changes, duplicate records and missing fields as blocking. Give offline a distinct nonzero result and require at least one successful online receipt in each week rather than accepting an offline week as checked.
2. Download the linked problem statement, follow redirects, validate MIME type and hash its bytes. Save the API response, final URL, PDF hash and UTC time together.
3. Add a dated human-channel receipt for WhatsApp and organiser email, including unresolved answers and a hash or exported snapshot. W5 should reject an unanswered change rather than merely proving the API stayed still.

### Finding 9, significant: the aviation compliance gate checks six letters, not compliance

**The plan says** the proposal carries a section on Indian BVLOS regulation because the competition rules require compliance with Indian aviation law and safety rules.

**The problem** W5 only searches extracted PDF text for `regulat`. A sentence saying the system is unregulated passes. It does not require an official source, distinguish simulation from physical operation, identify the authority or state which assumptions would need approval before later field work. The legal baseline is not one frozen 2021 document either. The Ministry publishes the [Drone Rules, 2021](https://www.civilaviation.gov.in/index.php/ministry-documents/rules/drones-rules-2021-dated-25-august-2021), plus [2022](https://www.civilaviation.gov.in/ministry-documents/rules/drone-amendment-rules-2022-dated-11-feb-2022) and [2023 amendments](https://www.civilaviation.gov.in/ministry-documents/rules/drone-amendment-rules-2023). Its archive also shows BVLOS experimental operations handled through named conditional exemptions, including the [NAL order](https://www.civilaviation.gov.in/sites/default/files/migration/Conditional_exemption_to_NAL_for_BVLOS_drone_operations_13_Sep_2021.pdf). This review is not deciding what approval a future flight needs. It is pointing out that the package gate cannot tell legal analysis from one stray word.

**Fix** make the proposal and check match the actual Stage 1 claim:

1. Add a short compliance matrix with rule or official source, present simulation applicability, future physical-flight obligation, project control and owner. State plainly that Stage 1 and Stage 2 are simulation-only.
2. Require exact section headings and citations to the current principal rules and amendments. Machine-check presence and link targets, then use a dated human sign-off for correctness because a substring checker cannot give legal advice.
3. Add a Stage 3 go/no-go item: recheck current DGCA and Digital Sky requirements, airspace, registration, pilot, aircraft and BVLOS permissions before any physical flight. No field test starts from a Stage 1 paragraph.

## Verdict

NOT READY. The new link-loss scenario covers the missing half of the organiser's fault, and the full-graph geometry checker is a real improvement. Its handback state is internally impossible, though, and the new safety correction is only exercised in a rejected design. Three acceptance paths still go green on written or empty evidence: the W3 rehearsal receipts, the seam graph and the submission fixture baseline. W5 can also record a send without two core deliverables. Fix findings 1 through 4 before W4 implementation, and findings 2, 3 and 6 before trusting the weekly gates. The protocol, live-spec and compliance items are smaller to implement now than to discover during packaging.

## For the next round

First show a make-before-break handback trace where the recovered path delivers a named observation before `uav_3` moves. Add a moving-blackout geometry and runtime case which actually raises a slot. Then run one complete fixture package to exit 2 and the same package with a sent receipt to exit 0, followed by the existing tamper cases. Prove an all-empty seam snapshot fails. Finally, freeze observation identity and backlog drain semantics, hash the official PDF, and show the archive plus delivery manifest contain every promised source, video and licence artifact.

Findings: 9
