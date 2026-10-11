"""Extractor: transcript -> Outcome dict for one track. Never raises (BUILD-PLAN §4.7).

One structured LLM call with the track's single pydantic model. The spoken "Just to confirm" recap is a hint the
prompt prefers; the model still reads the whole transcript, so a non-compliant agent degrades rather than
fails. Any failure (empty transcript, no key, timeout, refusal, parse error) returns empty_outcome() with
a note, so the tick never stalls on one call.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from router import llm

TRACKS = ("quote", "schedule", "negotiate")
MAX_TRANSCRIPT_CHARS = 12_000
TRIM_MARKER = "\n[... transcript trimmed ...]\n"


class QuoteOutcome(BaseModel):
    amount_usd: float | None
    unit: str | None
    caveats: list[str]
    note: str | None


class ScheduleOutcome(BaseModel):
    slot_iso: str | None
    confirmed: bool
    note: str | None


class NegotiateOutcome(BaseModel):
    action: Literal["reduced", "canceled", "refused", "none"]
    saved_usd: float | None
    effective_date: str | None
    note: str | None


MODELS: dict[str, type[BaseModel]] = {
    "quote": QuoteOutcome,
    "schedule": ScheduleOutcome,
    "negotiate": NegotiateOutcome,
}

SYSTEM_PROMPT = """You read the transcript of a phone call an AI assistant made on a client's behalf and \
extract the outcome as structured data.

Rules:
- Use only what the transcript says. If a value was not stated, leave it null. Never guess a number or a date.
- The assistant was told to recap before hanging up, usually starting "Just to confirm" or "So that's". When \
a recap like that is present, prefer the facts in it; otherwise read the whole conversation.
- If the call never reached a person (voicemail, dead air, one speaker only), return nulls and explain in note.
- note is a short reason only when something could not be read; otherwise null."""

FIELD_GUIDANCE = {
    "quote": (
        "amount_usd: the total price in US dollars as a number. "
        "unit: how the price applies, for example 'flat', 'per hour', 'per month'. "
        "caveats: short phrases for conditions, exclusions or fees (empty list if none)."
    ),
    "schedule": (
        "slot_iso: the booked or offered appointment as an ISO 8601 datetime with timezone offset, resolved "
        "relative to the call time (for example 'Thursday at 2:30' means the next Thursday after the call). "
        "confirmed: true only if the vendor explicitly confirmed the booking."
    ),
    "negotiate": (
        "action: reduced if the bill was lowered, canceled if the service was canceled, refused if the vendor "
        "declined, none if nothing was decided. saved_usd: the amount saved as a number (monthly if the bill is "
        "monthly). effective_date: ISO date (YYYY-MM-DD) the change takes effect, resolved relative to the "
        "call time."
    ),
}


def empty_outcome(track: str, note: str) -> dict:
    """The all-nulls outcome for a track, with the reason in note."""
    if track == "quote":
        return {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": note}
    if track == "schedule":
        return {"track": "schedule", "slot_iso": None, "confirmed": False, "note": note}
    return {"track": "negotiate", "action": "none", "saved_usd": None, "effective_date": None, "note": note}


def render_transcript(transcript: Any) -> str:
    """A string transcript as is; a list of turns as 'Speaker: text' lines. Empty string if nothing."""
    if transcript is None:
        return ""
    if isinstance(transcript, str):
        return transcript.strip()
    if isinstance(transcript, dict):
        transcript = transcript.get("turns") or transcript.get("transcript") or []
    lines = []
    for turn in transcript if isinstance(transcript, list) else []:
        if isinstance(turn, str):
            speaker, text = "Unknown", turn
        elif isinstance(turn, dict):
            speaker = turn.get("speaker") or turn.get("role") or "Unknown"
            text = turn.get("text") or turn.get("content") or turn.get("message") or ""
        else:
            continue
        text = str(text).strip()
        if text:
            lines.append(f"{str(speaker).strip().capitalize()}: {text}")
    return "\n".join(lines)


def cap_transcript(text: str, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Keep the opening and (mostly) the end, where the Summary line lives."""
    if len(text) <= limit:
        return text
    head = limit // 3
    tail = limit - head - len(TRIM_MARKER)
    return text[:head] + TRIM_MARKER + text[-tail:]


def _user_prompt(track: str, text: str) -> str:
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    return f"Track: {track}\nCall time: {now}\nFields: {FIELD_GUIDANCE[track]}\n\nTranscript:\n{text}"


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def _str(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _iso_ok(value: Any) -> bool:
    text = str(value).strip() if value else ""
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        datetime.fromisoformat(text)
        return True
    except ValueError:
        return False


def _normalize(track: str, data: dict) -> dict:
    """Coerce the model's output into the contract shape; nothing surprising reaches the row."""
    note = _str(data.get("note"))
    if track == "quote":
        caveats = [c for c in (_str(x) for x in data.get("caveats") or []) if c]
        out: dict = {"track": "quote", "amount_usd": _num(data.get("amount_usd")), "unit": _str(data.get("unit"))}
        out["caveats"] = caveats
        if out["amount_usd"] is None and note is None:
            note = "no price stated"
        out["note"] = note
        return out
    if track == "schedule":
        slot = _str(data.get("slot_iso"))
        if slot and not _iso_ok(slot):
            note = note or f"unparseable slot: {slot[:60]}"
            slot = None
        confirmed = bool(data.get("confirmed")) and slot is not None
        if slot is None and note is None:
            note = "no slot stated"
        return {"track": "schedule", "slot_iso": slot, "confirmed": confirmed, "note": note}
    action = data.get("action") if data.get("action") in ("reduced", "canceled", "refused", "none") else "none"
    saved = _num(data.get("saved_usd"))
    effective = _str(data.get("effective_date"))
    if action == "none" and saved is None and note is None:
        note = "no outcome stated"
    return {"track": "negotiate", "action": action, "saved_usd": saved, "effective_date": effective, "note": note}


async def extract(track: str, transcript: str | list[dict]) -> dict:
    """Outcome dict for this track from the transcript. Never raises; always sets track."""
    track = track if track in TRACKS else "negotiate"
    try:
        text = cap_transcript(render_transcript(transcript))
        if not text:
            return empty_outcome(track, "empty transcript")
        result = await llm.structured(SYSTEM_PROMPT, _user_prompt(track, text), MODELS[track], timeout=25.0)
        if result is None:
            return empty_outcome(track, "extraction failed")
        data = result.model_dump() if isinstance(result, BaseModel) else result
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return empty_outcome(track, "extraction failed")
        return _normalize(track, data)
    except Exception as exc:  # noqa: BLE001
        return empty_outcome(track, f"extraction error: {type(exc).__name__}")
