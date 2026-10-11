"""Router: one plain-language request in, a capped list of CallPlan dicts out. Never raises.

plan() reads the plan cache first (scripts/preflight.py pre-seeds the demo sentence), then asks the LLM for
a roster-constrained plan, drops any vendor_key not on the user's roster, injects the cohort asks, caps at
MAX_CALLS_PER_RUN and caches the result. Any failure (no key, timeout, refusal, empty list, store error)
falls back to fallback_plans(), so a run always dispatches at least one call when the user has vendors.

The use case is specific (docs/USE-CASE.md): a Stanford student moving into an off-campus apartment for
the fall, on a student budget. The system prompt names that scenario family and asks the model to carry the
concrete details of the request (date, bedroom count, origin, destination, budget) into every objective.
fallback_plans() does the same thing without a model: parse_request_details() pulls those details out and
default_objective() writes them into a per-track sentence, saying "date to be confirmed" when there is none.

The model sees vendor keys, names and tracks. It never sees a phone number, so it cannot invent one; an
invented key fails the roster check here and would fail the vendor lookup at dispatch anyway.

Self-improvement hook: prior_context() first asks the analysis layer (analysis.insights.compute_insights +
analysis.context.prior_context_from_insights, computed from Postgres rows, with Keewano as the secondary
sink) for one or two sentences about this vendor, and falls back to the last substantive outcome with the
vendor. Insights are cached per user_id for INSIGHTS_CACHE_SECONDS; plan() warms that cache so the five
prior_context() calls that follow in the dispatch route share one computation. plan_context() exposes the
same insights so the dispatch route can log a "context_applied" event naming the vendors that received
insight-driven context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from router import llm
from router.cohorts import ask_line, cohort_for

TRACKS = ("quote", "schedule", "negotiate")
DEFAULT_CAP = 5
DEFAULT_INSIGHTS_CACHE_SECONDS = 120.0
DEFAULT_INSIGHTS_MAX_RUNS = 25

# Word-level keywords that imply a track for the no-LLM fallback. Matched on word boundaries.
TRACK_KEYWORDS: dict[str, tuple[str, ...]] = {
    "quote": ("move", "mover", "movers", "moving", "quote", "quotes", "estimate", "price"),
    "schedule": ("dentist", "dental", "cleaning", "appointment", "book", "booking", "schedule", "doctor", "checkup"),
    "negotiate": ("bill", "cancel", "retention", "internet", "wifi", "cable", "lower", "negotiate", "subscription"),
}
# Phrases that mean "do all of it": every track is implied.
ALL_TRACK_PHRASES = ("handle it", "everything", "all of it", "take care of it", "sort it out")
# Words that mean the request is about a move, which decides the quote and negotiate phrasing.
MOVE_WORDS = ("move", "moving", "mover", "movers", "relocate", "relocating", "apartment", "lease", "address")
# On the negotiate track: an existing bill, not a new account.
EXISTING_BILL_WORDS = ("lower", "reduce", "cancel", "retention", "bill", "renew", "renewal", "promo", "expiring")


class CallPlanModel(BaseModel):
    vendor_key: str
    track: Literal["quote", "schedule", "negotiate"]
    objective: str
    voice_gender: Literal["female", "male"]


class PlanList(BaseModel):
    plans: list[CallPlanModel]


SYSTEM_PROMPT = """You are the router for Dormammu, a service that places several phone calls at once on a \
student's behalf. Given one plain-language request and the user's vendor roster, decide which vendors to call \
and write one objective per call.

The scenario family: a Stanford student moving into an off-campus apartment for the fall, on a student \
budget. One sentence like "I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the \
move, internet and a dentist" fans out into: a quote from every mover on the roster for that bedroom count \
from the stated origin to the stated destination on the stated date (flat or hourly, stairs, packing extra); \
internet set up at the new address for the move-in date (current new-customer promotion, student rate, \
installation fee waived, no contract, confirm the install date), or, when the request is about a bill the \
student already pays, that bill lowered without a contract extension; and a new-patient dental cleaning \
inside the student's availability around the move. Smaller requests (one quote, one bill, one booking) follow \
the same rules with fewer calls.

