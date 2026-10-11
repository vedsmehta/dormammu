"""Pure scoring of extractor output against labeled Outcome dicts (Lane E2, extraction accuracy evidence).

score() compares one expected Outcome with one actual Outcome field by field, with the tolerances a person
checking the call would accept: amounts within a dollar, slots on the same day and hour, dates on the same
day, actions equal, units by meaning (flat, per hour, per month). caveats and note are never scored.
summarize() turns a list of scores into rates overall and per track. recap_agreement() asks whether the
spoken "Just to confirm" line the agent is told to end with actually names the value that was extracted.
No I/O, no clock, and nothing here raises on the shapes the contract allows (None anywhere, missing keys,
odd strings from the model).
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime
from typing import Any

SCORED_FIELDS: dict[str, tuple[str, ...]] = {
    "quote": ("amount_usd", "unit"),
    "schedule": ("slot_iso", "confirmed"),
    "negotiate": ("action", "saved_usd", "effective_date"),
}
IGNORED_FIELDS = ("caveats", "note", "track")
AMOUNT_TOLERANCE_USD = 1.0
RECAP_MARKERS = ("just to confirm", "summary")
AGENT_SPEAKERS = ("agent", "assistant", "ai", "bot")

# (canonical unit, words that mean it). Checked in order; the first hit wins.
_UNIT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("per hour", ("hour", "hours", "hr", "hrs", "hourly")),
    ("per month", ("month", "monthly", "mo")),
    ("per day", ("day", "daily")),
    ("flat", ("flat", "total", "fixed", "job", "move", "all-in", "all in", "lump", "one-time", "one time",
              "project", "service")),
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MONTHS = tuple(m.lower() for m in calendar.month_name if m)
_MONTH_ABBR = tuple(m.lower() for m in calendar.month_abbr if m)
_WEEKDAYS = tuple(d.lower() for d in calendar.day_name)


def parse_dt(value: Any) -> datetime | None:
    """ISO datetime or date string -> datetime (aware when the string carries an offset). None otherwise."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rate(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def amount_match(expected: Any, actual: Any, tolerance: float = AMOUNT_TOLERANCE_USD) -> bool:
    """Both null, or both numbers within `tolerance` dollars."""
    e, a = _num(expected), _num(actual)
    if e is None or a is None:
        return e is None and a is None
    return abs(e - a) <= tolerance


def canonical_unit(value: Any) -> str | None:
    """'Hourly', 'per hr', '/hour' -> 'per hour'; 'flat rate', 'total', 'per job' -> 'flat'; else lowercased."""
    if value is None:
        return None
    text = re.sub(r"[^a-z0-9 /-]", " ", str(value).lower()).strip()
    if not text:
        return None
    tokens = set(re.split(r"[\s/]+", text))
    for canonical, keys in _UNIT_RULES:
        if any(key in tokens or (" " in key and key in text) or ("-" in key and key in text) for key in keys):
            return canonical
    return re.sub(r"\s+", " ", text)


def unit_match(expected: Any, actual: Any) -> bool:
    return canonical_unit(expected) == canonical_unit(actual)


def slot_match(expected: Any, actual: Any) -> bool:
    """Same calendar day and hour. The wall clocks may agree (both read as the vendor's local time) or the
    instants may agree once the actual is converted into the expected offset, so a UTC answer and a
    DST-shifted offset both pass. Minutes are not compared."""
    if not expected and not actual:
        return True
    if not expected or not actual:
        return False
    e, a = parse_dt(expected), parse_dt(actual)
    if e is None or a is None:
        return False
    if (e.date(), e.hour) == (a.date(), a.hour):
        return True
    if e.tzinfo is not None and a.tzinfo is not None:
        shifted = a.astimezone(e.tzinfo)
        return (e.date(), e.hour) == (shifted.date(), shifted.hour)
    return False


def date_match(expected: Any, actual: Any) -> bool:
    """Same calendar date; a date string and a datetime string on that date both count."""
    if not expected and not actual:
        return True
    if not expected or not actual:
        return False
    e, a = parse_dt(expected), parse_dt(actual)
    return e is not None and a is not None and e.date() == a.date()


def action_match(expected: Any, actual: Any) -> bool:
    return str(expected or "none").strip().lower() == str(actual or "none").strip().lower()


def bool_match(expected: Any, actual: Any) -> bool:
    return bool(expected) == bool(actual)


_COMPARATORS = {
    "amount_usd": amount_match,
    "unit": unit_match,
    "slot_iso": slot_match,
    "confirmed": bool_match,
    "action": action_match,
    "saved_usd": amount_match,
    "effective_date": date_match,
}


