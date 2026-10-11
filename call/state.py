"""Call state vocabulary and the single Dial-status-to-state mapping (BUILD-PLAN §4.2, review 8A/14A)."""

from __future__ import annotations

from typing import Literal

CallState = Literal["planned", "dialing", "ringing", "talking", "extracting", "done", "failed"]

STATE_RANK: dict[str, int] = {
    "planned": 0,
    "dialing": 1,
    "ringing": 2,
    "talking": 3,
    "extracting": 4,
    "done": 6,
    "failed": 6,
}

TERMINAL_STATES = frozenset({"done", "failed"})

# Dial terminationType values that mean the call never produced a conversation.
FAILED_TERMINATIONS = frozenset({"busy", "no-answer", "failed", "canceled"})

_DIAL_STATES = {
    "queued": "dialing",
    "ringing": "ringing",
    "in-progress": "talking",
    "terminated": None,  # resolved by terminationType below
}


def is_terminal(state: str | None) -> bool:
    return state in TERMINAL_STATES


def map_dial_state(status: dict | None) -> tuple[str, int] | None:
    """Map a Dial `status` object to (state, rank). None means unknown: leave the row unchanged.

    Queued -> dialing, Ringing -> ringing, In-Progress -> talking,
    Terminated + completed -> extracting,
    Terminated + (busy | no-answer | failed | canceled | None | anything else) -> failed.
    `call.status_changed` can deliver a bare Terminated with no terminationType; that is failed.
    """
    if not isinstance(status, dict):
        return None
    raw_state = status.get("state")
    if not isinstance(raw_state, str):
        return None
    key = raw_state.strip().lower()
    if key not in _DIAL_STATES:
        return None
    if key != "terminated":
        state = _DIAL_STATES[key]
        return state, STATE_RANK[state]
    termination = status.get("terminationType")
    if isinstance(termination, str) and termination.strip().lower() == "completed":
        return "extracting", STATE_RANK["extracting"]
    return "failed", STATE_RANK["failed"]