Rules:
- Only use vendor_key values from the roster, exactly as written. Never invent a vendor, a phone number, \
a price, or any fact about the user.
- Each vendor has a fixed track: quote (get a price), schedule (book a time), negotiate (set up or lower a \
bill). Keep the vendor's track.
- Carry every concrete detail from the request into each objective it applies to: the move date, the \
bedroom count, the origin and the destination when present, the budget. If the request gives no date, \
write "date to be confirmed". Never add a detail the request does not contain.
- quote objectives name the job and the route: "a 1-bedroom from Stanford to Palo Alto on October 25", then \
ask for the all-in price, flat or hourly, whether stairs or a long carry add anything, whether packing is \
extra, and which dates are open.
- negotiate objectives say which it is: new service at the new address (ask for the current new-customer \
promotion, the student rate, the installation fee waived, no contract, and confirm the install date) or an \
existing bill to lower (current promotion or retention offer, no contract extension). Say "cancellation is \
authorized" only if the request says so.
- schedule objectives name the appointment type ("new-patient dental cleaning") and ask for the earliest \
slot inside the student's availability, around the move date when one is given.
- A move touches several vendors. Include every vendor on the roster that plausibly helps with the request; \
leave out vendors that clearly do not. At most one call per vendor. At most {cap} calls in total.
- Each objective is one or two sentences the phone agent will act on. Do not restate the cohort asks or any \
profile detail: the router appends the cohort asks to every objective itself, and the instruction builder \
handles the rest.
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


# ---------------------------------------------------------------------------------------------
# Request details: the concrete facts a request carries, pulled out without a model.

MONTHS = {
    "jan": "January", "feb": "February", "mar": "March", "apr": "April", "may": "May", "jun": "June",
    "jul": "July", "aug": "August", "sep": "September", "sept": "September", "oct": "October",
    "nov": "November", "dec": "December",
}  # fmt: skip
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "studio": "studio"}

_MONTH_DAY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE
)
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\b", re.IGNORECASE
)
_NUMERIC_DATE = re.compile(r"\b(1[0-2]|0?[1-9])/(3[01]|[12]\d|0?[1-9])(?:/\d{2,4})?\b")
_ORDINAL_DAY = re.compile(r"\b(?:on\s+)?the\s+(\d{1,2})(st|nd|rd|th)\b", re.IGNORECASE)
_RELATIVE = re.compile(
    r"\b(next (?:month|week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday)|this "
    r"(?:weekend|week|month)|tomorrow|end of (?:the )?month|in (?:a|two|three|\d+) weeks?)\b",
    re.IGNORECASE,
)
_BEDROOMS = re.compile(r"\b(\d|one|two|three|four|five)\s*-?\s*(?:br|bed|bedroom|bdrm)s?\b", re.IGNORECASE)
_STUDIO = re.compile(r"\bstudio\b", re.IGNORECASE)
_PLACE = r"([A-Z][A-Za-z.']*(?:\s+[A-Z][A-Za-z.']*)*)"
_DESTINATION = re.compile(r"\b(?:mov(?:e|ing)|relocat(?:e|ing)|going)\s+(?:in|back|over|down|up)?\s*to\s+" + _PLACE)
_ORIGIN = re.compile(r"\bfrom\s+" + _PLACE)
_FROM_TO = re.compile(r"\bfrom\s+" + _PLACE + r"\s+to\s+" + _PLACE)
_BUDGET = re.compile(r"\$\s?(\d[\d,]*)(?:\.\d+)?|\b(\d[\d,]{2,6})\s*(?:dollars|bucks)\b", re.IGNORECASE)


