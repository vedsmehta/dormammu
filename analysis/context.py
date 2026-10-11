"""Prior context from insights: one or two spoken sentences for the agent, built only from the data.

router.plan.prior_context() says what happened last time with this vendor. This module says that and
what the whole history says ("across 6 calls they answered every time and were cheapest 4 times"), so
the agent walks in knowing the vendor's pattern, not just its last price. The router can adopt
`prior_context_for` in a later phase; nothing here edits the router.

Every number comes from the insights report; when the report has nothing substantive for the vendor
the result is None, never a guess.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from aggregator.aggregate import parse_ts
from analysis.insights import FAILURE_LABELS, _bucket_label, compute_insights

MIN_CALLS_FOR_PATTERN = 2


def _money(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _short_date(value: Any) -> str | None:
    dt = parse_ts(value)
    return f"{dt:%b} {dt.day}" if dt else None


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def _slot_text(value: Any) -> str | None:
    dt = parse_ts(value)
    if dt is None:
        text = str(value).strip() if value else ""
        return text or None
    return f"{dt:%a} {_clock(dt)}"


def _last_time(name: str, last: dict, track: str | None) -> str | None:
    when = _short_date(last.get("at"))
    opener = f"Last time ({when}) {name}" if when else f"Last time {name}"
    track = last.get("track") or track
    if track == "quote":
        money = _money(last.get("amount_usd"))
        if money is None:
            return None
        unit = str(last.get("unit") or "").strip()
        return f"{opener} quoted {money}{' ' + unit if unit else ''}."
    if track == "schedule":
        slot = _slot_text(last.get("slot_iso"))
        if slot is None:
            return None
        return f"{opener} booked {slot}." if last.get("confirmed") else f"{opener} offered {slot} but did not confirm."
    if track == "negotiate":
        action = last.get("action")
        effective = _short_date(last.get("effective_date"))
        tail = f" effective {effective}" if effective else ""
        if action == "reduced":
            saved = _money(last.get("saved_usd"))
            return f"{opener} reduced the bill by {saved}/mo{tail}." if saved else f"{opener} agreed to reduce the bill{tail}."
        if action == "canceled":
            return f"{opener} canceled the service{tail}."
        if action == "refused":
            return f"{opener} refused to lower the bill."
    return None


def _pattern(card: dict, track: str | None) -> str | None:
    n_done, n_failed = int(card.get("n_done") or 0), int(card.get("n_failed") or 0)
    n_terminal = n_done + n_failed
    if n_terminal < MIN_CALLS_FOR_PATTERN:
        return None
    if n_done == 0:
        modes = ", ".join(f"{FAILURE_LABELS.get(k, k)} {v}" for k, v in (card.get("failure_breakdown") or {}).items())
        return f"Across {n_terminal} calls they never answered ({modes})."
    answered = "answered every time" if n_done == n_terminal else f"answered {n_done} of {n_terminal}"
    track = track or card.get("track")
    detail = ""
    if track == "quote" and card.get("n_compared"):
        detail = f" and were cheapest {card.get('times_cheapest', 0)} of {card['n_compared']} times"
    elif track == "schedule" and card.get("n_with_outcome"):
        detail = f" and confirmed {card.get('n_confirmed', 0)} of {card['n_with_outcome']}"
    elif track == "negotiate" and card.get("n_with_outcome"):
        avg = _money(card.get("avg_saved_usd"))
        detail = f" and reduced the bill {card.get('n_reduced', 0)} of {card['n_with_outcome']} times"
        if avg and card.get("n_reduced"):
            detail += f", {avg}/mo on average"
    return f"Across {n_terminal} calls they {answered}{detail}."


def _voicemail_hint(card: dict) -> str | None:
    buckets = card.get("time_of_day") or {}
    for key, slot in buckets.items():
        n_calls, n_voicemail = int(slot.get("n_calls") or 0), int(slot.get("n_voicemail") or 0)
        if n_voicemail >= 2 and n_calls >= 2 and n_voicemail / n_calls >= 0.5:
            return f"They went to voicemail {n_voicemail} of {n_calls} times {_bucket_label(key)}."
    return None


def prior_context_from_insights(insights: dict | None, vendor_key: str, track: str | None = None) -> str | None:
    """One or two sentences about this vendor from the insights report, or None when there is nothing to say."""
    try:
        cards = (insights or {}).get("vendor_scorecards") or {}
        card = cards.get(vendor_key)
        if not isinstance(card, dict):
            return None
        name = str(card.get("name") or vendor_key)
        track = track or card.get("track")
        parts: list[str] = []
        last = card.get("last_outcome")
        if isinstance(last, dict):
            sentence = _last_time(name, last, track)
            if sentence:
                parts.append(sentence)
        pattern = _pattern(card, track)
        if pattern:
            parts.append(pattern)
        if len(parts) < 2:
            hint = _voicemail_hint(card)
            if hint:
                parts.append(hint)
        return " ".join(parts[:2]) or None
    except Exception:  # noqa: BLE001
        return None


def prior_context_for(
    store: Any, user_id: str, vendor_key: str, track: str | None = None, *, insights: dict | None = None
) -> str | None:
    """Convenience for the router: compute insights (or reuse `insights`) and phrase the vendor's context."""
    try:
        report = insights if insights is not None else compute_insights(store, user_id)
    except Exception:  # noqa: BLE001
        return None
    return prior_context_from_insights(report, vendor_key, track)
