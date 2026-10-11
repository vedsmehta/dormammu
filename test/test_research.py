"""Research engine tests, no network: the OpenAI seam (research.engine.search_structured) and the page fetch
(research.phones.fetch_text) are monkeypatched. Shape and never-raises, phone verification, roster items,
categories from current_providers, cited plans only, the budget timeout, the regulations fallback, and the
second pass (targeted phone search, the vendor's own contact page, the foreign-host refusal, price lookups,
the search and time caps)."""

from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from research import engine, phones, regulations
from research.schemas import (
    CategoryFindings,
    InfoLookup,
    PhoneLookup,
    Proposal,
    RegulationFindings,
    SourcedDiscount,
    SourcedPlan,
    VendorFinding,
)

DEMO = "I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist"
PROFILE = {
    "id": "u1",
    "display_name": "Vedant",
    "phone": "+15550009999",
    "cohort": "student",
    "prefs": {
        "new_address": {"street": "1 University Ave", "city": "Palo Alto", "state": "CA", "zip": "94301"},
        "move_date": "2026-10-25",
        "bedrooms": "1",
        "budget_ceiling_usd": 900,
        "current_providers": [{"category": "internet", "name": "Comcast", "monthly_usd": 89.99, "contract_end": None}],
        "insurance": {"dental": "Delta Dental", "health": None},
        "student_proof": "stanford.edu email on file",
        "caller_name": "Alex",
    },
}
ROSTER = [
    {"id": "v1", "owner_id": "u1", "key": "movers_a", "name": "BearHug Moving", "track": "quote", "phone": "+15550100001"},
    {"id": "v2", "owner_id": "u1", "key": "dentist", "name": "Dr. Patel Dental", "track": "schedule", "phone": "+15550100002"},
]

MOVER_PAGE = "https://example-movers.com/contact"
MOVER_PRICE_PAGE = "https://example-movers.com/pricing"
ISP_PAGE = "https://example-isp.com/student"
PAGES = {
    MOVER_PAGE: "<html><body>Call us at (650) 555‑0123 for a quote. <a href='tel:+16505550123'>call</a></body></html>",
    MOVER_PRICE_PAGE: "<html><body>1-bedroom moves from $495 flat. Student move discount 10% with ID.</body></html>",
    ISP_PAGE: "<html><body>Student Connect plan $35 per month. Contact sales online.</body></html>",
}


def finding(name: str, phone: str | None, phone_url: str | None, **kw) -> VendorFinding:
    return VendorFinding(
        name=name,
        phone_as_written=phone,
        phone_source_url=phone_url,
        website=kw.get("website"),
        address=kw.get("address"),
        why=kw.get("why", f"{name} serves Palo Alto."),
        plans=kw.get("plans", []),
        discounts=kw.get("discounts", []),
        item_asks=kw.get("item_asks", ["whether weekday moves are cheaper"]),
        opening=kw.get("opening"),
        confidence=kw.get("confidence", 0.8),
        source_urls=kw.get("source_urls", [u for u in [phone_url] if u]),
    )


def phone_lookup(name: str, phone: str | None, phone_url: str | None, **kw) -> PhoneLookup:
    return PhoneLookup(
        name=name,
        phone_as_written=phone,
        phone_source_url=phone_url,
        website=kw.get("website"),
        address=kw.get("address"),
        confidence=kw.get("confidence", 0.8),
        source_urls=kw.get("source_urls", [u for u in [phone_url] if u]),
    )


def fake_search(
    by_category: dict[str, CategoryFindings],
    *,
    citations: dict[str, list[dict]] | None = None,
    web_search: bool = True,
    regs: RegulationFindings | None = None,
    lookups: dict[str, PhoneLookup | InfoLookup] | None = None,
    lookup_citations: dict[str, list[dict]] | None = None,
):
    """A search_structured replacement: category searches keyed on the category label in the user prompt,
    second-pass lookups keyed on the vendor name. An unregistered lookup answers "searched, found nothing"."""
    calls: list[str] = []
    lookups = lookups or {}

    async def search_structured(system, user, schema, *, location=None, timeout=25.0, web_search_flag=True, **_):
        calls.append(user)
        if schema is RegulationFindings:
            return engine.SearchResult(parsed=regs, citations=[], web_search=web_search, searches=1 if regs else 0)
        if schema in (PhoneLookup, InfoLookup):
            name = user.split("\n", 1)[0].removeprefix("Vendor: ")
            answer = lookups.get(name)
            if not isinstance(answer, schema):
                answer = (
                    phone_lookup(name, None, None)
                    if schema is PhoneLookup
                    else InfoLookup(name=name, plans=[], discounts=[], source_urls=[])
                )
            return engine.SearchResult(
                parsed=answer, citations=(lookup_citations or {}).get(name, []), web_search=web_search, searches=1
            )
        for key, findings in by_category.items():
            if f"Category: {key}" in user:
                return engine.SearchResult(
                    parsed=findings, citations=(citations or {}).get(key, []), web_search=web_search, searches=2
                )
        return engine.SearchResult(parsed=CategoryFindings(vendors=[], notes=None), web_search=web_search, searches=1)

    search_structured.calls = calls
    return search_structured


