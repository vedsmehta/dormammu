"""The research engine: profile + request + roster in, one proposal out. Async, never raises.

What it does, in order (docs/RESEARCH.md):

1. Understand the user. `details` is router.plan.parse_request_details(request) merged with the profile prefs
   the onboarding form captures (new_address, move_date, bedrooms, budget_ceiling_usd); the request wins
   when both speak.
2. Pick categories. Keyword families in the request (movers, internet, dentist, ...) plus one retention
   category per profile.current_providers entry ("Lower Comcast bill", or "Cancel Comcast" when the request
   says cancel and names it).
3. Search, concurrently. One Responses API call per category with the hosted web_search tool and a strict
   pydantic text_format (search_structured). At most MAX_CONCURRENT_SEARCHES at a time, QUERY_TIMEOUT_SECONDS
   each, OVERALL_BUDGET_SECONDS for the whole run via asyncio.wait_for; a timeout returns what finished.
4. Verify. A phone number is kept only when the same digits appear in a cited page fetched with httpx
   (research.phones). Every plan and discount must point at a citation (a URL the API cited, or one we could
   fetch), else it is dropped. Roster vendors are included as items with phone_verified true.
4b. Second pass (_second_pass). The product never asks the user for a vendor's number, so what the first pass
   could not prove is searched for again: one targeted "<vendor> <city> phone number" search per unverified
   item (at most SECOND_PASS_MAX_SEARCHES model calls in the pass, roster-less categories with fewer than two
   verified items first), verified the same way; then the vendor's own site (root plus up to MAX_SITE_PATHS
   contact pages) read for a tel: link or a single written number, which counts as verified because it is the
   vendor's own page (phone_source "own_site"); then one "<vendor> prices plans student discount <city>" search
   for items with no plans and no discounts, cited or dropped. Capped at SECOND_PASS_SECONDS out of the
   OVERALL_BUDGET_SECONDS. What it did is reported in proposal["second_pass"].
5. Strategize. Negotiate items get their variant from router.negotiation.choose_strategy; quote and schedule
   items get "quote" / "schedule". Asks are router.cohorts.cohort_asks for the track plus item-specific asks.
6. Check regulations (research.regulations.check) for the destination state, concurrently with the searches.

Nothing here dials. The proposal is what the user approves before a run is created.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

from research import phones
from research.schemas import (
    CategoryFindings,
    InfoLookup,
    PhoneLookup,
    Proposal,
    SourcedDiscount,
    SourcedPlan,
    VendorFinding,
)
from router import llm
from router.cohorts import cohort_asks, cohort_for
from router.negotiation import STRATEGIES, choose_strategy
from router.plan import is_existing_bill_request, is_move_request, parse_request_details

OVERALL_BUDGET_SECONDS = 75.0  # first pass plus second pass; the proposals route awaits the whole thing
SECOND_PASS_SECONDS = 20.0  # reserved out of OVERALL_BUDGET_SECONDS for the second pass
SECOND_PASS_MIN_SECONDS = 1.0  # less than this left and the second pass is skipped rather than started
SECOND_PASS_MAX_SEARCHES = 8  # model calls in the second pass: phone lookups first, price lookups with the rest
QUERY_TIMEOUT_SECONDS = 25.0
MAX_CONCURRENT_SEARCHES = 5
MAX_FETCH_PER_ITEM = 3
MAX_TOOL_CALLS_PER_SEARCH = 4
SEARCH_CONTEXT_SIZE = "low"
SITE_CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/locations")
MAX_SITE_PATHS = 3  # contact pages read on the vendor's own site, besides the root
NO_NUMBER_REASON = "no verified number after searching sources and the vendor's site"

ALL_PHRASES = ("handle it", "everything", "all of it", "take care of it", "sort it out")
CANCEL_WORDS = ("cancel", "cancelling", "canceling", "cancellation", "terminate", "end my", "stop my")

# A hint only, used when the request names a city and the profile has no state. Never asserted as a fact.
STATE_HINTS = {
    "palo alto": "CA", "stanford": "CA", "menlo park": "CA", "mountain view": "CA", "san jose": "CA",
    "san francisco": "CA", "berkeley": "CA", "oakland": "CA", "redwood city": "CA", "sunnyvale": "CA",
    "east palo alto": "CA", "los altos": "CA", "santa clara": "CA", "cupertino": "CA",
}  # fmt: skip

# key -> how to recognize it in the request, which track it runs on, and how to search for it.
# Placeholders: {city} {state} {zip} {date} {bedrooms} {place} ("Palo Alto, CA 94301" or what we have).
CATEGORY_FAMILIES: dict[str, dict[str, Any]] = {
    "movers": {
        "label": "Movers",
        "track": "quote",
        "words": ("move", "moving", "mover", "movers", "relocate", "relocating", "relocation"),
        "why": "The request is a move, so each mover is an all-in quote to compare.",
        "query": "licensed movers serving {city} {state} phone number {bedrooms} apartment move {date}",
        "noun": "licensed moving companies",
    },
    "internet": {
        "label": "Internet",
        "track": "negotiate",
        "words": ("internet", "wifi", "wi-fi", "broadband", "fiber"),
        "why": "Internet at the new address: new-customer promotion, student rate, no contract, install date.",
        "query": "internet providers available at {place} student plan price phone number",
        "noun": "internet service providers that serve the address",
    },
    "dentist": {
        "label": "Dentist",
        "track": "schedule",
        "words": ("dentist", "dental", "teeth", "cleaning", "orthodontist"),
        "why": "A new-patient dental cleaning near the new address, inside the client's availability.",
        "query": "dentist near {city} {state} accepting new patients phone number new patient cleaning price",
        "noun": "dental offices accepting new patients",
    },
    "doctor": {
        "label": "Doctor",
        "track": "schedule",
        "words": ("doctor", "physician", "checkup", "physical", "clinic", "pediatrician", "dermatologist"),
        "why": "A new-patient visit near the new address.",
        "query": "primary care doctor near {city} {state} accepting new patients phone number",
        "noun": "medical practices accepting new patients",
    },
    "phone": {
        "label": "Cell phone plan",
        "track": "negotiate",
        "words": ("cellphone", "cellular", "mobile", "wireless", "carrier", "verizon", "t-mobile", "att"),
        "phrases": ("phone plan", "phone bill", "cell phone"),
        "why": "A cheaper student cell plan, or the current bill lowered.",
        "query": "cheapest student cell phone plan {state} carrier customer service phone number",
        "noun": "wireless carriers with student plans",
    },
    "gym": {
        "label": "Gym",
        "track": "quote",
        "words": ("gym", "fitness", "membership", "climbing", "yoga"),
        "why": "A student gym membership near the new address: monthly price, joining fee, month-to-month.",
        "query": "gym near {city} {state} student membership price phone number",
        "noun": "gyms with student memberships",
    },
    "insurance": {
        "label": "Renters insurance",
        "track": "quote",
        "words": ("insurance", "renters", "renter's", "insured"),
        "why": "Renters insurance for the new apartment, priced for a student.",
        "query": "renters insurance {city} {state} student quote phone number",
        "noun": "insurers that write renters policies in the state",
    },
    "utilities": {
        "label": "Utilities",
        "track": "schedule",
        "words": ("utilities", "utility", "electric", "electricity", "gas", "water", "pg&e", "pge", "power"),
        "why": "Electric, gas and water started at the new address for the move-in date.",
        "query": "{city} {state} electric gas water utility start service new customer phone number",
        "noun": "the utilities that serve the address",
    },
    "storage": {
        "label": "Storage",
        "track": "quote",
        "words": ("storage", "locker", "self-storage"),
        "why": "A small storage unit near the new address with a student discount if there is one.",
        "query": "self storage units near {city} {state} student discount price phone number",
        "noun": "self-storage facilities",
    },
}
MOVE_DEFAULTS = ("movers", "internet", "utilities")

SYSTEM_PROMPT = """You are the research analyst for Dormammu, a service that phones vendors on behalf of a \
student who is moving. Your job is to find real vendors with real phone numbers and real current prices, \
grounded in web sources, for one category at a time. A phone agent will act on what you return.

