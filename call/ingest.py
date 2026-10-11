"""The single state-ingestion path for a Dial call update (BUILD-PLAN §4.3, review 5A/13A/14A/17A).

Every caller (the tick, the smoke script, anything else) is thin: fetch the Dial Call, call
`ingest_call_update(store, call_row, dial_call)`. This module owns the voicemail guard, the
monotonic state write, transcript storage, and the call_connected / call_terminated events.

Timing subtlety that the guard must respect: Dial marks a call Terminated/completed at hangup and
delivers `transcript` + `transcriptTurns` asynchronously afterwards (often several seconds later).
The "no callee turn" half of the guard is therefore evaluated only once the transcript exists;
until then the row sits in `extracting` (nonterminal, rank 4) and the next tick re-ingests it. The
`duration < 15` half fires immediately. Extraction must wait for `transcript` too (§4.7), and the
tick must ingest before it extracts, so a voicemail greeting never becomes a price.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from call.state import STATE_RANK, map_dial_state
from events.log import log_event

VOICEMAIL_MIN_SECONDS = 15
CALLEE_SPEAKERS = frozenset({"user", "transfer_target", "callee", "human", "customer"})
AGENT_SPEAKERS = frozenset({"agent", "assistant", "ai", "bot"})
_TRANSCRIPT_LINE = re.compile(r"^\s*\[?([A-Za-z_ ]{2,24})\]?\s*:\s*\S")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def has_callee_turn(turns: list[dict] | None, transcript: str | None) -> bool | None:
    """True/False when the transcript is present, None when it is not there yet.

    Prefers Dial's structured `transcriptTurns` (speaker "user" or "transfer_target" is the
    callee). Falls back to "Speaker: text" lines in the flat transcript when turns are absent.
    """
    if isinstance(turns, list) and turns:
        for turn in turns:
            speaker = str((turn or {}).get("speaker") or "").strip().lower()
            if speaker in CALLEE_SPEAKERS or (speaker and speaker not in AGENT_SPEAKERS):
                return True
        return False
    if transcript is None:
        return None
    labeled = False
    for line in transcript.splitlines():
        match = _TRANSCRIPT_LINE.match(line)
        if not match:
            continue
        labeled = True
        speaker = match.group(1).strip().lower()
        if speaker not in AGENT_SPEAKERS:
            return True
    if labeled:
        return False
    # Unlabeled prose: we cannot attribute speakers. Do not block on this criterion.
    return None if not transcript.strip() else True


def _status_of(dial_call: dict) -> dict:
    status = dial_call.get("status")
    status = dict(status) if isinstance(status, dict) else {}
    if status.get("terminationType") is None and dial_call.get("terminationType") is not None:
        status["terminationType"] = dial_call["terminationType"]
    return status


def _duration(dial_call: dict) -> float | None:
    value = dial_call.get("duration", dial_call.get("durationSeconds"))
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _user_id_for(store: Any, call_row: dict) -> str | None:
    if call_row.get("user_id"):
        return str(call_row["user_id"])
    run = store.get_run(call_row["run_id"]) if call_row.get("run_id") else None
    return run.get("user_id") if run else None


def ingest_call_update(store: Any, call_row: dict, dial_call: dict) -> dict | None:
    """Apply one Dial Call snapshot to our calls row. Returns the refreshed row, or None when the
    snapshot carried nothing we could map (unknown status) or the row no longer exists."""
    if not isinstance(dial_call, dict) or not call_row or not call_row.get("id"):
        return None
    call_id = call_row["id"]
    status = _status_of(dial_call)
    mapped = map_dial_state(status)
    if mapped is None:
        return None
    state, rank = mapped
    dial_terminated = str(status.get("state") or "").strip().lower() == "terminated"
    termination_type = status.get("terminationType")
    duration = _duration(dial_call)
    transcript = dial_call.get("transcript")
    turns = dial_call.get("transcriptTurns")
    if turns is None:
        turns = dial_call.get("transcript_turns")

    # Voicemail guard [review 17A]: Terminated/completed with nobody on the other end.
    voicemail = False
    if state == "extracting":
        callee = has_callee_turn(turns, transcript)
        if callee is False or (duration is not None and duration < VOICEMAIL_MIN_SECONDS):
            voicemail = True
            state, rank = "failed", STATE_RANK["failed"]
            termination_type = "voicemail"

    fields: dict[str, Any] = {}
    if dial_terminated:
        fields["termination_type"] = termination_type
        fields["ended_at"] = dial_call.get("terminatedAt") or _now_iso()
        if duration is not None:
            fields["duration_seconds"] = duration
    if transcript is not None and not call_row.get("transcript"):
        fields["transcript"] = transcript
        fields["transcript_turns"] = turns if isinstance(turns, list) else None

    previous_rank = int(call_row.get("state_rank") or 0)
    changed = store.update_call_state(call_id, state, rank, fields or None)

    if not changed and fields:
        # The state write was refused (same or higher rank already), but a transcript or final
        # duration that arrived late still belongs on the row. Never touch state here.
        late = {
            k: v for k, v in fields.items() if k in ("transcript", "transcript_turns", "duration_seconds")
        }
        if late:
            store.update_call(call_id, late)

    if changed:
        user_id = _user_id_for(store, call_row)
        run_id = call_row.get("run_id")
        if user_id:
            # First observed connection. Dial can skip In-Progress between polls, so a jump from
            # ringing straight to Terminated/completed also counts as connected.
            if state in ("talking", "extracting") and previous_rank < STATE_RANK["talking"]:
                log_event(
                    store,
                    user_id,
                    "call_connected",
                    {"dial_call_id": call_row.get("dial_call_id"), "track": call_row.get("track")},
                    run_id=run_id,
                    call_id=call_id,
                )
            if dial_terminated and previous_rank < STATE_RANK["extracting"]:
                log_event(
                    store,
                    user_id,
                    "call_terminated",
                    {
                        "dial_call_id": call_row.get("dial_call_id"),
                        "track": call_row.get("track"),
                        "state": state,
                        "termination_type": termination_type,
                        "duration_seconds": duration,
                        "voicemail": voicemail,
                        "transcript_ready": transcript is not None,
                    },
                    run_id=run_id,
                    call_id=call_id,
                )

    return store.get_call(call_id)