def parse_request_details(request: str) -> dict:
    """{date, bedrooms, origin, destination, budget}, each a short spoken string or None. Pure."""
    text = re.sub(r"\s+", " ", request or "").strip()
    details: dict[str, str | None] = {"date": None, "bedrooms": None, "origin": None, "destination": None,
                                      "budget": None}  # fmt: skip
    if not text:
        return details

    m = _MONTH_DAY.search(text) or _DAY_MONTH.search(text)
    if m:
        month, day = (m.group(1), m.group(2)) if _MONTH_DAY.match(m.group(0)) else (m.group(2), m.group(1))
        name = MONTHS.get(month.lower()[:4]) or MONTHS.get(month.lower()[:3])
        if name and 1 <= int(day) <= 31:
            details["date"] = f"{name} {int(day)}"
    if details["date"] is None:
        m = _NUMERIC_DATE.search(text)
        if m:
            details["date"] = f"{_month_by_number(int(m.group(1)))} {int(m.group(2))}"
    if details["date"] is None:
        m = _ORDINAL_DAY.search(text)
        if m and 1 <= int(m.group(1)) <= 31:
            details["date"] = f"the {m.group(1)}{m.group(2).lower()}"
    if details["date"] is None:
        m = _RELATIVE.search(text)
        if m:
            details["date"] = m.group(1).lower()

    m = _BEDROOMS.search(text)
    if m:
        n = NUMBER_WORDS.get(m.group(1).lower(), m.group(1))
        details["bedrooms"] = f"{n}-bedroom"
    elif _STUDIO.search(text):
        details["bedrooms"] = "studio"

    m = _DESTINATION.search(text)
    if m:
        details["destination"] = m.group(1).strip(" .")
    m = _ORIGIN.search(text)
    if m and m.group(1).strip(" .") != details["destination"]:
        details["origin"] = m.group(1).strip(" .")
    if details["destination"] is None:
        m = _FROM_TO.search(text)
        if m:
            details["destination"] = m.group(2).strip(" .")

    m = _BUDGET.search(text)
    if m:
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        if raw.isdigit():
            details["budget"] = f"${int(raw):,}"
    return details


def _month_by_number(n: int) -> str:
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]  # fmt: skip
    return names[(n - 1) % 12]


def is_move_request(request: str) -> bool:
    words = set(re.findall(r"[a-z']+", (request or "").lower()))
    return bool(words & set(MOVE_WORDS))


def is_existing_bill_request(request: str) -> bool:
    words = set(re.findall(r"[a-z']+", (request or "").lower()))
    return bool(words & set(EXISTING_BILL_WORDS))


def _route_text(details: dict) -> str:
    origin, destination = details.get("origin"), details.get("destination")
    if origin and destination:
        return f" from {origin} to {destination}"
    if destination:
        return f" to {destination}"
    if origin:
        return f" from {origin}"
    return ""


def _on(date: str) -> str:
    """'on October 25' but 'next month': relative phrases take no preposition."""
    return date if date.split()[0] in ("next", "this", "tomorrow", "in", "end") else f"on {date}"


def _when_text(details: dict, *, move: bool) -> str:
    date = details.get("date")
    if date:
        return f" {_on(date)}"
    return ", date to be confirmed" if move else ""