Hard rules:
- Use web search. Open the vendor's own page or a directory page before reporting a vendor.
- Report only what appears in a source you opened. Never guess, infer or "recall" a phone number, a price or \
a discount. If a source does not show a phone number, set phone_as_written to null.
- Copy phone_as_written exactly as the source writes it and set phone_source_url to that page.
- Every plan and every discount needs the source_url of the page that states it. Leave out any you cannot \
point to a page for. price_usd is a number in US dollars; unit is one of "per month", "per hour", "flat", \
"per visit", "per year" or null.
- "why" is one plain sentence a student can read, grounded in the sources (serves the area, a price, a rating).
- "opening" is the one sentence the phone agent will say right after introducing itself: plain spoken \
English, one question, mentioning one concrete thing you found (a plan name, a price or a promotion). \
Never promise or claim anything the client has not asked for.
- "item_asks" are up to three short, specific things to ask this vendor based on what you found \
(for example "whether the $35 Connect plan is available at the address").
- "confidence" from 0 to 1: how sure you are this vendor serves the destination and the phone is current.
- "source_urls" lists every URL you used for this vendor. Cite sources inline in any prose.
- Prefer vendors that serve the destination address. Skip aggregators that hide the vendor's own number.
Return the findings only."""

LOOKUP_SYSTEM_PROMPT = """You are the research analyst for Dormammu, a service that phones vendors on behalf of a \
student who is moving. An earlier search found one vendor but not its phone number, or not its prices. Your job \
is to find exactly that for this one vendor, grounded in a page you opened. A phone agent will act on it.

Hard rules:
- Use web search. Open the vendor's own website, or a directory listing for it (its Google Business profile, \
Yelp, Healthgrades, the BBB, a state licensing board), and read the page.
- Report only what appears in a page you opened. Never guess, infer or "recall" a phone number, a price or a \
discount. If no page you opened shows a phone number, set phone_as_written to null.
- Copy phone_as_written exactly as the page writes it and set phone_source_url to that page's URL.
- Make sure the page is about this vendor at this location, not a namesake somewhere else.
- "website" is the vendor's own site when you found it, else null. "address" likewise.
- Every plan and every discount needs the source_url of the page that states it. Leave out any you cannot \
point to a page for. price_usd is a number in US dollars; unit is one of "per month", "per hour", "flat", \
"per visit", "per year" or null.
- "confidence" from 0 to 1: how sure you are this is the right vendor and the number is current.
- "source_urls" lists every URL you used.
Return the lookup only."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


_DASHES = re.compile(r"\s*[–—]\s*")
_GREETING = re.compile(r"^(?:hi|hello|hey|good (?:morning|afternoon|evening))[,!.]?\s+", re.IGNORECASE)


def _text(value: Any) -> str:
    """A safe string: None and non-strings never leak, and model prose loses its en and em dashes."""
    if value is None:
        return ""
    text = _DASHES.sub(", ", str(value)).strip()
    return "" if text.lower() in ("none", "null", "undefined") else text


def _spoken(value: Any) -> str:
    """Model prose the agent will say: no greeting (the fixed opener already said hi), one line."""
    text = re.sub(r"\s+", " ", _text(value))
    text = _GREETING.sub("", text)
    return text[:1].upper() + text[1:] if text else ""


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9&'\-]+", (text or "").lower()))


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return slug[:40] or "vendor"


def _money(value: Any) -> str | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def _spoken_date(value: Any) -> str | None:
    """'2026-10-25' -> 'October 25'; anything unparseable passes through as text."""
    text = _text(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text[:10])
    except ValueError:
        return text
    return f"{dt:%B} {dt.day}"


def _on(date: str | None) -> str:
    if not date:
        return ""
    return f" {date}" if date.split()[0].lower() in ("next", "this", "tomorrow", "in", "end") else f" on {date}"


def _prefs(profile: dict | None) -> dict:
    prefs = (profile or {}).get("prefs") if isinstance(profile, dict) else None
    return prefs if isinstance(prefs, dict) else {}


# ---------------------------------------------------------------------------------------------
# Step 1: details


def merge_details(request: str, profile: dict | None) -> dict:
    """parse_request_details(request) plus the profile prefs; the request wins where both speak. Pure."""
    details: dict[str, Any] = dict(parse_request_details(request))
    prefs = _prefs(profile)
    new_address = prefs.get("new_address") if isinstance(prefs.get("new_address"), dict) else {}
    current_address = prefs.get("current_address") if isinstance(prefs.get("current_address"), dict) else {}

    if not details.get("destination") and _text(new_address.get("city")):
        details["destination"] = _text(new_address.get("city"))
    if not details.get("origin") and _text(current_address.get("city")):
        details["origin"] = _text(current_address.get("city"))
    if not details.get("date") and _text(prefs.get("move_date")):
        details["date"] = _spoken_date(prefs.get("move_date"))
    bedrooms = _text(prefs.get("bedrooms"))
    if not details.get("bedrooms") and bedrooms:
        details["bedrooms"] = "studio" if bedrooms == "studio" else f"{bedrooms}-bedroom"
    if not details.get("budget") and _money(prefs.get("budget_ceiling_usd")):
        details["budget"] = _money(prefs.get("budget_ceiling_usd"))

    state = _text(new_address.get("state")).upper() or None
    if not state and details.get("destination"):
        state = STATE_HINTS.get(str(details["destination"]).lower())
    details["state"] = state
    details["zip"] = _text(new_address.get("zip")) or None
    details["new_address"] = {k: _text(new_address.get(k)) or None for k in ("street", "city", "state", "zip")}
    details["move_date"] = _text(prefs.get("move_date")) or None
    details["budget_ceiling_usd"] = prefs.get("budget_ceiling_usd")
    details["household_size"] = prefs.get("household_size")
    details["insurance"] = prefs.get("insurance") if isinstance(prefs.get("insurance"), dict) else None
    details["student_proof"] = _text(prefs.get("student_proof")) or None
    details["is_move"] = is_move_request(request) or bool(details["move_date"])
    return details


def _place(details: dict) -> str:
    bits = [b for b in (details.get("destination"), details.get("state")) if b]
    place = ", ".join(bits) if bits else ""
    if details.get("zip"):
        place = f"{place} {details['zip']}".strip()
    return place or "the new address"


def _fill(template: str, details: dict) -> str:
    text = template.format(
        city=details.get("destination") or "",
        state=details.get("state") or "",
        zip=details.get("zip") or "",
        date=details.get("date") or "",
        bedrooms=details.get("bedrooms") or "",
        place=_place(details),
    )
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------------------------
# Step 2: categories


def _mentions_cancel(request: str) -> bool:
    text = (request or "").lower()
    return any(w in text for w in CANCEL_WORDS)


def _provider_named(request: str, provider: dict) -> bool:
    words = _words(request)
    name_words = _words(_text(provider.get("name")))
    category = _text(provider.get("category")).lower()
    return bool(words & name_words) or (category != "" and category in words)


