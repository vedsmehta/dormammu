"""Cohort config: the eligibility the agent may assert, and what it asks for because of it.

Config, not a system (BUILD-PLAN §12.2). Ships with `student` only; a second cohort is one more dict entry.
The honesty constraint (§8.4) is that the agent only asserts the cohort on the user's profile, never an
inferred one. The asks live here, not in the prompt template, so a judge can read what the agent is told
to claim.

The asks are specific per track because the student moving off campus (docs/USE-CASE.md) asks a mover for
something different from what they ask an internet provider or a dentist:
  quote      movers: a student move discount if one exists, and no deposit above 10 percent of the quote
  negotiate  internet: the student rate, the installation or activation fee waived, no contract, and the
             first month free if the vendor offers it
  schedule   dentist: new-patient slot, and that they take student insurance or have a self-pay rate
`asks` stays as the generic list for callers that do not know the track yet.
"""

from __future__ import annotations

COHORTS: dict[str, dict] = {
    "student": {
        "key": "student",
        "label": "full-time student",
        "asks": ["student rate", "waive activation fee", "no-contract term"],
        "asks_by_track": {
            "quote": ["student move discount if any", "no deposit above 10 percent"],
            "negotiate": [
                "student rate",
                "waive the installation or activation fee",
                "no-contract term",
                "first month free if offered",
            ],
            "schedule": ["new patient", "accepts student insurance or self-pay rate"],
        },
        "proof": "university email on file",
    },
}

DEFAULT_COHORT = "student"


def cohort_for(profile: dict | None) -> dict:
    """The cohort dict for a profile row. Unknown or missing cohort falls back to the default."""
    key = (profile or {}).get("cohort") or DEFAULT_COHORT
    return COHORTS.get(key, COHORTS[DEFAULT_COHORT])


def cohort_asks(cohort: dict | None, track: str | None) -> list[str]:
    """The asks for one track, else the generic list. Always a list of non-empty strings."""
    cohort = cohort or {}
    by_track = cohort.get("asks_by_track") or {}
    asks = by_track.get(track) if track else None
    if not asks:
        asks = cohort.get("asks") or []
    return [str(a).strip() for a in asks if a and str(a).strip()]


def ask_line(cohort: dict | None, track: str | None) -> str:
    """The 'Ask for: ...' sentence appended to an objective, or '' when the cohort has no asks."""
    asks = cohort_asks(cohort, track)
    return "Ask for: " + ", ".join(asks) + "." if asks else ""