def fake_fetch(pages: dict[str, str]):
    by_key = {phones.url_key(u): t for u, t in pages.items()}

    async def fetch_text(url, timeout=10.0):
        return by_key.get(phones.url_key(url))

    return fetch_text


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    regulations.clear_cache()
    monkeypatch.setattr(engine, "_web_search_unavailable", False)
    monkeypatch.setattr(phones, "fetch_text", fake_fetch({}))
    monkeypatch.setattr(engine, "search_structured", fake_search({}))
    yield
    regulations.clear_cache()


# ---------------------------------------------------------------------------------------------
# details and categories


def test_details_merge_request_wins_then_profile():
    details = engine.merge_details("Handle the move and internet, budget $800", PROFILE)
    assert details["destination"] == "Palo Alto"  # from the profile: the request names no city
    assert details["date"] == "October 25"  # from move_date
    assert details["bedrooms"] == "1-bedroom"
    assert details["budget"] == "$800"  # the request wins over budget_ceiling_usd
    assert details["state"] == "CA" and details["zip"] == "94301"
    assert details["is_move"] is True


def test_details_state_hint_without_profile():
    details = engine.merge_details(DEMO, None)
    assert details["destination"] == "Palo Alto" and details["state"] == "CA"
    assert details["new_address"] == {"street": None, "city": None, "state": None, "zip": None}


def test_categories_from_request_and_current_providers():
    details = engine.merge_details(DEMO, PROFILE)
    cats = engine.select_categories(DEMO, PROFILE, details)
    keys = [c["key"] for c in cats]
    assert keys[:3] == ["movers", "internet", "dentist"]
    assert "lower_comcast" in keys
    lower = next(c for c in cats if c["key"] == "lower_comcast")
    assert lower["track"] == "negotiate" and lower["source"] == "profile"
    assert lower["label"] == "Lower Comcast bill"
    assert "Comcast retention department phone number" in lower["query"]
    movers = cats[0]
    assert "Palo Alto" in movers["query"] and "October 25" in movers["query"] and "1-bedroom" in movers["query"]


def test_cancel_category_when_request_says_cancel_and_names_the_provider():
    request = "Cancel my Comcast internet before the move"
    cats = engine.select_categories(request, PROFILE, engine.merge_details(request, PROFILE))
    cancel = next(c for c in cats if c["key"] == "cancel_comcast")
    assert cancel["label"] == "Cancel Comcast"
    assert not any(c["key"] == "lower_comcast" for c in cats)


# ---------------------------------------------------------------------------------------------
# the proposal