def select_categories(request: str, profile: dict | None, details: dict) -> list[dict]:
    """Category dicts {key, label, track, why, source, query, provider?} from the request and the profile."""
    text = (request or "").lower()
    words = _words(text)
    out: list[dict] = []
    seen: set[str] = set()

    def add(key: str, fam: dict, why: str | None = None, source: str = "request") -> None:
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "key": key,
                "label": fam["label"],
                "track": fam["track"],
                "why": why or fam["why"],
                "source": source,
                "query": _fill(fam["query"], details),
            }
        )

    for key, fam in CATEGORY_FAMILIES.items():
        hit = bool(words & set(fam["words"])) or any(p in text for p in fam.get("phrases", ()))
        if hit:
            add(key, fam)
    if details.get("is_move") and any(p in text for p in ALL_PHRASES):
        for key in MOVE_DEFAULTS:
            add(key, CATEGORY_FAMILIES[key], source="request")

    cancel = _mentions_cancel(request)
    for provider in _prefs(profile).get("current_providers") or []:
        if not isinstance(provider, dict) or not _text(provider.get("name")):
            continue
        name = _text(provider.get("name"))
        pcat = _text(provider.get("category")).lower() or "service"
        monthly = _money(provider.get("monthly_usd"))
        paying = f"You pay {name} {monthly} a month for {pcat}" if monthly else f"You have {pcat} with {name}"
        if cancel and _provider_named(request, provider):
            key = f"cancel_{_slug(name)}"
            fam = {
                "label": f"Cancel {name}",
                "track": "negotiate",
                "why": f"{paying}; the request says to cancel it, so the call confirms the final bill and any fee.",
                "query": f"{name} cancel {pcat} service phone number cancellation fee policy",
            }
        else:
            key = f"lower_{_slug(name)}"
            fam = {
                "label": f"Lower {name} bill",
                "track": "negotiate",
                "why": f"{paying}; a retention call can lower it without a contract extension.",
                "query": f"{name} retention department phone number current promotion {pcat}",
            }
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "key": key,
                "label": fam["label"],
                "track": "negotiate",
                "why": fam["why"],
                "source": "profile",
                "query": _fill(fam["query"], details),
                "provider": {
                    "category": pcat,
                    "name": name,
                    "monthly_usd": provider.get("monthly_usd"),
                    "contract_end": provider.get("contract_end"),
                },
            }
        )
    return out


# ---------------------------------------------------------------------------------------------
# Step 3: search (the one OpenAI seam; tests monkeypatch research.engine.search_structured)


@dataclass
class SearchResult:
    parsed: BaseModel | None = None
    citations: list[dict] = field(default_factory=list)  # [{"title", "url"}] the API itself cited
    web_search: bool = False
    searches: int = 0
    error: str | None = None


_web_search_unavailable = False


def _location(details: dict) -> dict | None:
    loc: dict[str, Any] = {"type": "approximate", "country": "US"}
    if details.get("destination"):
        loc["city"] = details["destination"]
    if details.get("state"):
        loc["region"] = details["state"]
    return loc


def _collect_citations(response: Any) -> tuple[list[dict], int]:
    """url_citation annotations and web_search_call sources, deduped by URL key, plus the search count."""
    citations: list[dict] = []
    seen: set[str] = set()
    searches = 0

    def add(title: Any, url: Any) -> None:
        key = phones.url_key(url)
        if not key or key in seen:
            return
        seen.add(key)
        citations.append({"title": _text(title) or (phones.host_of(url) or "source"), "url": phones.clean_url(url)})

    for item in getattr(response, "output", None) or []:
        kind = getattr(item, "type", None)
        if kind == "web_search_call":
            searches += 1
            action = getattr(item, "action", None)
            for src in getattr(action, "sources", None) or []:
                add(None, getattr(src, "url", None))
        elif kind == "message":
            for content in getattr(item, "content", None) or []:
                for ann in getattr(content, "annotations", None) or []:
                    if getattr(ann, "type", None) == "url_citation":
                        add(getattr(ann, "title", None), getattr(ann, "url", None))
    return citations, searches


def _is_tool_rejection(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "web_search" in text or ("tool" in text and ("not supported" in text or "unsupported" in text))


async def search_structured(
    system: str,
    user: str,
    schema: type[BaseModel],
    *,
    location: dict | None = None,
    timeout: float = QUERY_TIMEOUT_SECONDS,
    web_search: bool = True,
) -> SearchResult:
    """One Responses API call with the hosted web_search tool and a strict pydantic text_format.

    Verified against openai 3.29.0 (Oct 11 2026):
        client.responses.parse(model=..., instructions=system, input=user,
            tools=[{"type": "web_search", "search_context_size": "low", "user_location": {...}}],
            tool_choice={"type": "web_search"}, text_format=schema,
            include=["web_search_call.action.sources"], max_tool_calls=4)
    response.output has web_search_call items (action.query, action.sources[].url) and a message whose
    output_text content carries .annotations of type url_citation {title, url, start_index, end_index};
    response.output_parsed is the schema instance. Falls back to a plain structured call when the account
    rejects the tool (web_search False, no citations). Never raises.
    """
    global _web_search_unavailable
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return SearchResult(error="OPENAI_API_KEY not set")
    use_tool = web_search and not _web_search_unavailable
    try:
        from openai import AsyncOpenAI

        async with AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0) as client:
            kwargs: dict[str, Any] = {
                "model": llm.model_name(),
                "instructions": system,
                "input": user,
                "text_format": schema,
            }
            if use_tool:
                tool: dict[str, Any] = {"type": "web_search", "search_context_size": SEARCH_CONTEXT_SIZE}
                if location:
                    tool["user_location"] = location
                # tool_choice forces at least one real search; without it gpt-4.1 sometimes answers from
                # memory, returns no web_search_call item and no url_citation annotations.
                kwargs.update(
                    tools=[tool],
                    tool_choice={"type": "web_search"},
                    include=["web_search_call.action.sources"],
                    max_tool_calls=MAX_TOOL_CALLS_PER_SEARCH,
                )
            try:
                response = await client.responses.parse(**kwargs)
            except Exception as exc:
                if not use_tool or not _is_tool_rejection(exc):
                    raise
                _web_search_unavailable = True
                use_tool = False
                for key in ("tools", "tool_choice", "include", "max_tool_calls"):
                    kwargs.pop(key, None)
                response = await client.responses.parse(**kwargs)
        parsed = response.output_parsed
        citations, searches = _collect_citations(response) if use_tool else ([], 0)
        return SearchResult(
            parsed=parsed if isinstance(parsed, schema) else None,
            citations=citations,
            web_search=use_tool,
            searches=searches,
            error=None if isinstance(parsed, schema) else "model returned no parsed output",
        )
    except Exception as exc:  # noqa: BLE001
        return SearchResult(web_search=use_tool, error=f"{type(exc).__name__}: {str(exc)[:200]}")