def default_objective(vendor: dict, request: str) -> str:
    """One or two concrete sentences for a vendor given the request and the vendor's track.

    Carries the parsed date, bedroom count, origin and destination; says "date to be confirmed" when the
    request is a move with no date. Never invents a detail the request does not contain.
    """
    name = vendor.get("name") or vendor.get("key") or "the vendor"
    req = _clean_request(request)
    track = vendor.get("track")
    details = parse_request_details(request)
    move = is_move_request(request)

    if track == "quote":
        if move:
            job = f"to move a {details['bedrooms']}" if details.get("bedrooms") else "for the move"
            return (
                f"Get an all-in quote from {name} {job}{_route_text(details)}{_when_text(details, move=True)}: "
                "flat or hourly, whether stairs or a long carry add anything, whether packing is extra, and "
                "what dates they have open."
            )
        return f"Get a total price quote from {name}, including what it covers and available dates, for: {req}."

    if track == "negotiate":
        if move and not is_existing_bill_request(request):
            where = f" in {details['destination']}" if details.get("destination") else ""
            when = f" for move-in {_on(details['date'])}" if details.get("date") else ", move-in date to be confirmed"
            return (
                f"Set up internet with {name} at the client's new address{where}{when}: ask for the current "
                "new-customer promotion, the student rate, no contract, the installation fee waived, and "
                "confirm the install date."
            )
        budget = f" The client's target is under {details['budget']} a month." if details.get("budget") else ""
        return (
            f"Review the client's current bill with {name} and ask for the current promotion or retention offer: "
            f"a lower monthly rate with no contract extension.{budget}"
        )

    if track == "schedule":
        words = set(re.findall(r"[a-z']+", (request or "").lower()))
        if words & {"dentist", "dental", "cleaning", "teeth"}:
            what = "dental cleaning"
        elif words & {"doctor", "checkup", "physical", "clinic"}:
            what = "visit"
        else:
            what = "appointment"
        around = f", around the move {_on(details['date'])}" if move and details.get("date") else ""
        return (
            f"Book the earliest new-patient {what} with {name} that fits the client's availability{around}, "
            "and get the self-pay price if insurance isn't accepted."
        )
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
    """Append the track-specific 'Ask for: ...' to every objective, once."""
    out = []
    for p in plans:
        objective = (p.get("objective") or "").rstrip()
        line = ask_line(cohort, p.get("track"))
        if line and "Ask for:" not in objective:
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
    asks = "; ".join(
        f"{track}: {', '.join(v)}" for track, v in (cohort.get("asks_by_track") or {}).items() if v
    ) or ", ".join(cohort.get("asks") or []) or "(none)"
    details = parse_request_details(request)
    found = ", ".join(f"{k} {v}" for k, v in details.items() if v) or "none parsed; carry what the request says"
    return (
        f"Request: {request.strip()}\n"
        f"Today: {today}\n"
        f"Details found in the request: {found}\n"
        f"Cohort: {cohort.get('label', 'unknown')} ({cohort.get('proof', 'no proof listed')}); asks by track: {asks}\n"
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


def _safe(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


async def plan(store: Any, request: str, user_id: str, *, refresh: bool = False) -> list[dict]:
    """CallPlan dicts for this request, 1..MAX_CALLS_PER_RUN when the user has vendors. Never raises.

    refresh=True skips the cache read and overwrites the cached plan (preflight uses it after roster edits).
    Also warms the per-user insights cache once, so the prior_context() calls the dispatch route makes next
    (one per planned call) share a single compute_insights() pass.
    """
    try:
        _safe(_insights_for, store, user_id, refresh=True)
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


# ---------------------------------------------------------------------------------------------
# Self-improvement hook: insights from the analysis layer, cached per user, with a plain fallback.

_insights_cache: dict[str, tuple[float, dict]] = {}


def _insights_ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("INSIGHTS_CACHE_SECONDS") or DEFAULT_INSIGHTS_CACHE_SECONDS))
    except ValueError:
        return DEFAULT_INSIGHTS_CACHE_SECONDS


def _insights_max_runs() -> int:
    try:
        return max(1, int(os.environ.get("INSIGHTS_MAX_RUNS") or DEFAULT_INSIGHTS_MAX_RUNS))
    except ValueError:
        return DEFAULT_INSIGHTS_MAX_RUNS


def _analysis_funcs() -> tuple[Any, Any] | None:
    """(compute_insights, prior_context_from_insights) from the analysis layer, or None when it is absent."""
    try:
        from analysis.context import prior_context_from_insights
        from analysis.insights import compute_insights
    except ImportError:
        return None
    except Exception:  # noqa: BLE001
        return None
    return compute_insights, prior_context_from_insights