async def test_shape_json_and_verified_phone(monkeypatch):
    movers = CategoryFindings(
        vendors=[
            finding(
                "Example Movers",
                "(650) 555-0123",
                MOVER_PAGE,
                website="https://example-movers.com",
                plans=[
                    SourcedPlan(name="1-bedroom flat", price_usd=495.0, unit="flat", notes=None, source_url=MOVER_PRICE_PAGE),
                    SourcedPlan(name="Hourly", price_usd=150.0, unit="per hour", notes=None, source_url=None),
                ],
                discounts=[
                    SourcedDiscount(
                        label="Student move discount",
                        who_qualifies="students with ID",
                        how_to_claim="mention it when booking",
                        source_url=MOVER_PRICE_PAGE,
                    ),
                    SourcedDiscount(label="Imaginary", who_qualifies="anyone", how_to_claim="ask", source_url="https://nowhere.example/x"),
                ],
                source_urls=[MOVER_PAGE, MOVER_PRICE_PAGE],
                opening="I'm calling for an all-in quote on a 1-bedroom move to Palo Alto on October 25; I saw the $495 flat rate, does that apply?",
            )
        ],
        notes=None,
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    monkeypatch.setattr(engine, "search_structured", fake_search({"Movers": movers}, citations={"Movers": [{"title": "Example Movers", "url": MOVER_PAGE}]}))

    proposal = await engine.research(PROFILE, DEMO, ROSTER)
    Proposal.model_validate(proposal)
    json.dumps(proposal)
    assert proposal["request"] == DEMO
    assert proposal["web_search"] is True and proposal["search_count"] >= 2
    assert proposal["details"]["destination"] == "Palo Alto"
    assert isinstance(proposal["summary"], str) and "approve" in proposal["summary"]
    assert proposal["model"]

    web = [it for it in proposal["items"] if it["source"] == "web"]
    assert len(web) == 1
    item = web[0]
    assert item["category"] == "movers" and item["track"] == "quote"
    assert item["vendor"]["phone"] == "+16505550123" and item["phone_verified"] is True
    assert item["recommended"] is True and item["skip_reason"] is None
    # api-cited contact page plus the fetched pricing page
    assert [c["verified"] for c in item["citations"]] == ["api", "fetched"]
    assert [p["name"] for p in item["plans"]] == ["1-bedroom flat"]  # the plan without a source is dropped
    assert item["plans"][0]["citation"] == 1 and item["plans"][0]["price_in_source"] is True
    assert [d["label"] for d in item["discounts"]] == ["Student move discount"]
    assert item["strategy"]["variant"] == "quote"
    assert item["strategy"]["asks"][:2] == ["student move discount if any", "no deposit above 10 percent"]
    assert "whether weekday moves are cheaper" in item["strategy"]["asks"]
    assert item["strategy"]["opening"].startswith("I'm calling for an all-in quote")
    assert "$900" in item["strategy"]["walk_away"]
    assert "1-bedroom" in item["strategy"]["objective"] and "October 25" in item["strategy"]["objective"]


async def test_phone_verification_drops_unsupported_numbers(monkeypatch):
    movers = CategoryFindings(
        vendors=[
            finding("Verified Movers", "650-555-0123", MOVER_PAGE),
            finding("Unproven Movers", "(415) 555-0199", MOVER_PAGE),  # digits not on the page
            finding("Silent Movers", None, None, source_urls=[MOVER_PRICE_PAGE]),
            finding("Odd Movers", "+44 20 7946 0958", MOVER_PAGE),  # not a US number
        ],
        notes=None,
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    monkeypatch.setattr(engine, "search_structured", fake_search({"Movers": movers}))

    proposal = await engine.research(None, "Get mover quotes for a move to Palo Alto", [], max_items_per_category=4)
    by_name = {it["vendor"]["name"]: it for it in proposal["items"]}
    assert by_name["Verified Movers"]["vendor"]["phone"] == "+16505550123"
    assert by_name["Verified Movers"]["phone_verified"] is True
    # The second pass searched for each of the three (the fake finds nothing) and none has a website to read,
    # so they stay dropped; the first-pass reason survives in phone_note.
    for name, reason in (
        ("Unproven Movers", "phone not confirmed in fetched sources"),
        ("Silent Movers", "no phone number found in sources"),
        ("Odd Movers", "phone in sources is not a US number"),
    ):
        assert by_name[name]["vendor"]["phone"] is None, name
        assert by_name[name]["phone_verified"] is False and by_name[name]["recommended"] is False
        assert by_name[name]["skip_reason"] == engine.NO_NUMBER_REASON
        assert by_name[name]["phone_note"] == f"first pass: {reason}; second pass tried a targeted search"
    assert not any("555" in (it["vendor"]["phone"] or "") for it in proposal["items"] if not it["phone_verified"])
    assert proposal["second_pass"]["phone_searches"] == 3 and proposal["second_pass"]["phones_found"] == 0
    assert proposal["second_pass"]["site_fetches"] == 0


async def test_roster_items_included_with_verified_phone():
    proposal = await engine.research(PROFILE, DEMO, ROSTER)
    roster = [it for it in proposal["items"] if it["source"] == "roster"]
    assert {it["vendor"]["name"] for it in roster} == {"BearHug Moving", "Dr. Patel Dental"}
    for it in roster:
        assert it["why"] == "already on your roster"
        assert it["phone_verified"] is True and it["recommended"] is True
        assert it["vendor"]["roster_vendor_id"] in {"v1", "v2"}
    mover = next(it for it in roster if it["vendor"]["name"] == "BearHug Moving")
    assert mover["category"] == "movers" and mover["track"] == "quote"
    dentist = next(it for it in roster if it["vendor"]["name"] == "Dr. Patel Dental")
    assert dentist["category"] == "dentist" and dentist["strategy"]["variant"] == "schedule"


async def test_current_provider_item_uses_anchor_strategy(monkeypatch):
    comcast = CategoryFindings(
        vendors=[finding("Comcast", "1-800-934-6489", "https://www.xfinity.com/support/contact-us", confidence=0.9)],
        notes=None,
    )
    monkeypatch.setattr(
        phones, "fetch_text", fake_fetch({"https://www.xfinity.com/support/contact-us": "Call 1-800-XFINITY (1-800-934-6489)"})
    )
    monkeypatch.setattr(engine, "search_structured", fake_search({"Lower Comcast bill": comcast}))
    proposal = await engine.research(PROFILE, "Lower my internet bill", [])
    items = {it["category"]: it for it in proposal["items"]}
    assert "lower_comcast" in items
    item = items["lower_comcast"]
    assert item["vendor"]["phone"] == "+18009346489" and item["phone_verified"] is True
    assert item["strategy"]["variant"] == "S1_anchor"  # $89.99 is in the objective
    assert "$89.99" in item["strategy"]["objective"] and "$89.99" in item["strategy"]["walk_away"]
    assert "student rate" in item["strategy"]["asks"]


async def test_cancel_item_uses_cancel_strategy():
    proposal = await engine.research(PROFILE, "Cancel my Comcast internet", [])
    cats = {c["key"]: c for c in proposal["categories"]}
    assert "cancel_comcast" in cats and cats["cancel_comcast"]["track"] == "negotiate"
    # No search results (fake returns no vendors), so the category has no items but the strategy templates hold.
    strategy = engine._strategy(cats["cancel_comcast"], "Comcast", proposal["details"], PROFILE, "Cancel my Comcast internet", None)
    assert strategy["variant"] == "S3_cancel"
    assert "cancellation is authorized" in strategy["objective"]


async def test_never_raises_when_search_and_fetch_fail(monkeypatch):
    async def exploding_search(*args, **kwargs):
        raise RuntimeError("boom")

    async def exploding_fetch(url, timeout=10.0):
        raise OSError("network down")

    monkeypatch.setattr(engine, "search_structured", exploding_search)
    monkeypatch.setattr(phones, "fetch_text", exploding_fetch)
    proposal = await engine.research(PROFILE, DEMO, ROSTER)
    Proposal.model_validate(proposal)
    assert proposal["errors"], "failures are reported, not raised"
    assert {it["source"] for it in proposal["items"]} == {"roster"}
    assert proposal["web_search"] is False
    assert len(proposal["regulations"]) == len(regulations.BASELINE)
    assert all(r["citation"] is None for r in proposal["regulations"])


async def test_search_error_result_is_reported_and_items_still_assemble(monkeypatch):
    async def erroring(system, user, schema, **kwargs):
        return engine.SearchResult(error="OPENAI_API_KEY not set")

    monkeypatch.setattr(engine, "search_structured", erroring)
    proposal = await engine.research(None, DEMO, ROSTER)
    assert any("OPENAI_API_KEY" in e for e in proposal["errors"])
    assert len(proposal["items"]) == 2 and proposal["web_search"] is False


async def test_overall_budget_returns_partial(monkeypatch):
    async def slow(system, user, schema, **kwargs):
        await asyncio.sleep(5)
        return engine.SearchResult(parsed=CategoryFindings(vendors=[], notes=None), web_search=True, searches=1)

    monkeypatch.setattr(engine, "search_structured", slow)
    monkeypatch.setattr(engine, "OVERALL_BUDGET_SECONDS", 0.2)
    proposal = await engine.research(PROFILE, DEMO, ROSTER)
    assert any("budget" in e for e in proposal["errors"])
    assert [it["source"] for it in proposal["items"]] == ["roster", "roster"]
    assert proposal["regulations"]  # baseline when the check did not finish
    assert proposal["second_pass"] == {"phone_searches": 0, "site_fetches": 0, "info_searches": 0, "phones_found": 0, "seconds": 0.0}
    assert proposal["seconds"] < 1.0


async def test_roster_without_categories_falls_back_to_track_categories():
    proposal = await engine.research(None, "call my people", ROSTER)
    assert {c["key"] for c in proposal["categories"]} == {"quote", "schedule"}
    assert len(proposal["items"]) == 2


def test_empty_request_is_minimal_but_valid():
    proposal = asyncio.run(engine.research(None, "", []))
    Proposal.model_validate(proposal)
    assert proposal["items"] == [] and proposal["categories"] == []


# ---------------------------------------------------------------------------------------------
# the second pass: the engine finds the number instead of asking the user for it

DENTAL_SITE = "https://quiet-dental.example"
DENTAL_PAGES = {
    DENTAL_SITE + "/": "<html><body>Quiet Dental. Gentle care in Palo Alto.</body></html>",
    DENTAL_SITE + "/contact": "<html><body>Call <a href='tel:+16505550177'>(650) 555-0177</a></body></html>",
}
MOVER_REQUEST = "Get mover quotes for a move to Palo Alto"
DENTIST_REQUEST = "Find a dentist in Palo Alto"


async def test_second_pass_targeted_search_finds_and_verifies_phone(monkeypatch):
    movers = CategoryFindings(vendors=[finding("Silent Movers", None, None, source_urls=[MOVER_PRICE_PAGE])], notes=None)
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    search = fake_search(
        {"Movers": movers},
        lookups={"Silent Movers": phone_lookup("Silent Movers", "(650) 555-0123", MOVER_PAGE, website="https://example-movers.com")},
    )
    monkeypatch.setattr(engine, "search_structured", search)

    proposal = await engine.research(None, MOVER_REQUEST, [])
    Proposal.model_validate(proposal)
    item = next(it for it in proposal["items"] if it["vendor"]["name"] == "Silent Movers")
    assert item["vendor"]["phone"] == "+16505550123" and item["phone_verified"] is True
    assert item["phone_source"] == "model_and_page" and item["skip_reason"] is None
    assert item["recommended"] is True and item["confidence"] == 0.8  # the 0.6 penalty is undone
    assert item["vendor"]["website"] == "https://example-movers.com"  # the lookup supplied it
    assert {"title": "example-movers.com", "url": MOVER_PAGE, "verified": "fetched"} in item["citations"]
    assert item["phone_note"].startswith("found by a targeted search; the digits are on example-movers.com")
    assert "no phone number found in sources" in item["phone_note"]
    sp = proposal["second_pass"]
    assert sp["phone_searches"] == 1 and sp["phones_found"] == 1 and sp["info_searches"] == 1  # no plans either
    assert sp["seconds"] >= 0
    assert any(c.startswith("Vendor: Silent Movers\nSearch query to start from: Silent Movers Palo Alto phone number") for c in search.calls)
    assert "1 more phone number" in proposal["summary"]


async def test_second_pass_accepts_number_on_a_page_the_model_cited(monkeypatch):
    directory = "https://directory.example/listing/quiet-dental"
    pages = {**DENTAL_PAGES, directory: "<html><body>Quiet Dental: (415) 555-0199</body></html>"}
    dentists = CategoryFindings(vendors=[finding("Quiet Dental", None, None, website=DENTAL_SITE)], notes=None)
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(pages))
    monkeypatch.setattr(
        engine,
        "search_structured",
        fake_search({"Dentist": dentists}, lookups={"Quiet Dental": phone_lookup("Quiet Dental", "(415) 555-0199", directory)}),
    )
    proposal = await engine.research(None, DENTIST_REQUEST, [])
    item = next(it for it in proposal["items"] if it["vendor"]["name"] == "Quiet Dental")
    # A foreign host is fine when the model cited that page and the digits are on it; it wins over the site.
    assert item["vendor"]["phone"] == "+14155550199" and item["phone_source"] == "model_and_page"
    assert {"title": "directory.example", "url": directory, "verified": "fetched"} in item["citations"]


async def test_second_pass_reads_vendor_contact_page_for_tel_link(monkeypatch):
    dentists = CategoryFindings(
        vendors=[finding("Quiet Dental", None, None, website=DENTAL_SITE, source_urls=[DENTAL_SITE])], notes=None
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch({**PAGES, **DENTAL_PAGES}))
    monkeypatch.setattr(engine, "search_structured", fake_search({"Dentist": dentists}))  # the lookup finds nothing

    proposal = await engine.research(None, DENTIST_REQUEST, [])
    item = next(it for it in proposal["items"] if it["vendor"]["name"] == "Quiet Dental")
    assert item["vendor"]["phone"] == "+16505550177" and item["phone_verified"] is True
    assert item["phone_source"] == "own_site" and item["skip_reason"] is None and item["recommended"] is True
    assert {"title": "quiet-dental.example", "url": DENTAL_SITE + "/contact", "verified": "fetched"} in item["citations"]
    assert item["phone_note"].startswith("the single number on the vendor's own page https://quiet-dental.example/contact")
    sp = proposal["second_pass"]
    assert sp["phone_searches"] == 1 and sp["phones_found"] == 1
    assert sp["site_fetches"] == 3  # the root was fetched in the first pass; /contact, /contact-us and /about are new


async def test_second_pass_refuses_number_from_foreign_host(monkeypatch):
    directory = "https://directory.example/listing/quiet-dental"
    pages = {
        **DENTAL_PAGES,
        DENTAL_SITE + "/contact": "<html><body>Front desk (650) 555-0177, billing (650) 555-0178</body></html>",
        directory: "<html><body>Quiet Dental: (415) 555-0199</body></html>",
    }
    dentists = CategoryFindings(
        vendors=[finding("Quiet Dental", None, None, website=DENTAL_SITE, source_urls=[directory])], notes=None
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(pages))
    # The lookup names a number but cites no page for it. The directory page carrying those digits was never
    # cited by the model or the API, and the vendor's own contact page shows two numbers, so nothing is proven.
    monkeypatch.setattr(
        engine,
        "search_structured",
        fake_search({"Dentist": dentists}, lookups={"Quiet Dental": phone_lookup("Quiet Dental", "(415) 555-0199", None, website=DENTAL_SITE)}),
    )
    proposal = await engine.research(None, DENTIST_REQUEST, [])
    item = next(it for it in proposal["items"] if it["vendor"]["name"] == "Quiet Dental")
    assert item["vendor"]["phone"] is None and item["phone_verified"] is False and item["recommended"] is False
    assert item["skip_reason"] == engine.NO_NUMBER_REASON
    assert item["phone_note"] == (
        "first pass: no phone number found in sources; second pass tried a targeted search and 4 pages on quiet-dental.example"
    )
    assert not any(c["url"] == directory for c in item["citations"] if c["verified"] == "api")
    assert proposal["second_pass"]["phones_found"] == 0 and proposal["second_pass"]["phone_searches"] == 1


async def test_second_pass_info_search_fills_empty_plans_with_citation(monkeypatch):
    movers = CategoryFindings(
        vendors=[finding("Example Movers", "(650) 555-0123", MOVER_PAGE, website="https://example-movers.com")], notes=None
    )
    info = InfoLookup(
        name="Example Movers",
        plans=[
            SourcedPlan(name="1-bedroom flat", price_usd=495.0, unit="flat", notes=None, source_url=MOVER_PRICE_PAGE),
            SourcedPlan(name="Hourly", price_usd=150.0, unit="per hour", notes=None, source_url="https://nowhere.example/x"),
            SourcedPlan(name="Uncited", price_usd=99.0, unit=None, notes=None, source_url=None),
        ],
        discounts=[
            SourcedDiscount(
                label="Student move discount", who_qualifies="students with ID", how_to_claim="mention it when booking", source_url=MOVER_PRICE_PAGE
            ),
            SourcedDiscount(label="Imaginary", who_qualifies="anyone", how_to_claim="ask", source_url="https://nowhere.example/y"),
        ],
        source_urls=[MOVER_PRICE_PAGE],
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    search = fake_search({"Movers": movers}, lookups={"Example Movers": info})
    monkeypatch.setattr(engine, "search_structured", search)

    proposal = await engine.research(None, MOVER_REQUEST, [])
    Proposal.model_validate(proposal)
    item = next(it for it in proposal["items"] if it["vendor"]["name"] == "Example Movers")
    assert item["phone_verified"] is True and item["phone_source"] == "model_and_page"  # untouched by the second pass
    assert [p["name"] for p in item["plans"]] == ["1-bedroom flat"]
    assert [d["label"] for d in item["discounts"]] == ["Student move discount"]
    cite = item["citations"][item["plans"][0]["citation"]]
    assert cite["url"] == MOVER_PRICE_PAGE and cite["verified"] == "fetched"
    assert item["plans"][0]["price_in_source"] is True
    assert item["discounts"][0]["citation"] == item["plans"][0]["citation"]
    sp = proposal["second_pass"]
    assert (sp["phone_searches"], sp["site_fetches"], sp["info_searches"], sp["phones_found"]) == (0, 0, 1, 0)
    assert any("Search query to start from: Example Movers prices plans student discount Palo Alto" in c for c in search.calls)


async def test_second_pass_search_cap_holds_and_phone_lookups_come_first(monkeypatch):
    vendors = [finding(f"Quiet Movers {i}", None, None) for i in range(3)]  # no phone, no website
    vendors += [finding(f"Loud Movers {i}", "(650) 555-0123", MOVER_PAGE) for i in range(7)]  # verified, no plans
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    search = fake_search({"Movers": CategoryFindings(vendors=vendors, notes=None)})
    monkeypatch.setattr(engine, "search_structured", search)

    proposal = await engine.research(None, MOVER_REQUEST, [], max_items_per_category=10)
    sp = proposal["second_pass"]
    assert sp["phone_searches"] == 3 and sp["info_searches"] == 5 and sp["site_fetches"] == 0
    assert sp["phone_searches"] + sp["info_searches"] == engine.SECOND_PASS_MAX_SEARCHES
    lookups = [c.split("\n", 1)[0] for c in search.calls if c.startswith("Vendor: ")]
    assert lookups[:3] == [f"Vendor: Quiet Movers {i}" for i in range(3)]  # phone lookups first
    assert all(name.startswith("Vendor: Loud Movers") for name in lookups[3:])  # price lookups for the dialable ones
    assert len(lookups) == 8
    assert proposal["search_count"] == 2 + 8


async def test_second_pass_prioritizes_categories_without_a_roster_vendor(monkeypatch):
    movers = CategoryFindings(vendors=[finding("Silent Movers", None, None)], notes=None)
    dentists = CategoryFindings(vendors=[finding("Quiet Dental", None, None)], notes=None)
    search = fake_search({"Movers": movers, "Dentist": dentists})
    monkeypatch.setattr(engine, "search_structured", search)
    monkeypatch.setattr(engine, "SECOND_PASS_MAX_SEARCHES", 1)

    # BearHug Moving on the roster covers movers; the dentist category has nobody to call, so it gets the slot.
    proposal = await engine.research(PROFILE, "handle the move and find a dentist", [ROSTER[0]])
    lookups = [c.split("\n", 1)[0] for c in search.calls if c.startswith("Vendor: ")]
    assert lookups == ["Vendor: Quiet Dental"]
    by_name = {it["vendor"]["name"]: it for it in proposal["items"]}
    assert by_name["Quiet Dental"]["skip_reason"] == engine.NO_NUMBER_REASON
    assert by_name["Silent Movers"]["skip_reason"] == "no phone number found in sources"  # no slot, no site: untouched
    assert by_name["Silent Movers"]["phone_note"] is None
    assert proposal["second_pass"]["phone_searches"] == 1


async def test_second_pass_time_cap_holds_and_leaves_items_untouched(monkeypatch):
    movers = CategoryFindings(vendors=[finding("Silent Movers", None, None, website="https://example-movers.com")], notes=None)
    plain = fake_search({"Movers": movers})

    async def slow_lookups(system, user, schema, **kwargs):
        if schema in (PhoneLookup, InfoLookup):
            await asyncio.sleep(5)
        return await plain(system, user, schema, **kwargs)

    monkeypatch.setattr(engine, "search_structured", slow_lookups)
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    monkeypatch.setattr(engine, "SECOND_PASS_SECONDS", 1.0)

    started = time.monotonic()
    proposal = await engine.research(None, MOVER_REQUEST, [])
    assert time.monotonic() - started < 3.0
    Proposal.model_validate(proposal)
    assert any("second-pass budget of 1 seconds exhausted" in e for e in proposal["errors"])
    item = proposal["items"][0]
    # The site read finished (example-movers.com/contact carries the number) but the search never returned,
    # and an item changes only once both are in. Nothing half-applied.
    assert item["phone_verified"] is False and item["vendor"]["phone"] is None
    assert item["skip_reason"] == "no phone number found in sources" and item["phone_note"] is None
    sp = proposal["second_pass"]
    assert sp["phone_searches"] == 1 and sp["phones_found"] == 0 and 0.9 <= sp["seconds"] <= 2.5


async def test_second_pass_skipped_when_nothing_to_do(monkeypatch):
    movers = CategoryFindings(
        vendors=[
            finding(
                "Example Movers",
                "(650) 555-0123",
                MOVER_PAGE,
                plans=[SourcedPlan(name="1-bedroom flat", price_usd=495.0, unit="flat", notes=None, source_url=MOVER_PAGE)],
            )
        ],
        notes=None,
    )
    monkeypatch.setattr(phones, "fetch_text", fake_fetch(PAGES))
    search = fake_search({"Movers": movers})
    monkeypatch.setattr(engine, "search_structured", search)
    proposal = await engine.research(None, MOVER_REQUEST, [])
    assert proposal["second_pass"] == {"phone_searches": 0, "site_fetches": 0, "info_searches": 0, "phones_found": 0, "seconds": 0.0}
    assert not any(c.startswith("Vendor: ") for c in search.calls)
    assert "second pass" not in proposal["summary"].lower()


def test_site_urls_root_then_contact_paths():
    assert engine._site_urls("https://example-movers.com") == [
        "https://example-movers.com/",
        "https://example-movers.com/contact",
        "https://example-movers.com/contact-us",
        "https://example-movers.com/about",
    ]
    # A deep website URL is read too, and a path it already is gets skipped in favor of the next one.
    assert engine._site_urls("https://www.example-movers.com/contact?utm_source=x") == [
        "https://www.example-movers.com/",
        "https://www.example-movers.com/contact",
        "https://www.example-movers.com/contact-us",
        "https://www.example-movers.com/about",
        "https://www.example-movers.com/locations",
    ]


# ---------------------------------------------------------------------------------------------
# phones


def test_phone_normalize_and_find():
    assert phones.normalize("(650) 329‑2100") == "+16503292100"
    assert phones.normalize("1-800-934-6489") == "+18009346489"
    assert phones.normalize("+1 650.329.2100") == "+16503292100"
    assert phones.normalize("650-329-210") is None
    assert phones.normalize("+44 20 7946 0958") is None
    assert phones.normalize("123-456-7890") is None  # area code cannot start with 1
    text = "Call (650) 329-2100 or <a href='tel:+14155550100'>here</a>. Fax 650 329 2101."
    assert phones.find_phones(text) == ["+14155550100", "+16503292100", "+16503292101"]
    assert phones.digits_present("+16503292100", text) is True
    assert phones.digits_present("+16503299999", text) is False
    assert phones.mask("+16503292100") == "+1650***2100"


def test_single_page_phone_ignores_numbers_hidden_in_markup():
    # A UUID segment in a font URL inside a style block is not the vendor's number (seen live on a Squarespace
    # site: the page's only ten-digit run was "ab22-2585362458e6/greek.woff2").
    style = "<style>@font-face { src: url('https://cdn.example/fonts/1c678f97-ab22-2585362458e6/greek.woff2'); }</style>"
    assert phones.single_page_phone(f"<html><head>{style}</head><body>Coached sessions, 50% off for students.</body></html>") is None
    assert phones.single_page_phone(f"<html>{style}<body>Call (650) 555-0199 today.</body></html>") == "+16505550199"
    assert phones.single_page_phone("<script>var id = '2585362458';</script><body><a href='tel:6505550199'>call</a></body>") == "+16505550199"
    # A style block cut off by MAX_FETCH_BYTES has no closing tag; its contents still do not count as text.
    truncated = "<html><body>Welcome.</body><style>.a { background: url('x/ab22-2585362458e6/y.png') }"
    assert phones.visible_text(truncated) == "Welcome."
    assert phones.single_page_phone(truncated) is None
    # Several visible numbers still mean "cannot tell".
    assert phones.single_page_phone("<body>Front desk (650) 555-0177, billing (650) 555-0178</body>") is None


def test_url_helpers():
    assert phones.clean_url("https://www.paloalto.gov/About/Contact-Us?utm_source=openai#x") == "https://www.paloalto.gov/About/Contact-Us"
    assert phones.url_key("https://WWW.Example.com/a/?utm_source=openai") == phones.url_key("https://example.com/a")
    assert phones.clean_url("mailto:x@y.z") is None and phones.url_key(None) is None
    assert phones.host_of("https://www.xfinity.com/learn") == "xfinity.com"


async def test_fetch_text_never_raises(monkeypatch):
    import httpx

    class Boom(httpx.AsyncClient):
        async def get(self, *a, **k):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    assert await phones.fetch_text("https://example.com") is None
    assert await phones.fetch_text("not a url") is None


# ---------------------------------------------------------------------------------------------
# regulations


async def test_regulations_fallback_when_search_unavailable(monkeypatch):
    async def no_model(system, user, schema, **kwargs):
        return engine.SearchResult(error="OPENAI_API_KEY not set")

    monkeypatch.setattr(engine, "search_structured", no_model)
    rules = await regulations.check("CA", {"canceling": True})
    assert len(rules) == len(regulations.BASELINE)
    by_key = {r["key"]: r for r in rules}
    assert by_key["recording_consent"]["applies"] is True and by_key["recording_consent"]["citation"] is None
    assert by_key["bot_disclosure"]["applies"] is True
    assert by_key["tcpa_ai_voice"]["applies"] is True and "9am and 6pm" in by_key["tcpa_ai_voice"]["what_we_do"]
    assert by_key["ftc_honesty"]["applies"] is True
    assert by_key["auto_renewal_cancellation"]["applies"] is True
    assert by_key["contract_authority"]["applies"] is True
    assert all(r["note"] for r in rules)
    not_canceling = await regulations.check("CA", {"canceling": False})
    assert {r["key"]: r["applies"] for r in not_canceling}["auto_renewal_cancellation"] is False
    texas = await regulations.check("TX", {})
    assert {r["key"]: r["applies"] for r in texas}["recording_consent"] is False
    assert "TX rule not verified" in {r["key"]: r["jurisdiction"] for r in texas}["recording_consent"]


async def test_regulations_cached_by_state_and_citations_verified(monkeypatch):
    from research.schemas import RegulationFinding

    calls = []
    url = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=PEN&sectionNum=632."

    async def model(system, user, schema, **kwargs):
        calls.append(schema)
        rules = [
            RegulationFinding(
                key="recording_consent",
                rule="Penal Code 632 needs all-party consent.",
                jurisdiction="California",
                applies=True,
                what_we_do="Disclose; do not record.",
                citation_title="Penal Code 632",
                citation_url=url,
            ),
            RegulationFinding(
                key="bot_disclosure",
                rule="B&P 17940.",
                jurisdiction="California",
                applies=True,
                what_we_do="Say it is an AI.",
                citation_title=None,
                citation_url="https://made-up.example/law",
            ),
        ]
        return engine.SearchResult(parsed=RegulationFindings(rules=rules), citations=[{"title": "Penal Code 632", "url": url}], web_search=True, searches=1)

    monkeypatch.setattr(engine, "search_structured", model)
    monkeypatch.setattr(phones, "fetch_text", fake_fetch({}))
    rules = await regulations.check("CA", {"canceling": False})
    by_key = {r["key"]: r for r in rules}
    assert by_key["recording_consent"]["citation"] == {"title": "Penal Code 632", "url": url, "verified": "api"}
    assert by_key["bot_disclosure"]["citation"] is None  # neither cited by the API nor fetchable
    assert by_key["bot_disclosure"]["note"] == "citation not verified"
    assert "applies_when" not in by_key["recording_consent"]
    again = await regulations.check("ca", {"canceling": True})
    assert len(calls) == 1, "second call for the same state hits the cache"
    assert {r["key"]: r["applies"] for r in again}["auto_renewal_cancellation"] is True


# ---------------------------------------------------------------------------------------------
# live


@pytest.mark.live
async def test_live_research_small(monkeypatch):
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY in .env")
    monkeypatch.undo()  # use the real search_structured and fetch_text
    proposal = await engine.research(PROFILE, "Find a dentist near Palo Alto accepting new patients", [], max_items_per_category=1)
    Proposal.model_validate(proposal)
    assert isinstance(proposal["web_search"], bool)
    for it in proposal["items"]:
        if it["vendor"]["phone"]:
            assert it["phone_verified"] is True and it["citations"]