def _user_prompt(category: dict, details: dict, profile: dict | None, n: int) -> str:
    cohort = cohort_for(profile)
    prefs = _prefs(profile)
    insurance = details.get("insurance") or {}
    context = [
        f"destination {_place(details)}",
        f"move date {details['date']}" if details.get("date") else None,
        details.get("bedrooms"),
        f"budget ceiling {details['budget']}" if details.get("budget") else None,
        f"{cohort.get('label', 'student')} ({cohort.get('proof', 'proof on file')})",
        f"dental insurance {insurance.get('dental')}" if insurance.get("dental") else None,
        f"health insurance {insurance.get('health')}" if insurance.get("health") else None,
        f"household of {prefs.get('household_size')}" if prefs.get("household_size") else None,
    ]
    provider = category.get("provider")
    lines = [
        f"Category: {category['label']} (track: {category['track']}).",
        f"Search query to start from: {category['query']}",
        "Client context: " + "; ".join(str(c) for c in context if c) + ".",
    ]
    if provider:
        monthly = _money(provider.get("monthly_usd"))
        lines.append(
            f"This is the client's current {provider['category']} provider, {provider['name']}"
            + (f", billed {monthly} a month" if monthly else "")
            + ". Find the retention or customer-service phone number as written on an official page, the "
            "current promotions for existing or new customers, and any fee or policy that matters for this call."
        )
        lines.append("Return exactly one vendor: the provider itself.")
    else:
        noun = CATEGORY_FAMILIES.get(category["key"], {}).get("noun", "vendors")
        lines.append(f"Return up to {n} {noun}, best first, each with its own phone number from a source.")
    lines.append(f"Today is {datetime.now().astimezone():%B %d, %Y}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Step 4 and 5: items with verified phones, cited plans and discounts, and a strategy


def _objective(category: dict, name: str, details: dict, request: str) -> str:
    key = category["key"]
    city = details.get("destination")
    date = details.get("date")
    bedrooms = details.get("bedrooms")
    where = f" in {city}" if city else ""
    when = _on(date) if date else ""
    provider = category.get("provider") or {}
    if key.startswith("cancel_"):
        end = f"; the contract ends {_spoken_date(provider.get('contract_end'))}" if provider.get("contract_end") else ""
        return (
            f"Cancel the client's {provider.get('category', 'service')} service with {name}; cancellation is "
            f"authorized. Confirm the final bill date and ask whether any early-termination fee applies{end}."
        )
    if key.startswith("lower_"):
        monthly = _money(provider.get("monthly_usd"))
        paying = f", currently {monthly} a month," if monthly else ""
        return (
            f"Review the client's current {provider.get('category', 'service')} bill with {name}{paying} and ask "
            "for the current promotion or retention offer: a lower monthly rate with no contract extension."
        )
    if key == "movers":
        job = f"to move a {bedrooms}" if bedrooms else "for the move"
        to = f" to {city}" if city else ""
        when_text = when if date else ", date to be confirmed"
        return (
            f"Get an all-in quote from {name} {job}{to}{when_text}: flat or hourly, whether stairs or a long "
            "carry add anything, whether packing is extra, and what dates they have open."
        )
    if key == "internet":
        if details.get("is_move") and not is_existing_bill_request(request):
            move_in = f" for move-in{when}" if date else ", move-in date to be confirmed"
            return (
                f"Set up internet with {name} at the client's new address{where}{move_in}: ask for the current "
                "new-customer promotion, the student rate, no contract, the installation fee waived, and "
                "confirm the install date."
            )
        return (
            f"Review the client's current internet bill with {name} and ask for the current promotion or "
            "retention offer: a lower monthly rate with no contract extension."
        )
    if key == "dentist":
        around = f", around the move{when}" if details.get("is_move") and date else ""
        return (
            f"Book the earliest new-patient dental cleaning with {name} that fits the client's availability"
            f"{around}, and get the self-pay price if insurance isn't accepted."
        )
    if key == "doctor":
        return (
            f"Book the earliest new-patient visit with {name} that fits the client's availability, and ask "
            "whether they take the client's insurance or have a self-pay rate."
        )
    if key == "phone":
        return (
            f"Ask {name} for the cheapest plan that fits a student, the student discount, whether there is an "
            "activation fee, and a no-contract term."
        )
    if key == "gym":
        return f"Get {name}'s monthly price for a student membership, the joining fee, and whether month-to-month is available."
    if key == "insurance":
        unit = f" for a {bedrooms}" if bedrooms else ""
        start = f" starting{when}" if date else ""
        return (
            f"Get a renters insurance quote from {name}{unit}{where}{start}: monthly price, deductible, and any "
            "student or bundling discount."
        )
    if key == "utilities":
        start = f" for{when}" if date else ""
        return (
            f"Start {name} service at the client's new address{where}{start}: confirm the start date, any "
            "deposit, and whether a student or new-customer deposit waiver applies."
        )
    if key == "storage":
        start = f" starting{when}" if date else ""
        return f"Get {name}'s monthly price for a small unit near {city or 'the new address'}{start}, the admin fee, and any student discount."
    about = re.sub(r"\s+", " ", request or "").strip()[:200]
    return f"Call {name} about: {about}."


def _default_opening(category: dict, details: dict, variant: str) -> str:
    key = category["key"]
    city = details.get("destination")
    date = details.get("date")
    bedrooms = details.get("bedrooms") or "apartment"
    provider = category.get("provider") or {}
    monthly = _money(provider.get("monthly_usd"))
    if variant == "S3_cancel":
        return "We're deciding whether to keep this service; before anything else, what's the best rate available on the account?"
    if key.startswith("lower_") or (category["track"] == "negotiate" and variant != "new_service"):
        bill = f", which is {monthly} a month" if monthly else ""
        return f"I'm going over the {provider.get('category', 'monthly')} bill{bill}; what promotions or adjustments are available on it right now?"
    if key == "internet":
        return (
            f"I'm setting up internet at a new address{' in ' + city if city else ''}"
            f"{' for move-in' + _on(date) if date else ''}; what's the current new-customer promotion there?"
        )
    if key == "movers":
        return f"I'm calling for an all-in quote to move a {bedrooms}{' to ' + city if city else ''}{_on(date)}; what would that run?"
    if key == "dentist":
        return f"I'd like to book a new-patient cleaning{' around ' + date if date else ''}; what's the earliest you have?"
    if key == "doctor":
        return "I'd like to book a new-patient visit; what's the earliest you have?"
    if key == "utilities":
        return f"I need to start service at a new address{' in ' + city if city else ''}{_on(date)}; what do you need from me?"
    if key == "insurance":
        return f"I'm looking for a renters policy for a {bedrooms}{' in ' + city if city else ''}; what would that run a month?"
    if key == "gym":
        return "I'm asking about a student membership; what's the monthly price and is there a joining fee?"
    if key == "phone":
        return "I'm looking for the cheapest plan that fits a student; what's available with no contract?"
    if key == "storage":
        return f"I'm looking for a small unit near {city or 'campus'}{_on(date)}; what's the monthly price?"
    return "I'm calling about the client's request; could you help me with that?"


def _walk_away(category: dict, details: dict, variant: str) -> str:
    key = category["key"]
    budget = details.get("budget")
    date = details.get("date")
    provider = category.get("provider") or {}
    monthly = _money(provider.get("monthly_usd"))
    if variant == "S3_cancel":
        return "If they will not cancel without the account holder on the line, do not argue; note the steps and end the call."
    if key.startswith("lower_") or (category["track"] == "negotiate" and variant != "new_service"):
        beat = f"beat {monthly} a month" if monthly else "lower the monthly rate"
        return f"If they cannot {beat} without a contract extension, thank them and keep the current plan for now."
    if variant == "new_service":
        by = f" by{_on(date)}" if date else " in time for the move"
        return f"If they cannot install{by} or the promotion needs a contract, say the client will compare and call back."
    if category["track"] == "quote":
        ceiling = f"above {budget}" if budget else "above the client's budget"
        return f"If the all-in price is {ceiling} or the deposit is above 10 percent of the quote, thank them and say the client will think it over."
    return "If nothing fits inside the client's availability, take their next two open slots and say the client will confirm."


def _strategy(category: dict, name: str, details: dict, profile: dict | None, request: str, finding: VendorFinding | None) -> dict:
    objective = _objective(category, name, details, request)
    track = category["track"]
    cohort = cohort_for(profile)
    asks = list(cohort_asks(cohort, track))
    for ask in (finding.item_asks if finding else []) or []:
        text = _text(ask).rstrip(".")
        if text and text.lower() not in {a.lower() for a in asks} and len(asks) < 7:
            asks.append(text)
    if track == "negotiate":
        variant, reason = choose_strategy(objective, None, {"name": name})
        label = STRATEGIES[variant]["label"]
    else:
        variant = track
        label = "all-in price quote" if track == "quote" else "earliest appointment"
        reason = (
            "quote track: compare all-in prices across vendors before choosing"
            if track == "quote"
            else "schedule track: book the earliest slot inside the client's availability"
        )
    opening = _spoken(finding.opening) if finding else ""
    if not (20 <= len(opening) <= 240):
        opening = _default_opening(category, details, variant)
    return {
        "variant": variant,
        "label": label,
        "reason": reason,
        "asks": asks,
        "opening": opening,
        "walk_away": _walk_away(category, details, variant),
        "objective": objective,
    }


def _roster_item(category: dict, vendor: dict, details: dict, profile: dict | None, request: str) -> dict:
    name = _text(vendor.get("name")) or _text(vendor.get("key")) or "vendor"
    phone = phones.normalize(vendor.get("phone")) or (_text(vendor.get("phone")) or None)
    return {
        "id": str(uuid.uuid4()),
        "category": category["key"],
        "track": category["track"],
        "source": "roster",
        "vendor": {
            "key": _text(vendor.get("key")) or _slug(name),
            "name": name,
            "phone": phone,
            "website": None,
            "address": None,
            "is_demo": bool(vendor.get("is_demo", False)),
            "roster_vendor_id": vendor.get("id"),
        },
        "why": "already on your roster",
        "plans": [],
        "discounts": [],
        "strategy": _strategy(category, name, details, profile, request, None),
        "confidence": 1.0,
        "citations": [],
        "recommended": bool(phone),
        "phone_verified": bool(phone),
        "phone_source": "roster" if phone else None,
        "phone_note": None,
        "skip_reason": None if phone else "roster row has no phone number",
    }


def _roster_category(vendor: dict, categories: list[dict]) -> dict | None:
    """The category a roster vendor belongs to: a current-provider match, then a name keyword, then track."""
    name = _text(vendor.get("name")).lower()
    name_words = _words(name)
    for cat in categories:
        provider = cat.get("provider")
        if provider and _words(provider["name"]) & name_words:
            return cat
    for cat in categories:
        fam = CATEGORY_FAMILIES.get(cat["key"])
        if fam and (name_words & set(fam["words"]) or cat["key"] in name_words):
            return cat
    for cat in categories:
        if cat["track"] == vendor.get("track") and not cat.get("provider"):
            return cat
    return None


def _track_category(vendor: dict) -> dict:
    track = vendor.get("track") if vendor.get("track") in ("quote", "schedule", "negotiate") else "quote"
    labels = {"quote": "Quotes", "schedule": "Appointments", "negotiate": "Bills"}
    return {
        "key": track,
        "label": labels[track],
        "track": track,
        "why": "on your roster",
        "source": "roster",
        "query": None,
    }


@dataclass
class _Context:
    request: str
    profile: dict | None
    roster: list[dict]
    details: dict
    categories: list[dict]
    max_items: int
    started: float
    items_by_category: dict[str, list[dict]] = field(default_factory=dict)
    regulations: list[dict] | None = None
    errors: list[str] = field(default_factory=list)
    web_search: bool = False
    search_count: int = 0
    cache: phones.PageCache = field(default_factory=phones.PageCache)
    fetch_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_SEARCHES))
    search_limit: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(MAX_CONCURRENT_SEARCHES))
    second_pass: dict[str, Any] = field(
        default_factory=lambda: {
            "phone_searches": 0, "site_fetches": 0, "info_searches": 0, "phones_found": 0, "seconds": 0.0,
        }
    )  # fmt: skip


