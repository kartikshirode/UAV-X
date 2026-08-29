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

REQUIRED_RUNS = [
    "survey_baseline", "relay_required", "direct_only", "relay_kill",
    "link_loss", "encounter", "encounter_noyield", "mission_integrated",
]
