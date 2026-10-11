"""Instruction builder: CallPlan + vendor + profile -> the prompt the phone agent runs on. Pure.

The agent should sound like a person making a call, not like an assistant reading a script. So the
instruction is written as "say this, then this", in plain speech, with hard limits on how much it talks.

Order on every call (BUILD-PLAN §8.1 and negotiation_flow.md §6, "one sentence on first contact, then
move on"):
  1. OPENER, exactly: "Hi, this is <caller>, am I speaking with <vendor>?"
  2. One sentence that it's an AI assistant calling for the client. Never claimed to be human.
  3. The ask, in one sentence. Then it lets the other person talk.
Every instruction ends with SUMMARY_CLOSER, the spoken recap the extractor prefers (§4.8).
Nothing here is ever None or "undefined" in the output: optional pieces are omitted, not interpolated.
The vendor's phone number is dialed by dispatch and never appears in the instruction.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from router.cohorts import cohort_for
from router.negotiation import negotiation_lines

DEFAULT_CALLER_NAME = "Alex"

# Spoken once, right after the opener. Kept as a const so no template edit can drop it.
AI_DISCLOSURE = "I'm an AI assistant calling for"

SUMMARY_CLOSER = (
    'Before you hang up, say "Just to confirm," and repeat the key facts in one short sentence: the price, '
    "the day and time, or what was agreed. Then say thanks and goodbye."
)

TRACK_LABELS = {"quote": "price quote", "schedule": "appointment", "negotiate": "bill"}

VOICE_RULES = (
    (
        "Talk like a person on the phone, not like a script. Short sentences. Contractions. One question at a "
        "time, then let them talk."
    ),
    (
        "Don't over-explain. Say what you need in one sentence. Don't repeat back what they just said unless "
        "you're confirming a number, a day or a time."
    ),
    "No lists, no headings, nothing read out in bullet points. Never narrate what you're doing or why.",
    (
        'Plain words are fine: "sure", "got it", "okay", "one sec". Don\'t say "certainly", "I understand your '
        'concern", "as an AI", or "I apologize for any inconvenience".'
    ),
    (
        "If they ask something you don't know, say \"I'd have to check on that\" and move on. Never make up a "
        "detail about the client."
    ),
    "If you get voicemail, leave one sentence with who you are and what it's about, then hang up.",
)


def _text(value: Any) -> str:
    """A safe string: None and non-strings never leak into the instruction."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "null", "undefined") else text


def caller_name_for(profile: dict | None) -> str:
    """The name the agent introduces itself with: profile.prefs.caller_name, else CALLER_NAME, else Alex."""
    prefs = profile.get("prefs") if isinstance(profile, dict) else None
    if isinstance(prefs, dict):
        name = _text(prefs.get("caller_name"))
        if name:
            return name
    return _text(os.environ.get("CALLER_NAME")) or DEFAULT_CALLER_NAME


def opener(caller: str, vendor_name: str) -> str:
    """The first sentence on every call."""
    return f"Hi, this is {caller}, am I speaking with {vendor_name}?"


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
        "Say what the job is in one sentence, then ask what that would run all-in.",
        "Ask if that's flat or hourly, and if anything's extra on top.",
        "Ask what dates they have open.",
    ]
    budget = _money(prefs.get("budget_ceiling_usd"))
    if budget:
        lines.append(
            f"The client's budget ceiling is {budget}. Don't bring it up. If the price is over it, say the "
            "client will think it over."
        )
    return lines


def _schedule_lines(prefs: dict, display_name: str) -> list[str]:
    windows = _availability(prefs)
    preferred = [_text(p) for p in prefs.get("preferred_times") or [] if _text(p)]
    if windows:
        first = "Ask for the earliest they have inside the client's availability: " + "; ".join(windows) + "."
    elif preferred:
        first = "Ask for the earliest they have. The client prefers " + ", ".join(preferred) + "."
    else:
        first = "Ask for the earliest they have. The client is flexible on timing."
    under = f"under the name {display_name}" if display_name else "under the client's name"
    return [
        first,
        f"Repeat the day and time back before saying yes, then book it {under}.",
        "If nothing fits, ask for their next two options and say the client will confirm.",
    ]


def _negotiate_lines(display_name: str, context_numbers: str | None) -> list[str]:
    if display_name:
        first = f"Say you're calling about the account under the name {display_name} and you're going over the bill."
    else:
        first = (
            "Say you're calling about the client's account and you're going over the bill. If they need an "
            "account detail you don't have, say you'll check and call back."
        )
    return [first, *negotiation_lines(context_numbers)]


def _cohort_lines(cohort: dict, objective: str) -> list[str]:
    label = _text(cohort.get("label")) or "client"
    proof = _text(cohort.get("proof"))
    asks = [_text(a) for a in cohort.get("asks") or [] if _text(a)]
    line = f"The client is a {label}" + (f" ({proof})." if proof else ".")
    if asks:
        ask_line = "Ask for: " + ", ".join(asks) + "."
        if ask_line not in objective:
            line += " " + ask_line
    line += " Only claim that; never any other discount category."
    return [line]


def _client_details(display_name: str, phone: str) -> str:
    bits = []
    if display_name:
        bits.append(f"name {display_name}")
    if phone:
        bits.append(f"callback number {phone}")
    if not bits:
        return "Client details you may share: nothing beyond what's written here."
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
    caller = caller_name_for(profile)
    vendor_name = (
        _text(vendor.get("name")) or _text(vendor.get("key")) or _text(plan.get("vendor_key")) or "there"
    )
    track = _text(plan.get("track")) or _text(vendor.get("track")) or "quote"
    if track not in TRACK_LABELS:
        track = "quote"
    objective = _text(plan.get("objective")) or f"Call {vendor_name} about the client's request."
    cohort = cohort_for(profile or None)
    context = _text(prior_context)

    lines = [
        f'Your first words on this call, exactly: "{opener(caller, vendor_name)}" Then wait for them to answer.',
        (
            f"Then say once: \"{AI_DISCLOSURE} {who}.\" If they ask whether you're a person, say you're an AI "
            "assistant and carry on. Never claim to be human."
        ),
    ]
    if context:
        lines.append(f"Right after that, bring up last time in one sentence: {context}")
    lines.append(f"What you're calling about: {objective}")
    lines.append(f"How to run this {TRACK_LABELS[track]} call:")
    if track == "quote":
        steps = _quote_lines(prefs)
    elif track == "schedule":
        steps = _schedule_lines(prefs, display_name)
    else:
        steps = _negotiate_lines(display_name, context or None)
    lines.extend(f"- {step}" for step in steps)
    lines.extend(_cohort_lines(cohort, objective))
    lines.append("How to sound:")
    lines.extend(f"- {rule}" for rule in VOICE_RULES)
    lines.append(_client_details(display_name, phone))
    lines.append(SUMMARY_CLOSER)
    return "\n".join(lines)