async def _page(ctx: _Context, url: str | None) -> str | None:
    async with ctx.fetch_limit:
        return await ctx.cache.get(url)


def _plan_dict(ctx: _Context, p: SourcedPlan, idx: int) -> dict:
    page = ctx.cache.pages.get(phones.url_key(p.source_url) or "")
    in_source = None
    if p.price_usd is not None and page:
        amount = f"{p.price_usd:.2f}".rstrip("0").rstrip(".")
        in_source = amount in phones.visible_text(page)
    return {
        "name": _text(p.name),
        "price_usd": p.price_usd,
        "unit": _text(p.unit) or None,
        "notes": _text(p.notes) or None,
        "citation": idx,
        "price_in_source": in_source,
    }


def _discount_dict(d: SourcedDiscount, idx: int) -> dict:
    return {
        "label": _text(d.label),
        "who_qualifies": _text(d.who_qualifies),
        "how_to_claim": _text(d.how_to_claim),
        "citation": idx,
    }


async def _item_from_finding(ctx: _Context, category: dict, finding: VendorFinding, api_citations: list[dict]) -> dict:
    name = _text(finding.name) or "vendor"
    api_by_key = {phones.url_key(c["url"]): c for c in api_citations if phones.url_key(c.get("url"))}

    # Every URL this vendor rests on, phone source first so it is fetched first.
    referenced: list[str] = []
    for url in [finding.phone_source_url, *finding.source_urls, finding.website,
                *(p.source_url for p in finding.plans), *(d.source_url for d in finding.discounts)]:  # fmt: skip
        cleaned = phones.clean_url(url)
        if cleaned and phones.url_key(cleaned) not in {phones.url_key(u) for u in referenced}:
            referenced.append(cleaned)

    # Citations: a URL the API cited counts as is; any other URL counts only once we fetched it.
    citations: list[dict] = []
    index_by_key: dict[str, int] = {}
    fetched = 0
    for url in referenced:
        key = phones.url_key(url)
        if key in api_by_key:
            citations.append({**api_by_key[key], "verified": "api"})
            index_by_key[key] = len(citations) - 1
            continue
        if fetched >= MAX_FETCH_PER_ITEM:
            continue
        fetched += 1
        if await _page(ctx, url):
            citations.append({"title": phones.host_of(url) or "source", "url": url, "verified": "fetched"})
            index_by_key[key] = len(citations) - 1

    # Phone: only when the digits appear in a page we fetched.
    phone = phones.normalize(finding.phone_as_written)
    phone_verified = False
    phone_source: str | None = None
    phone_note: str | None = None
    skip_reason: str | None = None
    candidates = [u for u in referenced if phones.url_key(u) in index_by_key] or referenced
    if phone:
        async with ctx.fetch_limit:
            proof = await phones.verify_phone(phone, candidates, ctx.cache, max_pages=MAX_FETCH_PER_ITEM)
        if proof:
            phone_verified = True
            phone_source = "model_and_page"
            key = phones.url_key(proof)
            if key not in index_by_key:
                citations.append({"title": phones.host_of(proof) or "source", "url": proof, "verified": "fetched"})
                index_by_key[key] = len(citations) - 1
    if not phone_verified and not finding.phone_as_written:
        # The model found no number. The vendor's own page can still settle it when it presents exactly
        # one number (research.phones.single_page_phone). A number the model did report but which is not
        # on any fetched page is dropped here, never replaced: it may belong to a different vendor. The
        # second pass (_second_pass) then searches for the vendor's number again and reads its own site.
        own_hosts = {h for h in (phones.host_of(finding.website), phones.host_of(finding.phone_source_url)) if h}
        for url in candidates:
            if phones.host_of(url) not in own_hosts:
                continue
            text = ctx.cache.pages.get(phones.url_key(url) or "")
            found = phones.single_page_phone(text)
            if found:
                reported = f"the model reported {finding.phone_as_written}" if finding.phone_as_written else (
                    "the model reported no number"
                )
                phone_note = f"number taken from the single number on {phones.host_of(url)}; {reported}"
                phone, phone_verified, phone_source = found, True, "page"
                key = phones.url_key(url)
                if key not in index_by_key:
                    citations.append({"title": phones.host_of(url) or "source", "url": url, "verified": "fetched"})
                    index_by_key[key] = len(citations) - 1
                break
    if not phone_verified:
        if finding.phone_as_written and not phones.normalize(finding.phone_as_written):
            skip_reason = "phone in sources is not a US number"
        elif not finding.phone_as_written:
            skip_reason = "no phone number found in sources"
        else:
            skip_reason = "phone not confirmed in fetched sources"
        phone = None

    def cite(url: str | None) -> int | None:
        return index_by_key.get(phones.url_key(url) or "")

    plans = []
    for p in finding.plans:
        idx = cite(p.source_url)
        if idx is None or not _text(p.name):
            continue
        plans.append(_plan_dict(ctx, p, idx))
    discounts = []
    for d in finding.discounts:
        idx = cite(d.source_url)
        if idx is None or not _text(d.label):
            continue
        discounts.append(_discount_dict(d, idx))

    confidence = max(0.0, min(1.0, float(finding.confidence or 0.0)))
    if not phone_verified:
        confidence = round(confidence * 0.6, 2)
    return {
        "id": str(uuid.uuid4()),
        "category": category["key"],
        "track": category["track"],
        "source": "web",
        "vendor": {
            "key": f"{category['key']}_{_slug(name)}"[:60],
            "name": name,
            "phone": phone,
            "website": phones.clean_url(finding.website),
            "address": _text(finding.address) or None,
            "is_demo": False,
            "roster_vendor_id": None,
        },
        "why": _text(finding.why) or f"Found while searching for {category['label'].lower()}.",
        "plans": plans,
        "discounts": discounts,
        "strategy": _strategy(category, name, ctx.details, ctx.profile, ctx.request, finding),
        "confidence": confidence,
        "citations": citations,
        "recommended": phone_verified and confidence >= 0.5,
        "phone_verified": phone_verified,
        "phone_source": phone_source,
        "phone_note": phone_note,
        "skip_reason": skip_reason,
    }


