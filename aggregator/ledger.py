"""Ledger rules: turn a finished call's outcome into dated ledger rows. Pure, null-safe, never raises.

Rows carry vendor_id and source_call from the call row and no id/user_id (the caller fills user_id).
Dates are ISO strings (YYYY-MM-DD). Assumptions are stated in the label so they are visible on screen:
quotes are assumed valid 30 days; promos are assumed to run 11 months unless the call text names a
duration, in which case the expiry is that duration minus one month (the act-two trigger lands before
the promo ends).
"""

from __future__ import annotations

import calendar
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

QUOTE_VALIDITY_DAYS = 30
DEFAULT_PROMO_MONTHS = 11

_DURATION_RE = re.compile(r"\b(\d{1,2})[\s-]*(?:months?|mos?\b\.?)", re.IGNORECASE)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number <= 0:  # non-positive: not a fact worth dating
        return None
    return number


def _money(amount: float) -> str:
    return f"{int(amount)}" if float(amount).is_integer() else f"{amount:.2f}"


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def add_months(day: date, months: int) -> date:
    """Calendar month arithmetic with day clamping (Mar 31 + 11 months -> Feb 28)."""
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def _target_name(call_row: dict) -> str:
    return str(call_row.get("target_name") or call_row.get("vendor_key") or "Vendor")


def _base(call_row: dict, kind: str, label: str, amount: float | None, start: date, end: date) -> dict:
    return {
        "vendor_id": call_row.get("vendor_id"),
        "kind": kind,
        "label": label,
        "amount_usd": amount,
        "effective_on": start.isoformat(),
        "expires_on": end.isoformat(),
        "source_call": call_row.get("id"),
    }


def _call_text(call_row: dict, outcome: dict) -> str:
    parts: list[str] = []
    caveats = outcome.get("caveats")
    if isinstance(caveats, list):
        parts.extend(str(c) for c in caveats if c)
    if outcome.get("note"):
        parts.append(str(outcome["note"]))
    transcript = call_row.get("transcript")
    if isinstance(transcript, str):
        parts.append(transcript)
    turns = call_row.get("transcript_turns")
    if isinstance(turns, list):
        parts.extend(str(t.get("text") or "") for t in turns if isinstance(t, dict))
    return " ".join(parts)


def stated_promo_months(text: str) -> int | None:
    """First duration like '12 months', '6-month' or '3 mos' in the text, else None."""
    match = _DURATION_RE.search(text or "")
    if not match:
        return None
    months = int(match.group(1))
    return months if months > 0 else None


def _quote(call_row: dict, outcome: dict, today: date) -> dict | None:
    amount = _num(outcome.get("amount_usd"))
    if amount is None:
        return None
    unit = outcome.get("unit")
    label = f"{_target_name(call_row)} quote ${_money(amount)}"
    if unit:
        label += f" {unit}"
    label += f" ({QUOTE_VALIDITY_DAYS}-day validity assumed)"
    return _base(call_row, "quote", label, amount, today, today + timedelta(days=QUOTE_VALIDITY_DAYS))


def _schedule(call_row: dict, outcome: dict, today: date) -> dict | None:
    if not outcome.get("confirmed"):
        return None
    slot = _parse_date(outcome.get("slot_iso"))
    if slot is None:
        return None
    return _base(call_row, "appointment", f"{_target_name(call_row)} appointment", None, slot, slot)


def _negotiate(call_row: dict, outcome: dict, today: date) -> dict | None:
    if outcome.get("action") not in ("reduced", "canceled"):
        return None
    saved = _num(outcome.get("saved_usd"))
    if saved is None:
        return None
    start = _parse_date(outcome.get("effective_date")) or today
    stated = stated_promo_months(_call_text(call_row, outcome))
    if stated is None:
        months, suffix = DEFAULT_PROMO_MONTHS, f"({DEFAULT_PROMO_MONTHS}-month promo assumed)"
    else:
        months, suffix = max(stated - 1, 0), f"({stated}-month promo stated on call)"
    label = f"{_target_name(call_row)} promo rate ${_money(saved)}/mo off {suffix}"
    return _base(call_row, "promo_expiry", label, saved, start, add_months(start, months))


_BUILDERS = {"quote": _quote, "schedule": _schedule, "negotiate": _negotiate}


def ledger_rows(call_row: dict, today: date | None = None) -> list[dict]:
    """Dated facts from call_row.outcome as ledger rows (no id/user_id). Missing amounts -> no row."""
    today = today or date.today()
    if not isinstance(call_row, dict):
        return []
    outcome = call_row.get("outcome")
    if not isinstance(outcome, dict):
        return []
    builder = _BUILDERS.get(outcome.get("track") or call_row.get("track"))
    if builder is None:
        return []
    try:
        row = builder(call_row, outcome, today)
    except Exception:  # noqa: BLE001
        return []
    return [row] if row else []
