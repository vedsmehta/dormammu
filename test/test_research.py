"""Research engine tests, no network: the OpenAI seam (research.engine.search_structured) and the page fetch
(research.phones.fetch_text) are monkeypatched. Shape and never-raises, phone verification, roster items,
categories from current_providers, cited plans only, the budget timeout, and the regulations fallback."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from research import engine, phones, regulations
from research.schemas import (
    CategoryFindings,
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


def fake_search(
    by_category: dict[str, CategoryFindings],
    *,
    citations: dict[str, list[dict]] | None = None,
    web_search: bool = True,
    regs: RegulationFindings | None = None,
):
    """A search_structured replacement keyed on the category label in the user prompt."""
    calls: list[str] = []

    async def search_structured(system, user, schema, *, location=None, timeout=25.0, web_search_flag=True, **_):
        calls.append(user)
        if schema is RegulationFindings:
            return engine.SearchResult(parsed=regs, citations=[], web_search=web_search, searches=1 if regs else 0)
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
    for name, reason in (
        ("Unproven Movers", "phone not confirmed in fetched sources"),
        ("Silent Movers", "no phone number found in sources"),
        ("Odd Movers", "phone in sources is not a US number"),
    ):
        assert by_name[name]["vendor"]["phone"] is None, name
        assert by_name[name]["phone_verified"] is False and by_name[name]["recommended"] is False
        assert by_name[name]["skip_reason"] == reason
    assert not any("555" in (it["vendor"]["phone"] or "") for it in proposal["items"] if not it["phone_verified"])


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


async def test_roster_without_categories_falls_back_to_track_categories():
    proposal = await engine.research(None, "call my people", ROSTER)
    assert {c["key"] for c in proposal["categories"]} == {"quote", "schedule"}
    assert len(proposal["items"]) == 2


def test_empty_request_is_minimal_but_valid():
    proposal = asyncio.run(engine.research(None, "", []))
    Proposal.model_validate(proposal)
    assert proposal["items"] == [] and proposal["categories"] == []


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
