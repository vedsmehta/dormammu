"""Router: one plain-language request in, a capped list of CallPlan dicts out. Never raises.

plan() reads the plan cache first (scripts/preflight.py pre-seeds the demo sentence), then asks the LLM for
a roster-constrained plan, drops any vendor_key not on the user's roster, injects the cohort asks, caps at
MAX_CALLS_PER_RUN and caches the result. Any failure (no key, timeout, refusal, empty list, store error)
falls back to fallback_plans(), so a run always dispatches at least one call when the user has vendors.

The model sees vendor keys, names and tracks. It never sees a phone number, so it cannot invent one; an
invented key fails the roster check here and would fail the vendor lookup at dispatch anyway.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from router import llm
from router.cohorts import cohort_for

TRACKS = ("quote", "schedule", "negotiate")
DEFAULT_CAP = 5

# Word-level keywords that imply a track for the no-LLM fallback. Matched on word boundaries.
TRACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "quote": ("move", "mover", "movers", "moving", "quote", "quotes", "estimate", "price"),
    "schedule": ("dentist", "dental", "appointment", "book", "booking", "schedule", "doctor", "checkup"),
    "negotiate": ("bill", "cancel", "retention", "internet", "cable", "lower", "negotiate", "subscription"),
}
# Phrases that mean "do all of it": every track is implied.
ALL_TRACK_PHRASES = ("handle it", "everything", "all of it", "take care of it", "sort it out")


class CallPlanModel(BaseModel):
    vendor_key: str
    track: Literal["quote", "schedule", "negotiate"]
    objective: str
    voice_gender: Literal["female", "male"]


class PlanList(BaseModel):
    plans: list[CallPlanModel]


SYSTEM_PROMPT = """You are the router for Dormammu, a service that places several phone calls at once on a \
user's behalf. Given one plain-language request and the user's vendor roster, decide which vendors to call \
and write one objective per call.

Rules:
- Only use vendor_key values from the roster, exactly as written. Never invent a vendor, a phone number, \
a price, or any fact about the user.
- Each vendor has a fixed track: quote (get a price), schedule (book a time), negotiate (lower or cancel a \
bill). Keep the vendor's track.
- A life event usually touches several vendors. Moving, for example, means price quotes from movers, \
setting up or renegotiating internet or utilities, and booking appointments around the move. Include every \
vendor on the roster that plausibly helps with the request; leave out vendors that clearly do not.
- At most one call per vendor. At most {cap} calls in total.
- The objective is one or two sentences the phone agent will act on: what to ask for and what to confirm, \
written for that vendor's track. Carry timing from the request (for example "next month") when relevant.
- Keep the objective about the task. Do not restate the cohort asks, the budget, or any profile detail: the \
router appends the cohort asks to every objective itself, and the instruction builder handles the rest.
- voice_gender is "female" or "male"; vary it across the calls.
Return the plan list only."""


def request_hash(request: str) -> str:
    """sha256 of the trimmed, lower-cased request. The plan cache key."""
    return hashlib.sha256(request.strip().lower().encode()).hexdigest()


def max_calls() -> int:
    """MAX_CALLS_PER_RUN from env, read at call time so tests and preflight can change it."""
    try:
        return max(1, int(os.environ.get("MAX_CALLS_PER_RUN") or DEFAULT_CAP))
    except ValueError:
        return DEFAULT_CAP


def implied_tracks(request: str) -> set[str]:
    """Tracks the request implies by keyword. Empty set means nothing specific."""
    text = (request or "").lower()
    if any(phrase in text for phrase in ALL_TRACK_PHRASES):
        return set(TRACKS)
    words = set(re.findall(r"[a-z']+", text))
    return {track for track, keywords in TRACK_KEYWORDS.items() if words & set(keywords)}


def _clean_request(request: str) -> str:
    text = re.sub(r"\s+", " ", request or "").strip().rstrip(".!")
    return text[:200] if text else "the client's request"


def default_objective(vendor: dict, request: str) -> str:
    """One-sentence task for a vendor given the request and the vendor's track."""
    name = vendor.get("name") or vendor.get("key") or "the vendor"
    req = _clean_request(request)
    track = vendor.get("track")
    if track == "quote":
        return f"Get a total price quote from {name}, including what it covers and available dates, for: {req}."
    if track == "schedule":
        return f"Book the earliest appointment with {name} that fits the client's availability, for: {req}."
    if track == "negotiate":
        return f"Review the client's bill with {name} and ask for the current promotion or retention offer: {req}."
    return f"Call {name} about: {req}."