async def _search_category(ctx: _Context, category: dict, limit: asyncio.Semaphore) -> None:
    async with limit:
        result = await search_structured(
            SYSTEM_PROMPT,
            _user_prompt(category, ctx.details, ctx.profile, ctx.max_items),
            CategoryFindings,
            location=_location(ctx.details),
            timeout=QUERY_TIMEOUT_SECONDS,
        )
    ctx.web_search = ctx.web_search or bool(result.web_search)
    ctx.search_count += int(result.searches or 0)
    if result.error:
        ctx.errors.append(f"{category['key']}: {result.error}")
    findings = result.parsed if isinstance(result.parsed, CategoryFindings) else None
    if findings is None:
        return
    items: list[dict] = []
    for finding in findings.vendors[: ctx.max_items]:
        try:
            items.append(await _item_from_finding(ctx, category, finding, result.citations))
        except Exception as exc:  # noqa: BLE001
            ctx.errors.append(f"{category['key']}: {finding.name!r} skipped ({type(exc).__name__}: {exc})"[:200])
    ctx.items_by_category.setdefault(category["key"], []).extend(items)


async def _regulations(ctx: _Context) -> None:
    from research import regulations

    context = {
        "canceling": any(c["key"].startswith("cancel_") for c in ctx.categories),
        "categories": [c["key"] for c in ctx.categories],
        "destination": ctx.details.get("destination"),
        "recording": False,
        "cohort": cohort_for(ctx.profile).get("key", "student"),
    }
    try:
        ctx.regulations = await regulations.check(ctx.details.get("state"), context)
    except Exception as exc:  # noqa: BLE001
        ctx.errors.append(f"regulations: {type(exc).__name__}: {exc}"[:200])
        ctx.regulations = regulations.baseline(ctx.details.get("state"), context, note=str(exc)[:120])


async def _run(ctx: _Context) -> None:
    limit = ctx.search_limit
    tasks = [
        asyncio.wait_for(_search_category(ctx, c, limit), QUERY_TIMEOUT_SECONDS + 15.0)
        for c in ctx.categories
        if c.get("query")
    ]
    tasks.append(_regulations(ctx))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for cat, res in zip([c for c in ctx.categories if c.get("query")], results):
        if isinstance(res, TimeoutError):
            ctx.errors.append(f"{cat['key']}: search timed out after {QUERY_TIMEOUT_SECONDS:.0f}s")
        elif isinstance(res, BaseException):
            ctx.errors.append(f"{cat['key']}: {type(res).__name__}: {res}"[:200])


# ---------------------------------------------------------------------------------------------
# Step 4b: the second pass. The product never asks the user for a vendor's number; it goes and finds it.


def _roster_covered(ctx: _Context) -> set[str]:
    """Category keys that already hold a roster vendor with a number: the user has someone to call there."""
    covered: set[str] = set()
    for vendor in ctx.roster:
        if not (vendor.get("key") or vendor.get("name")) or not _text(vendor.get("phone")):
            continue
        cat = _roster_category(vendor, ctx.categories)
        if cat is not None:
            covered.add(cat["key"])
    return covered


def _needy_categories(ctx: _Context) -> set[str]:
    """Roster-less categories with fewer than two verified items. Their lookups go first."""
    covered = _roster_covered(ctx)
    return {
        key
        for key, items in ctx.items_by_category.items()
        if key not in covered and sum(1 for it in items if it["phone_verified"]) < 2
    }


def _lookup_prompt(item: dict, category: dict, details: dict, *, want: str) -> str:
    vendor = item["vendor"]
    name = vendor["name"]
    city = details.get("destination") or ""
    known = [
        f"website {vendor['website']}" if vendor.get("website") else None,
        f"address {vendor['address']}" if vendor.get("address") else None,
        f"found while searching for {category['label'].lower()}",
    ]
    if want == "phone":
        query = f"{name} {city} phone number"
        ask = "Find this vendor's phone number as written on a page you opened, and the vendor's own website."
    else:
        query = f"{name} prices plans student discount {city}"
        ask = (
            "Find this vendor's current prices or plans and any student or new-customer discount, each with "
            "the page that states it."
        )
    return "\n".join(
        [
            f"Vendor: {name}",
            f"Search query to start from: {' '.join(query.split())}",
            "Known: " + "; ".join(k for k in known if k) + ".",
            f"Destination: {_place(details)}.",
            ask,
            f"Today is {datetime.now().astimezone():%B %d, %Y}.",
        ]
    )


def _site_urls(website: str) -> list[str]:
    """The site root, the website URL itself when it is not the root, and up to MAX_SITE_PATHS contact pages."""
    website = phones.clean_url(website) or website
    parts = urlsplit(website)
    root = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
    urls = [root]
    if phones.url_key(website) != phones.url_key(root):
        urls.append(website)
    paths = 0
    for path in SITE_CONTACT_PATHS:
        url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        if phones.url_key(url) in {phones.url_key(u) for u in urls}:
            continue
        urls.append(url)
        paths += 1
        if paths >= MAX_SITE_PATHS:
            break
    return urls


def _add_citation(item: dict, url: str, verified: str, title: str | None = None) -> int:
    """The index of `url` in item.citations, appending it when new."""
    key = phones.url_key(url)
    for i, c in enumerate(item["citations"]):
        if phones.url_key(c.get("url")) == key:
            return i
    item["citations"].append({"title": title or phones.host_of(url) or "source", "url": url, "verified": verified})
    return len(item["citations"]) - 1


def _set_phone(ctx: _Context, item: dict, phone: str, source: str, url: str, verified: str, note: str) -> None:
    """A proven number: the item becomes dialable and the 0.6 penalty an unverified phone cost it is undone."""
    item["vendor"]["phone"] = phone
    item["phone_verified"] = True
    item["phone_source"] = source
    item["phone_note"] = note
    item["skip_reason"] = None
    _add_citation(item, url, verified)
    item["confidence"] = round(min(1.0, item["confidence"] / 0.6), 2)
    item["recommended"] = item["confidence"] >= 0.5
    ctx.second_pass["phones_found"] += 1


def _note_result(ctx: _Context, category: dict, name: str, what: str, result: SearchResult) -> None:
    ctx.web_search = ctx.web_search or bool(result.web_search)
    ctx.search_count += int(result.searches or 0)
    if result.error:
        ctx.errors.append(f"{category['key']}: {name!r} {what}: {result.error}"[:200])


