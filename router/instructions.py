"""Instruction builder: CallPlan + vendor + profile -> the prompt the phone agent runs on. Pure.

The AI disclosure is a const prepended inside build(), so no template edit can drop it (BUILD-PLAN §8.1).
Every instruction ends with SUMMARY_CLOSER, the spoken "Summary:" line the extractor prefers (§4.8).
Nothing here is ever None or "undefined" in the output: optional pieces are omitted, not interpolated.
The vendor's phone number is dialed by dispatch and never appears in the instruction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from router.cohorts import cohort_for

AI_DISCLOSURE = "You are an AI assistant placing a phone call on behalf of"

SUMMARY_CLOSER = (
    'Before you hang up, say exactly: "Summary: " followed by the key facts in one sentence, '
    "the price, the date, or the outcome we agreed. Then say goodbye."
)

TRACK_LABELS = {"quote": "price quote", "schedule": "appointment booking", "negotiate": "bill review"}

COMMON_RULES = (
    "State the objective within your first two sentences. No small talk.",
    'Never invent details about the client. For anything not written here, say: "Let me check and call back."',
    "Do not make payments, share card details, or agree to anything beyond the objective.",
    (
        "Be polite and brief. If you reach voicemail, leave a one-sentence message with the objective and say an "
        "AI assistant will call back. Do not negotiate with a machine."
    ),
)


def _text(value: Any) -> str:
    """A safe string: None and non-strings never leak into the instruction."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "null", "undefined") else text


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def _day(dt: datetime) -> str:
    return f"{dt:%a %b} {dt.day}"


def _window_text(window: Any) -> str | None:
    if not isinstance(window, dict):
        return _text(window) or None
    start, end = _parse_dt(window.get("start")), _parse_dt(window.get("end"))
    if start is None:
        raw = _text(window.get("start"))
        return raw or None
    if end is None:
        return f"{_day(start)} from {_clock(start)}"
    if end.date() == start.date():
        return f"{_day(start)}, {_clock(start)} to {_clock(end)}"
    return f"{_day(start)} {_clock(start)} to {_day(end)} {_clock(end)}"


def _availability(prefs: dict) -> list[str]:
    windows = prefs.get("availability")
    if not isinstance(windows, list):
        return []
    return [w for w in (_window_text(x) for x in windows) if w]


def _money(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _quote_lines(prefs: dict) -> list[str]:
    lines = [
        "State the job in one or two sentences, then ask for a total price and exactly what it includes.",
        "Ask whether the price is flat or hourly, and about any fees not in the total.",
        "Ask which dates they have available and whether the date changes the price.",
    ]
    budget = _money(prefs.get("budget_ceiling_usd"))
    if budget:
        lines.append(
            f"The client's budget ceiling is {budget}. Do not volunteer it. If the price is above it, "
            "say the client will think it over."
        )
    return lines


def _schedule_lines(prefs: dict) -> list[str]:
    windows = _availability(prefs)
    preferred = [_text(p) for p in prefs.get("preferred_times") or [] if _text(p)]
    if windows:
        first = "Ask for the earliest available slot inside the client's availability: " + "; ".join(windows) + "."
    elif preferred:
        first = "Ask for the earliest available slot. The client prefers " + ", ".join(preferred) + "."
    else:
        first = "Ask for the earliest available slot. The client is flexible on timing."
    return [
        first,
        (
            "Repeat the exact day and time back to them before accepting it, then confirm the booking is under "
            "the client's name."
        ),
        "If nothing fits, ask for the next two options and say the client will confirm.",
    ]


def _negotiate_lines(display_name: str) -> list[str]:
    if display_name:
        first = (
            f"Say you are calling about the account under the name {display_name} and that you are reviewing "
            "the bill."
        )
    else:
        first = (
            "Say you are calling about the client's account and that you are reviewing the bill. Do not guess "
            "account details; if they need an identifier you do not have, say you will check and call back."
        )
    return [
        first,
        "Ask what current promotion or retention offer is available on this account.",
        (
            "Accept only a reduction, or a cancellation the client asked for. Never agree to add services, "
            "upgrades, or a new contract."
        ),
        "If they offer a reduction, confirm the new monthly amount and the date it takes effect.",
    ]


def _cohort_lines(cohort: dict, objective: str) -> list[str]:
    label = _text(cohort.get("label")) or "client"
    proof = _text(cohort.get("proof"))
    asks = [_text(a) for a in cohort.get("asks") or [] if _text(a)]
    line = f"The client is a {label}" + (f" ({proof})." if proof else ".")
    if asks:
        ask_line = "Ask for: " + ", ".join(asks) + "."
        if ask_line not in objective:
            line += " " + ask_line
    line += " Only assert this eligibility; never claim any other discount category."
    return [line]


def _client_details(display_name: str, phone: str) -> str:
    bits = []
    if display_name:
        bits.append(f"name {display_name}")
    if phone:
        bits.append(f"callback number {phone}")
    if not bits:
        return "Client details you may share: nothing beyond what is written here."
    return "Client details you may share: " + "; ".join(bits) + "."


def build(plan: dict, vendor: dict, profile: dict | None, prior_context: str | None = None) -> str:
    """The full instruction for one call. Pure; never interpolates None."""
    plan = plan or {}
    vendor = vendor or {}
    profile = profile if isinstance(profile, dict) else {}
    prefs = profile.get("prefs") if isinstance(profile.get("prefs"), dict) else {}

    display_name = _text(profile.get("display_name"))
    who = display_name or "a client"
    phone = _text(profile.get("phone"))
    vendor_name = _text(vendor.get("name")) or _text(vendor.get("key")) or _text(plan.get("vendor_key")) or "them"
    track = _text(plan.get("track")) or _text(vendor.get("track")) or "quote"
    if track not in TRACK_LABELS:
        track = "quote"
    objective = _text(plan.get("objective")) or f"Call {vendor_name} about the client's request."
    cohort = cohort_for(profile or None)
    context = _text(prior_context)

    disclosure = (
        f"{AI_DISCLOSURE} {who}. Your very first words on this call, before anything else, must be: "
        f'"Hi, this is an AI assistant calling on behalf of {who}." Never claim to be human; if asked, confirm '
        "you are an AI."
    )
    lines = [disclosure, f"You are calling {vendor_name}. Call type: {TRACK_LABELS[track]}."]
    if context:
        lines.append(f"Open with this context right after the disclosure: {context}")
    lines.append(f"Objective: {objective}")
    lines.append(f"How to run this {TRACK_LABELS[track]} call:")
    if track == "quote":
        steps = _quote_lines(prefs)
    elif track == "schedule":
        steps = _schedule_lines(prefs)
    else:
        steps = _negotiate_lines(display_name)
    lines.extend(f"- {step}" for step in steps)
    lines.extend(_cohort_lines(cohort, objective))
    lines.append("Rules:")
    lines.extend(f"- {rule}" for rule in COMMON_RULES)
    lines.append(_client_details(display_name, phone))
    lines.append(SUMMARY_CLOSER)
    return "\n".join(lines)
