"""The lists check_submission.py enforces, importable without running it.

check_submission.py does its work at module scope, so importing it runs the
whole check. The fixture suite needs the two lists and nothing else, and
round 5 finding 2 was caused by those lists being copied by hand into the
fixture and then drifting.
"""

REQUIRED_SECTIONS = [
    "architecture", "communication", "relay", "fault", "safety", "results",
    # From the rules, not the rubric: "Proposed solutions must comply with all
    # applicable laws, aviation requirements, and safety protocols in India."
    # A BVLOS swarm proposal that never mentions BVLOS regulation is ignoring a
    # stated rule, in front of a panel from an aerospace department.
    "regulat",
]

# Round 6, the conclusion pass. W1's chunk gates need a scenario to run, and it
# must not be one of the nine: harness_check proves the harness works and says
# nothing about the rubric. Citing it as evidence would be citing a hover.
# check_docs.py knows it is exempt from both directions of the scenario check.
HARNESS_RUNS = ["harness_check"]

REQUIRED_RUNS = [
    "survey_baseline", "relay_required", "direct_only", "relay_kill",
    "link_loss", "encounter", "encounter_noyield", "mission_integrated",
    # Round 6 finding 5: the queue is sized against a 45 second outage and
    # the 2.25 s drain bound is derived from it, while the two accepted
    # recoveries last 32.5 s and 28.0 s. Nothing had held the route down
    # for as long as the arithmetic assumes.
    "queue_drain",
]


# Round 5 finding 9: "regulat" appearing anywhere satisfied the old check, so a
# sentence saying the system is unregulated would have passed. These are the
# things a real regulatory section cannot avoid naming. A checker cannot judge
# legal correctness, which is why submission/human-preflight.json also carries a
# dated human sign-off.
REQUIRED_COMPLIANCE = [
    ("Drone Rules", "the principal instrument, the Drone Rules 2021"),
    ("2021", "the year of the principal rules"),
    ("2022", "the first amendment"),
    ("2023", "the second amendment"),
    ("civilaviation.gov.in", "an official source rather than a summary"),
    ("DGCA", "the authority"),
    ("simulation only", "the plain statement that Stage 1 and 2 fly nothing"),
]