async def _lookup_phone(ctx: _Context, item: dict, category: dict, *, timeout: float) -> tuple[str, str, str] | None:
    """One targeted search for the vendor's number: (phone, proof URL, "api" | "fetched"), or None.

    The number is kept only when its digits are on a page the model cited for it, a page the API cited, or
    the vendor's own website. A website or address the lookup adds is kept on the item either way.
    """
    vendor = item["vendor"]
    name = vendor["name"]
    ctx.second_pass["phone_searches"] += 1
    async with ctx.search_limit:
        result = await search_structured(
            LOOKUP_SYSTEM_PROMPT,
            _lookup_prompt(item, category, ctx.details, want="phone"),
            PhoneLookup,
            location=_location(ctx.details),
            timeout=timeout,
        )
    _note_result(ctx, category, name, "phone lookup", result)
    lookup = result.parsed if isinstance(result.parsed, PhoneLookup) else None
    if lookup is None:
        return None
    website = phones.clean_url(lookup.website)
    if website and not vendor.get("website"):
        vendor["website"] = website
    if _text(lookup.address) and not vendor.get("address"):
        vendor["address"] = _text(lookup.address)
    phone = phones.normalize(lookup.phone_as_written)
    if not phone:
        return None
    api_keys = {phones.url_key(c["url"]) for c in result.citations}
    allowed = [lookup.phone_source_url, *(c["url"] for c in result.citations), website, vendor.get("website")]
    candidates: list[str] = []
    for url in allowed:
        cleaned = phones.clean_url(url)
        if cleaned and phones.url_key(cleaned) not in {phones.url_key(u) for u in candidates}:
            candidates.append(cleaned)
    async with ctx.fetch_limit:
        proof = await phones.verify_phone(phone, candidates, ctx.cache, max_pages=MAX_FETCH_PER_ITEM)
    if not proof:
        return None
    return phone, proof, ("api" if phones.url_key(proof) in api_keys else "fetched")


async def _read_site(ctx: _Context, website: str) -> tuple[tuple[str, str] | None, list[str]]:
    """The vendor's own site, root and contact pages: ((phone, page URL) or None, the URLs read).

    A page counts only when it presents exactly one number (research.phones.single_page_phone). The host is
    the vendor's own, so that page is the proof.
    """
    urls = _site_urls(website)
    ctx.second_pass["site_fetches"] += sum(1 for u in urls if phones.url_key(u) not in ctx.cache.pages)
    texts = await asyncio.gather(*(_page(ctx, u) for u in urls))
    for url, text in zip(urls, texts):
        found = phones.single_page_phone(text)
        if found:
            return (found, url), urls
    return None, urls


async def _rescue_phone(ctx: _Context, item: dict, category: dict, *, search: bool, timeout: float) -> None:
    """One item without a verified phone: a targeted search and the vendor's own site, read at the same time.

    The search result is preferred when both prove a number. The item is mutated only after both are in, so
    a timeout leaves it untouched. A number nobody cited and that is not on the vendor's own site is never taken.
    """
    first_reason = item.get("skip_reason") or "no phone number"
    vendor = item["vendor"]
    website = phones.clean_url(vendor.get("website"))
    # The site read is a side task so this task's first await is the search permit: phone lookups queue for
    # the semaphore ahead of the price lookups _second_pass creates after them.
    site_task = asyncio.ensure_future(_read_site(ctx, website)) if website else None
    try:
        found = await _lookup_phone(ctx, item, category, timeout=timeout) if search else None
    except BaseException:
        if site_task is not None:
            site_task.cancel()
        raise
    site = await site_task if site_task is not None else None
    if found is None and site is None and phones.clean_url(vendor.get("website")):
        # The search found no number but did name the vendor's site. Read it now.
        site = await _read_site(ctx, phones.clean_url(vendor["website"]) or "")

    tried: list[str] = ["a targeted search"] if search else []
    if site is not None:
        urls = site[1]
        tried.append(f"{len(urls)} page{'s' if len(urls) != 1 else ''} on {phones.host_of(urls[0])}")
    if found is not None:
        phone, proof, verified = found
        note = f"found by a targeted search; the digits are on {phones.host_of(proof)} (first pass: {first_reason})"
        _set_phone(ctx, item, phone, "model_and_page", proof, verified, note)
        return
    if site is not None and site[0] is not None:
        phone, url = site[0]
        note = f"the single number on the vendor's own page {url} (first pass: {first_reason})"
        _set_phone(ctx, item, phone, "own_site", url, "fetched", note)
        return
    if tried:
        item["skip_reason"] = NO_NUMBER_REASON
        item["phone_note"] = f"first pass: {first_reason}; second pass tried {' and '.join(tried)}"


async def _enrich_item(ctx: _Context, item: dict, category: dict, *, timeout: float) -> None:
    """One item with no plans and no discounts: one targeted price search; anything uncited is dropped."""
    name = item["vendor"]["name"]
    ctx.second_pass["info_searches"] += 1
    async with ctx.search_limit:
        result = await search_structured(
            LOOKUP_SYSTEM_PROMPT,
            _lookup_prompt(item, category, ctx.details, want="info"),
            InfoLookup,
            location=_location(ctx.details),
            timeout=timeout,
        )
    _note_result(ctx, category, name, "price lookup", result)
    lookup = result.parsed if isinstance(result.parsed, InfoLookup) else None
    if lookup is None:
        return
    api_by_key = {phones.url_key(c["url"]): c for c in result.citations if phones.url_key(c.get("url"))}
    fetched = 0

    async def cite(url: str | None) -> int | None:
        nonlocal fetched
        cleaned = phones.clean_url(url)
        key = phones.url_key(cleaned)
        if not cleaned or not key:
            return None
        for i, c in enumerate(item["citations"]):
            if phones.url_key(c.get("url")) == key:
                return i
        if key in api_by_key:
            return _add_citation(item, api_by_key[key]["url"], "api", api_by_key[key].get("title"))
        if fetched >= MAX_FETCH_PER_ITEM:
            return None
        fetched += 1
        if await _page(ctx, cleaned):
            return _add_citation(item, cleaned, "fetched")
        return None

    plans: list[dict] = []
    names = {p["name"].lower() for p in item["plans"]}
    for p in lookup.plans:
        label = _text(p.name)
        if not label or label.lower() in names:
            continue
        idx = await cite(p.source_url)
        if idx is None:
            continue
        names.add(label.lower())
        plans.append(_plan_dict(ctx, p, idx))
    discounts: list[dict] = []
    labels = {d["label"].lower() for d in item["discounts"]}
    for d in lookup.discounts:
        label = _text(d.label)
        if not label or label.lower() in labels:
            continue
        idx = await cite(d.source_url)
        if idx is None:
            continue
        labels.add(label.lower())
        discounts.append(_discount_dict(d, idx))
    item["plans"].extend(plans)
    item["discounts"].extend(discounts)


def _second_pass_work(ctx: _Context) -> tuple[list[tuple[dict, dict]], list[tuple[dict, dict]]]:
    """(items without a verified phone, items with no plans and no discounts), each with its category.

    Phone work is ordered with roster-less categories that have fewer than two verified items first; price
    work with items that have a verified phone first (those are the ones that will be called).
    """
    by_key = {c["key"]: c for c in ctx.categories}
    needy = _needy_categories(ctx)
    unverified: list[tuple[dict, dict]] = []
    bare: list[tuple[dict, dict]] = []
    for key, items in ctx.items_by_category.items():
        cat = by_key.get(key)
        if cat is None:
            continue
        for it in items:
            if not it["phone_verified"]:
                unverified.append((it, cat))
            if not it["plans"] and not it["discounts"]:
                bare.append((it, cat))
    unverified.sort(key=lambda pair: pair[1]["key"] not in needy)
    bare.sort(key=lambda pair: not pair[0]["phone_verified"])
    return unverified, bare


async def _second_pass(ctx: _Context, budget: float) -> None:
    """Find what the first pass could not. Capped at SECOND_PASS_MAX_SEARCHES model calls and `budget` seconds.

    Every unverified item gets its own site read whether or not it got one of the search slots. Items are
    mutated only once their evidence is in hand, so a timeout leaves each item either untouched or finished.
    """
    unverified, bare = _second_pass_work(ctx)
    n_phone = min(SECOND_PASS_MAX_SEARCHES, len(unverified))
    n_info = min(SECOND_PASS_MAX_SEARCHES - n_phone, len(bare))
    timeout = min(QUERY_TIMEOUT_SECONDS, budget)
    tasks = [
        _rescue_phone(ctx, it, cat, search=i < n_phone, timeout=timeout) for i, (it, cat) in enumerate(unverified)
    ]
    tasks += [_enrich_item(ctx, it, cat, timeout=timeout) for it, cat in bare[:n_info]]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for res in results:
        if isinstance(res, BaseException):
            ctx.errors.append(f"second pass: {type(res).__name__}: {res}"[:200])