def _plan_row(vendor: dict, objective: str, voice_gender: str = "female") -> dict:
    return {
        "vendor_key": vendor["key"],
        "track": vendor.get("track") if vendor.get("track") in TRACKS else "quote",
        "objective": objective,
        "voice_gender": voice_gender if voice_gender in ("female", "male") else "female",
    }


def fallback_plans(vendors: list[dict], request: str) -> list[dict]:
    """Default roster for the tracks the request implies; all vendors if it implies nothing. Capped."""
    vendors = [v for v in (vendors or []) if v.get("key")]
    tracks = implied_tracks(request)
    chosen = [v for v in vendors if v.get("track") in tracks] if tracks else list(vendors)
    if not chosen:
        chosen = list(vendors)
    genders = ("female", "male")
    return [_plan_row(v, default_objective(v, request), genders[i % 2]) for i, v in enumerate(chosen[: max_calls()])]


def with_cohort_asks(plans: list[dict], cohort: dict) -> list[dict]:
    """Append 'Ask for: ...' to every objective, once."""
    asks = [a for a in (cohort.get("asks") or []) if a]
    if not asks:
        return [dict(p) for p in plans]
    line = "Ask for: " + ", ".join(asks) + "."
    out = []
    for p in plans:
        objective = (p.get("objective") or "").rstrip()
        if line not in objective:
            sep = " " if objective.endswith((".", "!", "?")) or not objective else ". "
            objective = f"{objective}{sep}{line}".strip()
        out.append({**p, "objective": objective})
    return out


def _roster_text(vendors: list[dict]) -> str:
    return "\n".join(f"- {v['key']} | {v.get('name') or v['key']} | {v.get('track') or 'quote'}" for v in vendors)


def _profile_text(profile: dict | None) -> str:
    """Only the display name reaches the router. Phone and prefs stay out of the planning prompt."""
    name = (profile or {}).get("display_name")
    return f"Client: {name}" if name else "Client: (no name on file)"


def _user_prompt(request: str, vendors: list[dict], profile: dict | None, cohort: dict) -> str:
    now = datetime.now().astimezone()
    today = f"{now:%A, %B} {now.day}, {now.year}"
    asks = ", ".join(cohort.get("asks") or []) or "(none)"
    return (
        f"Request: {request.strip()}\n"
        f"Today: {today}\n"
        f"Cohort: {cohort.get('label', 'unknown')} ({cohort.get('proof', 'no proof listed')}); asks: {asks}\n"
        f"{_profile_text(profile)}\n"
        f"Vendor roster (key | name | track):\n{_roster_text(vendors)}"
    )


def _validate(result: PlanList | None, vendors: list[dict], request: str) -> list[dict]:
    """Keep plans whose vendor_key is on the roster, one per vendor, roster track wins. Capped."""
    roster = {v["key"]: v for v in vendors}
    out: list[dict] = []
    seen: set[str] = set()
    for p in (result.plans if result is not None else []):
        vendor = roster.get(p.vendor_key)
        if vendor is None or p.vendor_key in seen:
            continue
        seen.add(p.vendor_key)
        objective = (p.objective or "").strip() or default_objective(vendor, request)
        out.append(_plan_row(vendor, objective, p.voice_gender))
    return out[: max_calls()]


async def _ask_llm(request: str, vendors: list[dict], profile: dict | None, cohort: dict) -> list[dict]:
    try:
        system = SYSTEM_PROMPT.format(cap=max_calls())
        result = await llm.structured(system, _user_prompt(request, vendors, profile, cohort), PlanList)
        return _validate(result, vendors, request)
    except Exception:  # noqa: BLE001
        return []


