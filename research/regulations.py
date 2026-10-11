"""Regulations the calling agent must respect, checked per destination state. Never raises.

check(state, context) returns a list of
    {"rule", "jurisdiction", "applies": bool, "what_we_do", "citation": {"title", "url"} | None, "key", "note"}

The baseline is static and covers what the product actually does: recording consent (California Penal Code
632, all-party consent; we disclose and do not record by default), bot disclosure (California Business and
Professions Code 17940-17943; the agent says it is an AI in its second sentence), the TCPA with the FCC's
February 2024 declaratory ruling that AI-generated voices are "artificial voices" (we call businesses, during
business hours 9am to 6pm local, and identify ourselves), honesty in claims (FTC Act section 5; the agent
asserts only the eligibility the profile has), the state's automatic-renewal and cancellation rules when a
provider is being canceled, and contract-extension authority (the agent may not extend, add or cancel
without written authorization in its objective).

With OPENAI_API_KEY and web search, the model confirms each rule for the state and attaches a citation. A
citation is kept only when the API cited that URL or we could fetch it; otherwise it is null. On any failure
the static baseline comes back with citation null, `applies` true where it is a legal certainty for
California, and a note saying why. Results are cached by state for the life of the process; `applies` for
the context-dependent cancellation rule is recomputed per call.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from research import phones
from research.schemas import RegulationFindings

CACHE_SECONDS = 24 * 3600.0
MAX_CITATION_FETCHES = 6
QUERY_TIMEOUT_SECONDS = 25.0

# applies_when: "always" (federal or policy), "california" (certain in CA, unverified elsewhere),
# "canceling" (only when a provider is being canceled).
BASELINE: list[dict[str, Any]] = [
    {
        "key": "recording_consent",
        "rule": "Recording a confidential phone conversation needs the consent of every party (California Penal "
        "Code section 632; federal law needs one party, 18 U.S.C. 2511).",
        "jurisdiction": "California",
        "applies_when": "california",
        "what_we_do": "We disclose on the first exchange and do not record by default; the transcript is the "
        "agent's own notes, not a recording, unless the vendor is told and agrees.",
        "candidate_url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=PEN&sectionNum=632.",
        "candidate_title": "California Penal Code section 632",
    },
    {
        "key": "bot_disclosure",
        "rule": "A bot may not mislead a person about its artificial identity to influence a purchase; disclosure "
        "is the safe harbor (California Business and Professions Code sections 17940-17943, SB 1001).",
        "jurisdiction": "California",
        "applies_when": "california",
        "what_we_do": "The agent says it is an AI assistant in its second sentence on every call, before any ask, "
        "and again whenever asked whether it is a person.",
        "candidate_url": "https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?lawCode=BPC&division=7.&title=&part=3.&chapter=6.&article=",
        "candidate_title": "California Business and Professions Code, Chapter 6, Bots",
    },
    {
        "key": "tcpa_ai_voice",
        "rule": "Under the TCPA, AI-generated voices are artificial voices (FCC Declaratory Ruling, February 8 2024); "
        "calls to residential and wireless numbers need consent and must identify the caller.",
        "jurisdiction": "United States (federal)",
        "applies_when": "always",
        "what_we_do": "We call businesses at their published business numbers, only between 9am and 6pm local "
        "time, identify who is calling and why in the first two sentences, and never call a residential or "
        "mobile number without the client's confirmed consent.",
        "candidate_url": "https://www.fcc.gov/document/fcc-makes-ai-generated-voices-robocalls-illegal",
        "candidate_title": "FCC: AI-generated voices in robocalls are illegal under the TCPA",
    },
    {
        "key": "ftc_honesty",
        "rule": "Deceptive claims to obtain a price or discount are unfair or deceptive practices (FTC Act section 5, "
        "15 U.S.C. 45).",
        "jurisdiction": "United States (federal)",
        "applies_when": "always",
        "what_we_do": "The agent asserts only the eligibility on the profile (for example a full-time student with "
        "proof on file), cites only prices it was given, and never invents an outage, a hardship or a competitor "
        "quote.",
        "candidate_url": "https://www.ftc.gov/legal-library/browse/statutes/federal-trade-commission-act",
        "candidate_title": "Federal Trade Commission Act",
    },
    {
        "key": "auto_renewal_cancellation",
        "rule": "Automatic-renewal and continuous-service contracts must let the customer cancel by the method they "
        "signed up with, and online sign-ups must be cancelable online (California Business and Professions Code "
        "sections 17600-17606, as amended effective July 1 2025).",
        "jurisdiction": "California",
        "applies_when": "canceling",
        "what_we_do": "On a cancellation call the agent asks for the final bill date, any early-termination fee and a "
        "written confirmation, and notes the online cancellation path if the vendor insists on a retention pitch.",
        "candidate_url": "https://leginfo.legislature.ca.gov/faces/codes_displayText.xhtml?lawCode=BPC&division=7.&title=&part=3.&chapter=1.&article=9.",
        "candidate_title": "California Automatic Renewal Law, Business and Professions Code 17600-17606",
    },
    {
        "key": "contract_authority",
        "rule": "An agent binds the client only within the authority it was given; the phone agent has none to "
        "extend a contract, add services, upgrade tiers or cancel unless the objective says so in writing.",
        "jurisdiction": "Dormammu policy (agency law)",
        "applies_when": "always",
        "what_we_do": "The agent accepts discounts, credits and removed line items, and says it will check and "
        "report anything that needs a contract extension, an upgrade or a cancellation.",
        "candidate_url": None,
        "candidate_title": None,
    },
]

SYSTEM_PROMPT = """You are the compliance checker for Dormammu, a service whose AI phone agent calls businesses \
(movers, internet providers, dentists, utilities) on behalf of a student in the United States. The agent \
discloses that it is an AI in its second sentence, does not record calls, calls only during business hours, \
asserts only the eligibility on the client's profile, and may not extend contracts.

