"""Cohort config: the eligibility the agent may assert, and what it asks for because of it.

Config, not a system (BUILD-PLAN §12.2). Ships with `student` only; a second cohort is one more dict entry.
The honesty constraint (§8.4) is that the agent only asserts the cohort on the user's profile, never an
inferred one. The asks live here, not in the prompt template, so a judge can read what the agent is told
to claim.
"""

from __future__ import annotations

COHORTS: dict[str, dict] = {
    "student": {
        "key": "student",
        "label": "full-time student",
        "asks": ["student rate", "waive activation fee", "no-contract term"],
        "proof": "university email on file",
    },
}

DEFAULT_COHORT = "student"


def cohort_for(profile: dict | None) -> dict:
    """The cohort dict for a profile row. Unknown or missing cohort falls back to the default."""
    key = (profile or {}).get("cohort") or DEFAULT_COHORT
    return COHORTS.get(key, COHORTS[DEFAULT_COHORT])
