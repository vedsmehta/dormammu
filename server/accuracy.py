"""Accuracy and evidence helpers. Pure functions over calls rows (docs/CONTRACT.md shapes), no I/O.

Used by GET /api/py/accuracy, /evidence and /calls in server/app.py. The one judgment call worth
spelling out is "recap agreement": the agent is instructed to close every call with a spoken recap
("Just to confirm, ..." per router.instructions.SUMMARY_CLOSER; older fixtures used "Summary:"). If the
value the extractor pulled out (quote amount, negotiated saving, or the appointment day) also appears in
that recap turn, the extraction and the spoken summary agree. It is a consistency check between two
independent readings of the same call, not ground truth, and the route labels it that way.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

RECAP_PREFIXES = ("just to confirm", "summary")
RECAP_WINDOW = 40  # the prefix must appear within the first N chars of the agent turn
AGENT_SPEAKERS = {"agent", "assistant", "ai", "bot"}
SUCCESS_ACTIONS = {"reduced", "canceled"}
READ_ACTIONS = {"reduced", "canceled", "refused"}
TRACKS = ("quote", "schedule", "negotiate")
TERMINAL_STATES = ("done", "failed")
NO_ANSWER_TERMINATIONS = {"no-answer", "busy"}

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_TURN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z_ ]{0,24}?)\s*:\s*(.*)$")


# ---------------------------------------------------------------------------------------------
# Phone masking
# ---------------------------------------------------------------------------------------------


def mask_phone(phone: Any) -> str | None:
    """'+14155550167' -> '+1 (415) ***-**67'. Area code and last two digits survive, nothing else.

    10-digit numbers keep the area code without a country code; longer international numbers keep
    the country code; anything shorter keeps only the last two digits. None/empty stays None.
    """
    if phone is None:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if not digits:
        return None
    if len(digits) < 4:
        return "***"
    last = digits[-2:]
    if len(digits) == 11 and digits[0] == "1":
        return f"+1 ({digits[1:4]}) ***-**{last}"
    if len(digits) == 10:
        return f"({digits[0:3]}) ***-**{last}"
    if len(digits) > 11:
        return f"+{digits[: len(digits) - 10]} ***-**{last}"
    return f"***-**{last}"


# ---------------------------------------------------------------------------------------------
# Transcript access
# ---------------------------------------------------------------------------------------------


def outcome_of(call: dict) -> dict:
    outcome = call.get("outcome")
    if isinstance(outcome, str):
        try:
            outcome = json.loads(outcome)
        except ValueError:
            return {}
    return outcome if isinstance(outcome, dict) else {}


def turns_of(call: dict) -> list[dict]:
    """[{speaker, text}] from transcript_turns, else parsed from 'speaker: text' lines of transcript."""
    turns = call.get("transcript_turns")
    if isinstance(turns, str):
        try:
            turns = json.loads(turns)
        except ValueError:
            turns = None
    if isinstance(turns, list) and turns:
        return [
            {"speaker": str(t.get("speaker") or ""), "text": str(t.get("text") or "")}
            for t in turns
            if isinstance(t, dict)
        ]
    text = call.get("transcript")
    if not isinstance(text, str) or not text.strip():
        return []
    out: list[dict] = []
    for line in text.splitlines():
        match = _TURN_RE.match(line)
        if match:
            out.append({"speaker": match.group(1).strip(), "text": match.group(2).strip()})
        elif out and line.strip():
            out[-1]["text"] = f"{out[-1]['text']} {line.strip()}"
    return out


def _is_recap(text: str) -> bool:
    head = text.strip().lstrip("\"'“”‘’ ").lower()[:RECAP_WINDOW]
    return any(prefix in head for prefix in RECAP_PREFIXES)


def recap_line(call: dict) -> str | None:
    """The last agent turn that opens with "Just to confirm" or "Summary", else None.

    With no speaker labels at all (a flat transcript string), the last line containing a prefix is used.
    """
    turns = turns_of(call)
    for turn in reversed(turns):
        if turn["speaker"].strip().lower() in AGENT_SPEAKERS and _is_recap(turn["text"]):
            return turn["text"]
    if turns:
        return None
    text = call.get("transcript")
    if not isinstance(text, str):
        return None
    for line in reversed(text.splitlines()):
        if _is_recap(line):
            return line.strip()
    return None


# ---------------------------------------------------------------------------------------------
# Value variants: how a number or a day may be spoken in a recap
# ---------------------------------------------------------------------------------------------


def _below_thousand(n: int) -> str:
    parts: list[str] = []
    if n >= 100:
        parts.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + ("" if n % 10 == 0 else f" {_ONES[n % 10]}"))
    elif n > 0 or not parts:
        parts.append(_ONES[n])
    return " ".join(parts)


def number_words(n: int) -> set[str]:
    """Spoken forms of a non-negative integer under one million: 'one thousand four hundred fifty' and,
    for four-digit numbers, the pair form 'fourteen fifty' / 'twelve hundred'."""
    n = abs(int(n))
    if n >= 1_000_000:
        return set()
    if n < 1000:
        return {_below_thousand(n)}
    thousands, rest = divmod(n, 1000)
    long_form = f"{_below_thousand(thousands)} thousand"
    if rest:
        long_form = f"{long_form} {_below_thousand(rest)}"
    out = {long_form}
    if n < 10_000:
        hi, lo = divmod(n, 100)
        out.add(
            f"{_below_thousand(hi)} hundred" if lo == 0 else f"{_below_thousand(hi)} {_below_thousand(lo)}"
        )
    return out


def amount_variants(value: Any) -> set[str]:
    """Digit and word renderings of a dollar amount, all lower case, no currency symbol."""
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return set()
    if amount < 0:
        amount = -amount
    whole = round(amount)
    cents = round((amount - int(amount)) * 100)
    out: set[str] = set()
    if abs(amount - whole) < 0.005:
        out |= {f"{whole}", f"{whole:,}", f"{whole}.00", f"{whole:,}.00"}
        out |= number_words(whole)
    else:
        out |= {f"{amount:.2f}", f"{amount:,.2f}"}
        dollars = int(amount)
        out |= {f"{w} {c}" for w in number_words(dollars) for c in number_words(cents)}
        out |= {f"{w} dollars and {c} cents" for w in number_words(dollars) for c in number_words(cents)}
    return out


def _ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def slot_day_variants(slot_iso: Any) -> set[str]:
    """Ways the appointment day may be spoken: 'october 20', 'oct 20', '10/20', '2026-10-20', '20th'."""
    if not slot_iso:
        return set()
    text = str(slot_iso).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        day = datetime.fromisoformat(text).date()
    except ValueError:
        return set()
    month = _MONTHS[day.month - 1]
    return {
        f"{month} {day.day}",
        f"{month} {day.day:02d}",
        f"{month[:3]} {day.day}",
        f"{month} {_ordinal(day.day)}",
        f"{day.month}/{day.day}",
        f"{day.month:02d}/{day.day:02d}",
        day.isoformat(),
        f"the {_ordinal(day.day)}",
    }


def _normalize(text: str) -> str:
    text = text.lower().replace("-", " ").replace("–", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def value_to_check(track: str, outcome: dict) -> tuple[str, Any] | None:
    """(kind, value) the recap must mention for this track, or None when there is nothing to check."""
    if track == "quote" and outcome.get("amount_usd") is not None:
        return "amount", outcome["amount_usd"]
    if track == "negotiate" and outcome.get("saved_usd") is not None:
        return "amount", outcome["saved_usd"]
    if track == "schedule" and outcome.get("slot_iso"):
        return "day", outcome["slot_iso"]
    return None


def value_in_text(kind: str, value: Any, text: str) -> bool:
    variants = amount_variants(value) if kind == "amount" else slot_day_variants(value)
    if not variants:
        return False
    haystacks = {_normalize(text), _normalize(text).replace(" and ", " ")}
    haystacks |= {h.replace(",", "") for h in haystacks}
    return any(_normalize(v) in h for v in variants for h in haystacks)


def recap_agrees(call: dict) -> bool | None:
    """True/False when a done call has both a recap turn and an extracted value; None when unchecked."""
    if call.get("state") != "done":
        return None
    check = value_to_check(str(call.get("track") or ""), outcome_of(call))
    if check is None:
        return None
    recap = recap_line(call)
    if not recap:
        return None
    kind, value = check
    return value_in_text(kind, value, recap)


# ---------------------------------------------------------------------------------------------
# Success and coverage definitions
# ---------------------------------------------------------------------------------------------


def track_success(track: str, outcome: dict) -> bool:
    """quote: a price was quoted; schedule: a slot was confirmed; negotiate: the bill moved."""
    if track == "quote":
        return outcome.get("amount_usd") is not None
    if track == "schedule":
        return outcome.get("confirmed") is True
    if track == "negotiate":
        return outcome.get("action") in SUCCESS_ACTIONS
    return False


def has_value(track: str, outcome: dict) -> bool:
    """The extractor read a real answer from the transcript (a refusal counts, an all-nulls note does not)."""
    if track == "quote":
        return outcome.get("amount_usd") is not None
    if track == "schedule":
        return bool(outcome.get("slot_iso")) or outcome.get("confirmed") is True
    if track == "negotiate":
        return outcome.get("action") in READ_ACTIONS or outcome.get("saved_usd") is not None
    return False


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def accuracy_report(calls: list[dict]) -> dict:
    """Per-track success, overall funnel rates and recap agreement for a list of calls rows. Pure.

    Rates: answer_rate = done / (done + failed); voicemail_rate and no_answer_rate are over the same
    terminal set; extraction_coverage = done calls with a readable value / done calls. All None when the
    denominator is zero.
    """
    rows = [c for c in calls if isinstance(c, dict)]
    done = [c for c in rows if c.get("state") == "done"]
    failed = [c for c in rows if c.get("state") == "failed"]
    terminal = len(done) + len(failed)

    tracks: dict[str, dict] = {}
    for track in TRACKS:
        track_done = [c for c in done if c.get("track") == track]
        with_value = sum(1 for c in track_done if track_success(track, outcome_of(c)))
        tracks[track] = {
            "n_done": len(track_done),
            "n_with_value": with_value,
            "success_rate": _rate(with_value, len(track_done)),
        }

    voicemail = sum(1 for c in failed if c.get("termination_type") == "voicemail")
    no_answer = sum(1 for c in failed if c.get("termination_type") in NO_ANSWER_TERMINATIONS)
    durations = [d for d in (_num(c.get("duration_seconds")) for c in done) if d is not None]
    covered = sum(1 for c in done if has_value(str(c.get("track") or ""), outcome_of(c)))

    checked = agreed = 0
    disagreed: list[str] = []
    for call in done:
        verdict = recap_agrees(call)
        if verdict is None:
            continue
        checked += 1
        if verdict:
            agreed += 1
        elif len(disagreed) < 20:
            disagreed.append(str(call.get("id")))

    return {
        "tracks": tracks,
        "overall": {
            "n_calls": len(rows),
            "n_done": len(done),
            "n_failed": len(failed),
            "n_terminal": terminal,
            "n_simulated": sum(1 for c in rows if c.get("simulated")),
            "answer_rate": _rate(len(done), terminal),
            "voicemail_rate": _rate(voicemail, terminal),
            "no_answer_rate": _rate(no_answer, terminal),
            "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
            "extraction_coverage": _rate(covered, len(done)),
        },
        "recap_agreement": {
            "checked": checked,
            "agreed": agreed,
            "rate": _rate(agreed, checked),
            "disagreed_call_ids": disagreed,
        },
    }


def keewano_token_kind(token: str | None) -> str:
    """'project_key' for a JWT (dots), 'personal_token' for a kwpat- token, 'missing' when unset.

    Anything else is 'unknown'. Never returns or logs the token itself.
    """
    value = (token or "").strip()
    if not value:
        return "missing"
    if value.lower().startswith(("kwpat-", "kwpat_")):
        return "personal_token"
    if value.count(".") >= 2:
        return "project_key"
    return "unknown"