def clear_context_cache() -> None:
    """Forget cached insights (tests, and preflight after seeding)."""
    _insights_cache.clear()


def _insights_for(store: Any, user_id: str, *, refresh: bool = False) -> dict:
    """compute_insights(store, user_id) for this user, cached for INSIGHTS_CACHE_SECONDS. {} when unavailable."""
    funcs = _analysis_funcs()
    if funcs is None:
        return {}
    now = time.monotonic()
    if not refresh:
        hit = _insights_cache.get(user_id)
        if hit is not None and now - hit[0] < _insights_ttl():
            return hit[1]
    compute_insights = funcs[0]
    try:
        try:
            insights = compute_insights(store, user_id, max_runs=_insights_max_runs())
        except TypeError:
            insights = compute_insights(store, user_id)
    except Exception:  # noqa: BLE001
        insights = {}
    if not isinstance(insights, dict):
        insights = {}
    _insights_cache[user_id] = (now, insights)
    return insights


def _insight_sentence(insights: dict, vendor_key: str, track: str) -> str | None:
    funcs = _analysis_funcs()
    if funcs is None or not insights:
        return None
    try:
        sentence = funcs[1](insights, vendor_key, track)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(sentence, str) and sentence.strip():
        return sentence.strip()
    return None


def plan_context(store: Any, user_id: str, *, refresh: bool = False) -> dict:
    """The insights behind this user's next run, for the dispatch route to log as a "context_applied" event.

    Returns {"source": "analysis"|"none", "insights": dict ({} when the analysis layer is missing or failed),
    "context": {vendor_key: sentence} for every roster vendor the insights can speak to,
    "vendors_with_insight": [vendor_key, ...], "n_recommendations": int, "generated_at": str|None}.
    `context` and `vendors_with_insight` are the small, loggable part; `insights` is the full report for a
    page. Never raises.
    """
    out: dict[str, Any] = {
        "source": "none",
        "insights": {},
        "context": {},
        "vendors_with_insight": [],
        "n_recommendations": 0,
        "generated_at": None,
    }
    try:
        insights = _insights_for(store, user_id, refresh=refresh)
        if not insights:
            return out
        out["source"] = "analysis"
        out["insights"] = insights
        out["generated_at"] = insights.get("generated_at")
        recs = insights.get("recommendations")
        out["n_recommendations"] = len(recs) if isinstance(recs, list) else 0
        vendors = _safe(store.list_vendors, user_id) or []
        for v in vendors:
            if not isinstance(v, dict) or not v.get("key"):
                continue
            sentence = _insight_sentence(insights, v["key"], v.get("track") or "quote")
            if sentence:
                out["context"][v["key"]] = sentence
        out["vendors_with_insight"] = sorted(out["context"])
    except Exception:  # noqa: BLE001, S110
        pass
    return out


# ---------------------------------------------------------------------------------------------
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


def last_outcome_context(store: Any, user_id: str, vendor_key: str, track: str) -> str | None:
    """One sentence from the most recent substantive outcome with this vendor, or None. The plain fallback."""
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


def prior_context_detail(store: Any, user_id: str, vendor_key: str, track: str) -> dict:
    """{"text": str|None, "source": "insights"|"last_outcome"|"none"}. Insights first, then the last outcome."""
    try:
        insights = _insights_for(store, user_id)
        sentence = _insight_sentence(insights, vendor_key, track)
        if sentence:
            return {"text": sentence, "source": "insights"}
    except Exception:  # noqa: BLE001, S110
        pass
    sentence = last_outcome_context(store, user_id, vendor_key, track)
    if sentence:
        return {"text": sentence, "source": "last_outcome"}
    return {"text": None, "source": "none"}


def prior_context(store: Any, user_id: str, vendor_key: str, track: str) -> str | None:
    """The sentence the agent opens with about last time, or None. Contract signature; never raises."""
    try:
        return prior_context_detail(store, user_id, vendor_key, track)["text"]
    except Exception:  # noqa: BLE001
        return None