def _safe(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        return None


async def plan(store: Any, request: str, user_id: str, *, refresh: bool = False) -> list[dict]:
    """CallPlan dicts for this request, 1..MAX_CALLS_PER_RUN when the user has vendors. Never raises.

    refresh=True skips the cache read and overwrites the cached plan (preflight uses it after roster edits).
    """
    try:
        key = request_hash(request)
        if not refresh:
            cached = _safe(store.get_plan_cache, key)
            if cached:
                return [dict(p) for p in cached][: max_calls()]
        vendors = [v for v in (store.list_vendors(user_id) or []) if v.get("key")]
        if not vendors:
            return []
        profile = _safe(store.get_profile, user_id)
        cohort = cohort_for(profile if isinstance(profile, dict) else None)
        plans = await _ask_llm(request, vendors, profile, cohort)
        if not plans:
            plans = fallback_plans(vendors, request)
        plans = with_cohort_asks(plans, cohort)[: max_calls()]
        _safe(store.put_plan_cache, key, plans)
        return plans
    except Exception:  # noqa: BLE001
        try:
            return with_cohort_asks(fallback_plans(store.list_vendors(user_id), request), cohort_for(None))
        except Exception:  # noqa: BLE001
            return []


# ---------------------------------------------------------------------------
# Prior context: what we learned last time, as one spoken sentence.


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


def _money(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _short_date(value: Any) -> str | None:
    dt = _parse_dt(value)
    if dt is None:
        text = str(value).strip() if value else ""
        return text or None
    return f"{dt:%b} {dt.day}"


def _clock(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour}:{dt.minute:02d}{suffix}"


def _slot_text(value: Any) -> str | None:
    dt = _parse_dt(value)
    if dt is None:
        text = str(value).strip() if value else ""
        return text or None
    return f"{dt:%a} {_clock(dt)}"


def _outcome_dict(row: dict) -> dict | None:
    outcome = row.get("outcome")
    if isinstance(outcome, str):
        try:
            outcome = json.loads(outcome)
        except ValueError:
            return None
    return outcome if isinstance(outcome, dict) else None


def _sentence(row: dict, outcome: dict, track: str) -> str | None:
    name = row.get("target_name") or row.get("vendor_key") or "they"
    when = _short_date(row.get("ended_at") or row.get("started_at"))
    opener = f"Last time ({when}) {name}" if when else f"Last time {name}"
    if track == "quote":
        money = _money(outcome.get("amount_usd"))
        if money is None:
            return None
        unit = (outcome.get("unit") or "").strip()
        return f"{opener} quoted {money}{' ' + unit if unit else ''}."
    if track == "schedule":
        slot = _slot_text(outcome.get("slot_iso"))
        if slot is None:
            return None
        if outcome.get("confirmed"):
            return f"{opener} booked {slot}."
        return f"{opener} offered {slot} but did not confirm."
    if track == "negotiate":
        action = outcome.get("action")
        effective = _short_date(outcome.get("effective_date"))
        tail = f" effective {effective}" if effective else ""
        if action == "reduced":
            saved = _money(outcome.get("saved_usd"))
            if saved:
                return f"{opener} reduced the bill by {saved}/mo{tail}."
            return f"{opener} agreed to reduce the bill{tail}."
        if action == "canceled":
            return f"{opener} canceled the service{tail}."
        if action == "refused":
            return f"{opener} refused to lower the bill."
    return None


def prior_context(store: Any, user_id: str, vendor_key: str, track: str) -> str | None:
    """One sentence from the most recent substantive outcome with this vendor, or None."""
    try:
        rows = store.last_outcomes_for_vendor(user_id, vendor_key, limit=3) or []
    except Exception:  # noqa: BLE001
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        outcome = _outcome_dict(row)
        if not outcome:
            continue
        try:
            sentence = _sentence(row, outcome, outcome.get("track") or row.get("track") or track)
        except Exception:  # noqa: BLE001
            sentence = None
        if sentence:
            return sentence
    return None