For the state given, confirm each listed rule with web search and return one entry per key. Keep the key \
exactly. Set applies to whether the rule binds a call placed to a business in that state for this client. \
Write what_we_do as the concrete thing the agent does. Give citation_url only as a URL you actually opened, \
preferably the official code or agency page; set it to null otherwise. Never invent a statute number or a URL. \
If the state has its own recording-consent or automatic-renewal statute, name it in the rule text with its \
section number as written in the source. Return the rules only."""

_cache: dict[str, dict[str, Any]] = {}


def clear_cache() -> None:
    _cache.clear()


def _state(state: str | None) -> str:
    text = (state or "").strip().upper()
    return text[:2] if len(text) >= 2 else ""


def _applies(rule: dict, state: str, context: dict) -> bool:
    when = rule.get("applies_when", "always")
    if when == "canceling":
        return bool(context.get("canceling"))
    if when == "california":
        return state == "CA"
    return True


def baseline(state: str | None, context: dict | None, note: str | None = None) -> list[dict]:
    """The static list, citation null. `applies` is a legal certainty for California and recomputed per call."""
    code = _state(state)
    context = context or {}
    out = []
    for rule in BASELINE:
        applies = _applies(rule, code, context)
        jurisdiction = rule["jurisdiction"]
        if rule.get("applies_when") == "california" and code and code != "CA":
            jurisdiction = f"{rule['jurisdiction']} baseline; {code} rule not verified"
        out.append(
            {
                "key": rule["key"],
                "rule": rule["rule"],
                "jurisdiction": jurisdiction,
                "applies": applies,
                "what_we_do": rule["what_we_do"],
                "citation": None,
                "note": note or "static baseline; citation not verified",
            }
        )
    return out


def _user_prompt(state: str, context: dict) -> str:
    lines = [f"State: {state or 'unknown (assume the agent may be calling into California)'}.",
             "Rules to confirm (key: rule; candidate official source):"]  # fmt: skip
    for rule in BASELINE:
        src = f" Candidate source: {rule['candidate_url']}" if rule.get("candidate_url") else ""
        lines.append(f"- {rule['key']}: {rule['rule']}{src}")
    canceling = "yes" if context.get("canceling") else "no"
    lines.append(f"Is a provider being canceled on this run: {canceling}.")
    cats = ", ".join(str(c) for c in context.get("categories") or []) or "none listed"
    lines.append(f"Categories being called: {cats}.")
    return "\n".join(lines)


async def _verified_citation(url: str | None, title: str | None, api_keys: dict[str, dict], cache: phones.PageCache) -> dict | None:
    cleaned = phones.clean_url(url)
    key = phones.url_key(cleaned)
    if not cleaned or not key:
        return None
    if key in api_keys:
        return {"title": title or api_keys[key].get("title") or key, "url": cleaned, "verified": "api"}
    if await cache.get(cleaned):
        return {"title": title or phones.host_of(cleaned) or "source", "url": cleaned, "verified": "fetched"}
    return None


async def _lookup(state: str, context: dict) -> list[dict] | None:
    """The LLM-confirmed rule table for a state, or None when the model could not be used."""
    from research import engine

    result = await engine.search_structured(
        SYSTEM_PROMPT,
        _user_prompt(state, context),
        RegulationFindings,
        location={"type": "approximate", "country": "US", **({"region": state} if state else {})},
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    findings = result.parsed if isinstance(result.parsed, RegulationFindings) else None
    if findings is None:
        return None
    api_keys = {phones.url_key(c["url"]): c for c in result.citations if phones.url_key(c.get("url"))}
    cache = phones.PageCache()
    by_key = {r["key"]: r for r in BASELINE}
    found = {f.key: f for f in findings.rules if f.key in by_key}
    extras = [f for f in findings.rules if f.key not in by_key][:3]

    async def resolve(f: Any, fallback_url: str | None, fallback_title: str | None) -> dict | None:
        cit = await _verified_citation(f.citation_url, f.citation_title, api_keys, cache)
        if cit is None and fallback_url:
            cit = await _verified_citation(fallback_url, fallback_title, api_keys, cache)
        return cit

    fetch_budget = MAX_CITATION_FETCHES
    out: list[dict] = []
    for rule in BASELINE:
        f = found.get(rule["key"])
        citation = None
        if fetch_budget > 0:
            citation = await resolve(f, rule.get("candidate_url"), rule.get("candidate_title")) if f else await (
                _verified_citation(rule.get("candidate_url"), rule.get("candidate_title"), api_keys, cache)
            )
            fetch_budget -= 1
        if citation is None and not result.web_search:
            citation = None
        out.append(
            {
                "key": rule["key"],
                "rule": (f.rule.strip() if f and f.rule and f.rule.strip() else rule["rule"]),
                "jurisdiction": (f.jurisdiction.strip() if f and f.jurisdiction.strip() else rule["jurisdiction"]),
                "applies": _applies(rule, state, context) if rule.get("applies_when") != "always" else True,
                "model_applies": bool(f.applies) if f else None,
                "what_we_do": (f.what_we_do.strip() if f and f.what_we_do.strip() else rule["what_we_do"]),
                "citation": citation,
                "note": None if citation else "citation not verified",
                "applies_when": rule.get("applies_when", "always"),
            }
        )
    for f in extras:
        citation = await _verified_citation(f.citation_url, f.citation_title, api_keys, cache)
        if citation is None:
            continue
        out.append(
            {
                "key": f.key or "other",
                "rule": f.rule.strip(),
                "jurisdiction": f.jurisdiction.strip() or state or "unknown",
                "applies": bool(f.applies),
                "model_applies": bool(f.applies),
                "what_we_do": f.what_we_do.strip(),
                "citation": citation,
                "note": "added by the model, citation verified",
                "applies_when": "model",
            }
        )
    return out


def _apply_context(rows: list[dict], state: str, context: dict) -> list[dict]:
    out = []
    by_key = {r["key"]: r for r in BASELINE}
    for row in rows:
        row = copy.deepcopy(row)
        base = by_key.get(row.get("key"))
        if base is not None:
            row["applies"] = _applies(base, state, context)
        row.pop("applies_when", None)
        out.append(row)
    return out


async def check(state: str | None, context: dict) -> list[dict]:
    """Rules for calls into `state` given this run's context. Cached by state; never raises."""
    code = _state(state)
    context = context if isinstance(context, dict) else {}
    try:
        hit = _cache.get(code)
        if hit and (asyncio.get_running_loop().time() - hit["at"]) < CACHE_SECONDS:
            return _apply_context(hit["rows"], code, context)
        rows = await _lookup(code, context)
        if rows is None:
            return baseline(code, context, note="static baseline; web search unavailable, citation not verified")
        _cache[code] = {"at": asyncio.get_running_loop().time(), "rows": rows}
        return _apply_context(rows, code, context)
    except Exception as exc:  # noqa: BLE001
        return baseline(code, context, note=f"static baseline; check failed ({type(exc).__name__})")