def _summary(ctx: _Context, items: list[dict]) -> str:
    details = ctx.details
    where = f" to {details['destination']}" if details.get("destination") else ""
    when = _on(details.get("date")) if details.get("date") else ""
    by_cat: dict[str, int] = {}
    for it in items:
        by_cat[it["category"]] = by_cat.get(it["category"], 0) + 1
    labels = {c["key"]: c["label"] for c in ctx.categories}
    parts = [f"{n} for {labels.get(k, k).lower()}" for k, n in by_cat.items()]
    verified = sum(1 for it in items if it["phone_verified"])
    recommended = sum(1 for it in items if it["recommended"])
    web = sum(1 for it in items if it["source"] == "web")
    roster = sum(1 for it in items if it["source"] == "roster")
    regs = ctx.regulations or []
    applying = sum(1 for r in regs if r.get("applies"))
    sentences = [
        f"Found {len(items)} option{'s' if len(items) != 1 else ''} across {len(by_cat)} "
        f"categor{'ies' if len(by_cat) != 1 else 'y'} for the move{where}{when}"
        + (": " + ", ".join(parts) if parts else "")
        + ".",
    ]
    if web or roster:
        sentences.append(
            f"{web} came from web research and {roster} {'were' if roster != 1 else 'was'} already on your roster."
        )
    sentences.append(
        f"{verified} ha{'ve' if verified != 1 else 's'} a phone number confirmed in a fetched source; the rest are "
        "listed without a dialable number and will not be called."
    )
    sp = ctx.second_pass
    if sp["phone_searches"] or sp["site_fetches"] or sp["info_searches"]:
        sentences.append(
            f"A second pass ran {sp['phone_searches']} targeted phone search{'es' if sp['phone_searches'] != 1 else ''}, "
            f"read {sp['site_fetches']} vendor page{'s' if sp['site_fetches'] != 1 else ''} and ran "
            f"{sp['info_searches']} price search{'es' if sp['info_searches'] != 1 else ''}; it confirmed "
            f"{sp['phones_found']} more phone number{'s' if sp['phones_found'] != 1 else ''}."
        )
    sentences.append(f"{recommended} {'are' if recommended != 1 else 'is'} recommended.")
    if regs:
        sentences.append(
            f"{len(regs)} rules were checked and {applying} apply; the agent discloses it is an AI, does not "
            "record, calls during business hours and claims only the eligibility on your profile."
        )
    if not ctx.web_search:
        sentences.append("Web search was unavailable for this run, so no new vendors or prices were found online.")
    if ctx.errors:
        sentences.append(f"{len(ctx.errors)} step{'s' if len(ctx.errors) != 1 else ''} reported a problem.")
    sentences.append("Nothing is called until you approve it.")
    return " ".join(sentences)


def _assemble(ctx: _Context) -> dict:
    items: list[dict] = []
    seen_roster: set[str] = set()
    categories = list(ctx.categories)
    derived = bool(ctx.categories)  # the request or profile named categories; roster vendors must fit one
    for vendor in ctx.roster or []:
        if not isinstance(vendor, dict) or not (vendor.get("key") or vendor.get("name")):
            continue
        cat = _roster_category(vendor, categories)
        if cat is None:
            if derived:
                continue
            cat = _track_category(vendor)
            if cat["key"] not in {c["key"] for c in categories}:
                categories.append(cat)
            else:
                cat = next(c for c in categories if c["key"] == cat["key"])
        key = _text(vendor.get("key")) or _slug(_text(vendor.get("name")))
        if key in seen_roster:
            continue
        seen_roster.add(key)
        items.append(_roster_item(cat, vendor, ctx.details, ctx.profile, ctx.request))
    for cat in categories:
        items.extend(ctx.items_by_category.get(cat["key"], []))
    items.sort(key=lambda it: ([c["key"] for c in categories].index(it["category"]), not it["recommended"]))

    regs = ctx.regulations
    if regs is None:
        from research import regulations

        regs = regulations.baseline(ctx.details.get("state"), {"canceling": False}, note="regulations check did not finish")
    proposal = {
        "request": ctx.request,
        "details": ctx.details,
        "categories": [{k: v for k, v in c.items() if k != "provider"} | {"provider": c.get("provider")} for c in categories],
        "items": items,
        "regulations": regs,
        "summary": _summary(ctx, items),
        "generated_at": _now_iso(),
        "model": llm.model_name(),
        "web_search": bool(ctx.web_search),
        "search_count": int(ctx.search_count),
        "errors": list(ctx.errors),
        "seconds": round(time.monotonic() - ctx.started, 1),
        "second_pass": dict(ctx.second_pass),
    }
    return Proposal.model_validate(proposal).model_dump(mode="json")


def _minimal(request: str, errors: list[str]) -> dict:
    return {
        "request": request,
        "details": parse_request_details(request),
        "categories": [],
        "items": [],
        "regulations": [],
        "summary": "Research could not run. Nothing is called until you approve it.",
        "generated_at": _now_iso(),
        "model": llm.model_name(),
        "web_search": False,
        "search_count": 0,
        "errors": errors,
        "seconds": None,
        "second_pass": {"phone_searches": 0, "site_fetches": 0, "info_searches": 0, "phones_found": 0, "seconds": 0.0},
    }


async def research(
    profile: dict | None, request: str, roster: list[dict], *, max_items_per_category: int = 3
) -> dict:
    """The proposal for this request. Never raises; a timeout or failure returns what finished plus errors."""
    started = time.monotonic()
    request = (request or "").strip()
    try:
        details = merge_details(request, profile)
        categories = select_categories(request, profile, details)
        ctx = _Context(
            request=request,
            profile=profile if isinstance(profile, dict) else None,
            roster=[v for v in (roster or []) if isinstance(v, dict)],
            details=details,
            categories=categories,
            max_items=max(1, int(max_items_per_category)),
            started=started,
        )
    except Exception as exc:  # noqa: BLE001
        return _minimal(request, [f"setup: {type(exc).__name__}: {exc}"[:200]])
    # First pass: the overall budget minus what is reserved for the second pass.
    first_budget = OVERALL_BUDGET_SECONDS - SECOND_PASS_SECONDS
    if first_budget <= 0:
        first_budget = OVERALL_BUDGET_SECONDS
    try:
        await asyncio.wait_for(_run(ctx), first_budget)
    except TimeoutError:
        ctx.errors.append(f"research budget of {first_budget:g} seconds exhausted; returning what finished")
    except Exception as exc:  # noqa: BLE001
        ctx.errors.append(f"research: {type(exc).__name__}: {exc}"[:200])
    # Second pass: whatever is left of the overall budget, at most SECOND_PASS_SECONDS.
    budget = min(SECOND_PASS_SECONDS, OVERALL_BUDGET_SECONDS - (time.monotonic() - started))
    if budget >= SECOND_PASS_MIN_SECONDS:
        second_started = time.monotonic()
        try:
            if any(_second_pass_work(ctx)):
                await asyncio.wait_for(_second_pass(ctx, budget), budget)
                ctx.second_pass["seconds"] = round(time.monotonic() - second_started, 1)
        except TimeoutError:
            ctx.errors.append(f"second-pass budget of {budget:g} seconds exhausted; keeping what it found")
            ctx.second_pass["seconds"] = round(time.monotonic() - second_started, 1)
        except Exception as exc:  # noqa: BLE001
            ctx.errors.append(f"second pass: {type(exc).__name__}: {exc}"[:200])
            ctx.second_pass["seconds"] = round(time.monotonic() - second_started, 1)
    try:
        return _assemble(ctx)
    except Exception as exc:  # noqa: BLE001
        return _minimal(request, [*ctx.errors, f"assemble: {type(exc).__name__}: {exc}"[:200]])