def score(expected: dict, actual: dict | None, track: str) -> dict:
    """{"track", "fields": {field: bool}, "exact": bool, "partial": float}. caveats and note are ignored.
    A missing or malformed actual scores as all-wrong except where the label is itself null."""
    track = track if track in SCORED_FIELDS else "negotiate"
    expected = expected if isinstance(expected, dict) else {}
    actual = actual if isinstance(actual, dict) else {}
    fields = {name: _COMPARATORS[name](expected.get(name), actual.get(name)) for name in SCORED_FIELDS[track]}
    hits = sum(1 for ok in fields.values() if ok)
    return {"track": track, "fields": fields, "exact": hits == len(fields), "partial": rate(hits, len(fields))}


def _rates(scores: list[dict]) -> dict:
    n = len(scores)
    exact = sum(1 for s in scores if s.get("exact"))
    counts: dict[str, list[int]] = {}
    for s in scores:
        for field, ok in (s.get("fields") or {}).items():
            bucket = counts.setdefault(field, [0, 0])
            bucket[0] += 1 if ok else 0
            bucket[1] += 1
    return {
        "n": n,
        "exact_rate": rate(exact, n),
        "field_rates": {field: rate(hit, total) for field, (hit, total) in counts.items()},
    }


def summarize(scores: list[dict]) -> dict:
    """{"n", "exact_rate", "field_rates": {field: rate}, "by_track": {track: {"n", "exact_rate", "field_rates"}}}.
    Scores are score() dicts (they carry "track"). Tracks with no scores are left out of by_track."""
    scores = [s for s in (scores or []) if isinstance(s, dict)]
    overall = _rates(scores)
    by_track: dict[str, dict] = {}
    for track in SCORED_FIELDS:
        subset = [s for s in scores if s.get("track") == track]
        if subset:
            by_track[track] = _rates(subset)
    return {**overall, "by_track": by_track}


def agent_lines(transcript: Any) -> list[str]:
    """The agent's lines, in order, from a turn list ({speaker, text}) or a 'Speaker: text' string."""
    if isinstance(transcript, str):
        lines = []
        for raw in transcript.splitlines():
            speaker, sep, text = raw.partition(":")
            if sep and speaker.strip().lower() in AGENT_SPEAKERS and text.strip():
                lines.append(text.strip())
        return lines
    if isinstance(transcript, dict):
        transcript = transcript.get("turns") or transcript.get("transcript") or []
    lines = []
    for turn in transcript if isinstance(transcript, list) else []:
        if not isinstance(turn, dict):
            continue
        speaker = str(turn.get("speaker") or turn.get("role") or "").strip().lower()
        text = str(turn.get("text") or turn.get("content") or "").strip()
        if speaker in AGENT_SPEAKERS and text:
            lines.append(text)
    return lines


def recap_line(transcript: Any) -> str | None:
    """The last agent line that opens a recap ("Just to confirm", "Summary"), or None when the agent skipped it."""
    for line in reversed(agent_lines(transcript)):
        lowered = line.lower()
        if any(marker in lowered for marker in RECAP_MARKERS):
            return line
    return None


def numbers_in(text: str) -> list[float]:
    """Every number in the text as a float, commas removed ('$1,450' -> 1450.0)."""
    found = []
    for raw in _NUMBER.findall(text or ""):
        try:
            found.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return found


def recap_value(outcome: dict | None) -> float | datetime | None:
    """The one value a recap has to name for this outcome: the amount, the slot, or the saving. None if nothing."""
    outcome = outcome if isinstance(outcome, dict) else {}
    track = outcome.get("track")
    if track == "quote":
        return _num(outcome.get("amount_usd"))
    if track == "schedule":
        return parse_dt(outcome.get("slot_iso"))
    if track == "negotiate":
        return _num(outcome.get("saved_usd"))
    return None


def recap_agreement(transcript_turns: Any, outcome: dict | None) -> bool:
    """True when the agent's final recap line names the extracted amount (within a dollar), the slot's weekday
    or its month and day, or the saved amount. False when there is no recap line or nothing to check."""
    line = recap_line(transcript_turns)
    value = recap_value(outcome)
    if line is None or value is None:
        return False
    lowered = line.lower()
    if isinstance(value, datetime):
        if _WEEKDAYS[value.weekday()] in lowered:
            return True
        month_named = _MONTHS[value.month - 1] in lowered or bool(
            re.search(rf"\b{_MONTH_ABBR[value.month - 1]}\b", lowered)
        )
        day_named = bool(re.search(rf"\b{value.day}(?:st|nd|rd|th)?\b", lowered))
        return month_named and day_named
    return any(abs(number - value) <= AMOUNT_TOLERANCE_USD for number in numbers_in(line))
