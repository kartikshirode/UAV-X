# Human preflight

**The loop must not start until every box below has a dated receipt in `submission/human-preflight.json`.**

Round 3 finding 4: these were filed as "open items, not blocking". They are not backlog. An unregistered or ineligible entrant has no valid submission however good the simulation is, and eligibility can disqualify the entry at any stage, including after Stage 1 results. Building for four weeks and then discovering the entry was never valid is the worst outcome available, and it is entirely preventable this week.

None of these can be done by an agent. All of them need you.

## 1. Registration

Register on techfest.org, Competitions, PUSHPAK Grand Challenge, UAV-X.

This is the competition's entry mechanism. There is no submission portal, so registration is the only thing that connects your email to an entry.

Record: the date, the registered email address, and **the competition id Techfest issues**, which looks like `UAVX-` followed by 12 upper case hex characters.

The id is the part that matters. A date and an address are both things a person types, and either can be typed by somebody who never registered; the schema accepted that for six rounds. An issued id cannot be guessed and the organisers can be asked to confirm it, so it is the only line in this section that is evidence rather than a claim.

## 2. Eligibility

From the official rules, stated twice:

> Project staff, research staff, consultants, interns, or other personnel directly engaged with the PUSHPAK Project, Drone Centre, or the organizing/host institutions are not eligible, whether participating individually or as part of a team.

The conservative declaration names IIT Bombay, IISER Bhopal and VJTI Mumbai. Solo entry, so this is one check rather than five.

Record: an explicit declaration that you hold no such attachment.

## 3. Clarification channel

Join the UAV-X WhatsApp group: https://chat.whatsapp.com/EdOZigIfR4s0LvBl4N49XB

Organiser clarifications land there before anywhere else. If the video format or the source delivery method is answered for someone else, it is answered for you.

Record: the date joined, and the date you last read it. The final package check refuses a channel nobody has looked at in a fortnight. The rules say changes are communicated through official channels, and an API diff does not read a chat group.

## 4. The organiser questions

Send [organiser-email.md](organiser-email.md). Two of its answers change work that starts now:

- **Source delivery.** Repository link or email attachment? The plan currently does both, which is a hedge rather than compliance.
- **Video limit.** No published limit, so the 180 s cap in `check_submission.py` is our invention. If theirs is shorter, the edit changes.

Record: the date sent, the date you last checked for a reply, and either the answers or the fallback being assumed.

## 5. Delivery method, decided

Round 3 is right that finding out during the submission tail that the intended attachment cannot be delivered risks the submission itself. A three minute video plus a source archive will exceed most mail servers.

Decide now, and record it:

- **Total attachment budget** your mail provider allows
- **Delivery route** for anything above it, meaning a repository link, a shared drive link, or a split archive
- **Fallback** if the organisers say attachments only

`check_submission.py` checks the package against this budget in `4.8`, so the number has to exist before then.

## 6. Regulatory sign-off

The rules require the solution to comply with Indian aviation law and safety rules. Stage 1 and Stage 2 are simulation only, so nothing flies and no operational permission attaches, but the proposal has to say that rather than leave it implied, and it has to cite the actual instruments.

`check_submission.py` verifies the proposal names the Drone Rules 2021, the 2022 and 2023 amendments, an official source, the authority, and the words "simulation only". That is presence, not correctness. A substring checker cannot give legal advice, so a person reads the section and signs it.

Record: the date, who signed it, and a sentence saying what was checked and what it concluded.

## The receipt

Write `submission/human-preflight.json`:

```json
{
  "registered":        {"done": "2026-08-27", "email": "...", "competition_id": "UAVX-0123456789AB"},
  "eligibility":       {"done": "2026-08-27", "declaration": "no attachment to PUSHPAK, the Drone Centre, IIT Bombay, IISER Bhopal or VJTI Mumbai"},
  "clarification_channel": {"done": "2026-08-27", "last_checked": "2026-08-27"},
  "organiser_email":   {"sent": "2026-08-27", "answers": "pending", "fallback": "repo link plus zip, video under 180 s", "last_checked": "2026-08-27"},
  "delivery":          {"attachment_limit_mb": 25, "route": "repository link, archive attached if under the limit", "fallback": "shared drive link if over budget"},
  "compliance_review": {"done": "2026-08-27", "by": "...", "statement": "Stage 1 and Stage 2 are simulation only, so no operational permission attaches; the DGCA and Digital Sky obligations before any physical flight are recorded in the proposal."}
}
```

The shape is enforced by [../submission/human-preflight.schema.json](../submission/human-preflight.schema.json), not by eye. Round 4 finding 7: preflight used to read three fields and accept any truthy object for the rest, so a half-written receipt passed.

`scripts/gate.sh preflight` reads this file and refuses to run without it. That is deliberate: it is the one part of this project a machine genuinely cannot do, so the machine stops until it is done.

**Keep VJTI Mumbai in the eligibility declaration** even though the organisers removed it from the published collaborator list on 27 August. Declaring no attachment to an institution that turns out not to be involved costs nothing. The reverse is a disqualification at any stage.
