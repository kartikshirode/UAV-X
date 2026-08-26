# Human preflight

**The loop must not start until every box below has a dated receipt in `submission/human-preflight.json`.**

Round 3 finding 4: these were filed as "open items, not blocking". They are not backlog. An unregistered or ineligible entrant has no valid submission however good the simulation is, and eligibility can disqualify the entry at any stage, including after Stage 1 results. Building for five weeks and then discovering the entry was never valid is the worst outcome available, and it is entirely preventable this week.

None of these can be done by an agent. All of them need you.

## 1. Registration

Register on techfest.org, Competitions, PUSHPAK Grand Challenge, UAV-X.

This is the competition's entry mechanism. There is no submission portal, so registration is the only thing that connects your email to an entry.

Record: the date, and the registered email address.

## 2. Eligibility

From the official rules, stated twice:

> Project staff, research staff, consultants, interns, or other personnel directly engaged with the PUSHPAK Project, Drone Centre, or the organizing/host institutions are not eligible, whether participating individually or as part of a team.

Organising and host institutions are IIT Bombay, IISER Bhopal and VJTI Mumbai. Solo entry, so this is one check rather than five.

Record: an explicit declaration that you hold no such attachment.

## 3. Clarification channel

Join the UAV-X WhatsApp group: https://chat.whatsapp.com/EdOZigIfR4s0LvBl4N49XB

Organiser clarifications land there before anywhere else. If the video format or the source delivery method is answered for someone else, it is answered for you.

Record: the date joined.

## 4. The organiser questions

Send [organiser-email.md](organiser-email.md). Two of its answers change work that starts now:

- **Source delivery.** Repository link or email attachment? The plan currently does both, which is a hedge rather than compliance.
- **Video limit.** No published limit, so the 180 s cap in `check_submission.py` is our invention. If theirs is shorter, the edit changes.

Record: the date sent, and either the answers or the fallback being assumed.

## 5. Delivery method, decided

Round 3 is right that finding out in W5 that the intended attachment cannot be delivered risks the submission itself. A three minute video plus a source archive will exceed most mail servers.

Decide now, and record it:

- **Total attachment budget** your mail provider allows
- **Delivery route** for anything above it, meaning a repository link, a shared drive link, or a split archive
- **Fallback** if the organisers say attachments only

`check_submission.py` checks the package against this budget in W5, so the number has to exist before then.

## The receipt

Write `submission/human-preflight.json`:

```json
{
  "registered":        {"done": "2026-08-27", "email": "..."},
  "eligibility":       {"done": "2026-08-27", "declaration": "no attachment to PUSHPAK, the Drone Centre, IIT Bombay, IISER Bhopal or VJTI Mumbai"},
  "clarification_channel": {"done": "2026-08-27"},
  "organiser_email":   {"sent": "2026-08-27", "answers": "pending", "fallback": "repo link plus zip, video under 180 s"},
  "delivery":          {"attachment_limit_mb": 25, "route": "repository link, archive attached if under the limit"}
}
```

`scripts/gate.sh preflight` reads this file and refuses to run without it. That is deliberate: it is the one part of this project a machine genuinely cannot do, so the machine stops until it is done.
