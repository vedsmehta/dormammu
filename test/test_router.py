"""Router tests: cache hit skips the LLM, unknown keys dropped, empty LLM -> fallback, cap, concrete fallback
objectives for the student-moving use case, per-track cohort asks, and the prior-context hook (insights first,
last outcome as fallback, one compute_insights per plan)."""

from __future__ import annotations

import os
import sys
import types
from datetime import datetime

import pytest
from pydantic import BaseModel

from router import llm
from router import plan as router
from router.cohorts import COHORTS, ask_line, cohort_asks

VENDORS = [
    {"id": "v1", "owner_id": "u1", "key": "bearhug", "name": "BearHug Moving", "track": "quote", "phone": "+1x"},
    {"id": "v2", "owner_id": "u1", "key": "swift", "name": "Swift Movers", "track": "quote", "phone": "+1x"},
    {"id": "v3", "owner_id": "u1", "key": "chen", "name": "Dr. Chen", "track": "schedule", "phone": "+1x"},
    {"id": "v4", "owner_id": "u1", "key": "comcast", "name": "Comcast", "track": "negotiate", "phone": "+1x"},
]
PROFILE = {"id": "u1", "display_name": "Vedant", "phone": "+1x", "prefs": {}, "cohort": "student"}
STUDENT = COHORTS["student"]
DEMO = "I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist"
REAL_ANALYSIS_FUNCS = router._analysis_funcs


class FakeStore:
    def __init__(self, vendors=None, profile=PROFILE, outcomes=None):
        self.vendors = VENDORS if vendors is None else vendors
        self.profile = profile
        self.cache: dict[str, list[dict]] = {}
        self.outcomes = outcomes or []

    def get_plan_cache(self, request_hash):
        return self.cache.get(request_hash)

    def put_plan_cache(self, request_hash, plans):
        self.cache[request_hash] = plans

    def list_vendors(self, user_id):
        return self.vendors

    def get_profile(self, user_id):
        return self.profile

    def last_outcomes_for_vendor(self, user_id, vendor_key, limit=3):
        return [r for r in self.outcomes if r.get("vendor_key") == vendor_key][:limit]


def fake_llm(plans):
    """A structured() replacement returning the given plan dicts through the caller's schema."""

    async def structured(system, user, schema, timeout=20.0):
        if plans is None:
            return None
        return schema(plans=plans)

    return structured


@pytest.fixture(autouse=True)
def cap5(monkeypatch):
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "5")


@pytest.fixture(autouse=True)
def no_analysis(monkeypatch):
    """Tests are deterministic whether or not analysis/ exists: the insights path is opt-in per test."""
    router.clear_context_cache()
    monkeypatch.setattr(router, "_analysis_funcs", lambda: None)
    yield
    router.clear_context_cache()


def test_request_hash_normalizes():
    assert router.request_hash("  Hello World ") == router.request_hash("hello world")
    assert len(router.request_hash("x")) == 64


async def test_cache_hit_skips_llm(monkeypatch):
    store = FakeStore()
    cached = [{"vendor_key": "bearhug", "track": "quote", "objective": "cached", "voice_gender": "female"}]
    store.cache[router.request_hash("Move me")] = cached

    async def boom(*args, **kwargs):
        raise AssertionError("LLM must not be called on a cache hit")

    monkeypatch.setattr(llm, "structured", boom)
    assert await router.plan(store, "  move ME ", "u1") == cached


async def test_unknown_keys_dropped_and_roster_track_wins(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(
        llm,
        "structured",
        fake_llm(
            [
                {"vendor_key": "bearhug", "track": "quote", "objective": "Quote the move.", "voice_gender": "male"},
                {"vendor_key": "stranger", "track": "quote", "objective": "Call a stranger.", "voice_gender": "male"},
                {"vendor_key": "chen", "track": "negotiate", "objective": "Book a cleaning.", "voice_gender": "female"},
                {"vendor_key": "chen", "track": "schedule", "objective": "Duplicate.", "voice_gender": "female"},
            ]
        ),
    )
    plans = await router.plan(store, "I'm moving to SF next month, handle it", "u1")
    assert [p["vendor_key"] for p in plans] == ["bearhug", "chen"]
    assert plans[1]["track"] == "schedule"
    assert plans[0]["voice_gender"] == "male"
    assert all(set(p) == {"vendor_key", "track", "objective", "voice_gender"} for p in plans)


async def test_cohort_asks_injected_per_track_and_cached(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(
        llm,
        "structured",
        fake_llm(
            [
                {"vendor_key": "comcast", "track": "negotiate", "objective": "Lower it", "voice_gender": "female"},
                {"vendor_key": "bearhug", "track": "quote", "objective": "Quote it.", "voice_gender": "male"},
                {"vendor_key": "chen", "track": "schedule", "objective": "Book it.", "voice_gender": "female"},
            ]
        ),
    )
    plans = await router.plan(store, "lower my internet bill", "u1")
    assert plans[0]["objective"] == "Lower it. " + ask_line(STUDENT, "negotiate")
    assert plans[1]["objective"] == "Quote it. " + ask_line(STUDENT, "quote")
    assert plans[2]["objective"] == "Book it. " + ask_line(STUDENT, "schedule")
    assert store.cache[router.request_hash("lower my internet bill")] == plans
    assert all(p["objective"].count("Ask for:") == 1 for p in plans)
    # The specific student asks, per track, as docs/USE-CASE.md states them.
    assert "waive the installation or activation fee" in plans[0]["objective"]
    assert "first month free if offered" in plans[0]["objective"]
    assert "no deposit above 10 percent" in plans[1]["objective"]
    assert "accepts student insurance or self-pay rate" in plans[2]["objective"]


def test_cohort_asks_config():
    assert cohort_asks(STUDENT, "quote") == ["student move discount if any", "no deposit above 10 percent"]
    assert cohort_asks(STUDENT, "negotiate")[0] == "student rate"
    assert cohort_asks(STUDENT, "schedule")[0] == "new patient"
    assert cohort_asks(STUDENT, None) == STUDENT["asks"]
    assert cohort_asks(STUDENT, "unknown-track") == STUDENT["asks"]
    assert cohort_asks(None, "quote") == []
    assert ask_line(None, "quote") == ""


async def test_empty_llm_falls_back(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(llm, "structured", fake_llm([]))
    plans = await router.plan(store, "I need a dentist appointment", "u1")
    assert [p["vendor_key"] for p in plans] == ["chen"]
    assert plans[0]["objective"]
    assert ask_line(STUDENT, "schedule") in plans[0]["objective"]


async def test_none_and_exception_fall_back(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(llm, "structured", fake_llm(None))
    assert [p["vendor_key"] for p in await router.plan(store, "book the dentist", "u1")] == ["chen"]

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(llm, "structured", boom)
    plans = await router.plan(store, "something with no keywords", "u1")
    assert [p["vendor_key"] for p in plans] == ["bearhug", "swift", "chen", "comcast"]


async def test_cap_enforced(monkeypatch):
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "2")
    store = FakeStore()
    monkeypatch.setattr(
        llm,
        "structured",
        fake_llm(
            [{"vendor_key": v["key"], "track": v["track"], "objective": "x", "voice_gender": "female"} for v in VENDORS]
        ),
    )
    assert len(await router.plan(store, "handle everything", "u1")) == 2
    assert len(router.fallback_plans(VENDORS, "handle everything")) == 2


def _keys(vendors, request):
    return [p["vendor_key"] for p in router.fallback_plans(vendors, request)]


def test_fallback_tracks():
    assert _keys(VENDORS, "I need a dentist appointment") == ["chen"]
    assert _keys(VENDORS, "cancel my internet") == ["comcast"]
    assert _keys(VENDORS, "get moving quotes") == ["bearhug", "swift"]
    assert _keys(VENDORS, "I'm moving to SF next month, handle it") == ["bearhug", "swift", "chen", "comcast"]
    assert _keys(VENDORS, DEMO) == ["bearhug", "swift", "chen", "comcast"]
    assert _keys(VENDORS, "hello there") == ["bearhug", "swift", "chen", "comcast"]
    assert _keys(VENDORS, "remove the facebook booking") == ["chen"]
    only_quote = [v for v in VENDORS if v["track"] == "quote"]
    assert _keys(only_quote, "dentist") == ["bearhug", "swift"]
    assert router.fallback_plans([], "anything") == []
    for p in router.fallback_plans(VENDORS, "hello"):
        assert p["objective"].endswith(".")
        assert p["voice_gender"] in ("female", "male")


def test_parse_request_details():
    assert router.parse_request_details(DEMO) == {
        "date": "October 25", "bedrooms": "1-bedroom", "origin": None, "destination": "Palo Alto", "budget": None,
    }  # fmt: skip
    d = router.parse_request_details("Moving from San Jose to Palo Alto on 10/25, studio, under $900")
    assert d == {"date": "October 25", "bedrooms": "studio", "origin": "San Jose", "destination": "Palo Alto",
                 "budget": "$900"}  # fmt: skip
    assert router.parse_request_details("I'm moving to SF next month, handle it")["date"] == "next month"
    assert router.parse_request_details("I'm moving to SF next month, handle it")["destination"] == "SF"
    assert router.parse_request_details("book the dentist on the 3rd")["date"] == "the 3rd"
    assert router.parse_request_details("two bedroom move on Sept 1st")["bedrooms"] == "2-bedroom"
    assert router.parse_request_details("two bedroom move on Sept 1st")["date"] == "September 1"
    assert router.parse_request_details("budget is 1,200 dollars")["budget"] == "$1,200"
    assert all(v is None for v in router.parse_request_details("").values())
    assert all(v is None for v in router.parse_request_details("hello there").values())


def _objective(request, key):
    return next(p["objective"] for p in router.fallback_plans(VENDORS, request) if p["vendor_key"] == key)


def test_fallback_objectives_carry_the_details():
    quote = _objective(DEMO, "bearhug")
    assert "to move a 1-bedroom to Palo Alto on October 25" in quote
    for phrase in ("all-in", "flat or hourly", "stairs", "packing is extra", "dates they have open"):
        assert phrase in quote
    internet = _objective(DEMO, "comcast")
    assert internet.startswith("Set up internet with Comcast at the client's new address in Palo Alto")
    assert "move-in on October 25" in internet
    for phrase in ("new-customer promotion", "student rate", "no contract", "installation fee waived", "install date"):
        assert phrase in internet
    dentist = _objective(DEMO, "chen")
    assert "new-patient dental cleaning with Dr. Chen" in dentist
    assert "around the move on October 25" in dentist
    assert "self-pay" in dentist

    routed = _objective("Moving from San Jose to Palo Alto on 10/25, studio, under $900", "swift")
    assert "to move a studio from San Jose to Palo Alto on October 25" in routed


def test_fallback_objectives_without_a_date_say_so():
    quote = _objective("I'm moving to Berkeley, 2BR, handle it", "bearhug")
    assert "to move a 2-bedroom to Berkeley, date to be confirmed:" in quote
    assert "None" not in quote
    internet = _objective("I'm moving to Berkeley, 2BR, handle it", "comcast")
    assert "move-in date to be confirmed" in internet
    bare = _objective("moving, handle it", "bearhug")
    assert "for the move, date to be confirmed" in bare
    relative = _objective("I'm moving to SF next month, handle it", "bearhug")
    assert "for the move to SF next month:" in relative


def test_fallback_negotiate_distinguishes_existing_bill_from_new_service():
    existing = _objective("lower my internet bill", "comcast")
    assert existing.startswith("Review the client's current bill with Comcast")
    assert "retention offer" in existing and "no contract extension" in existing
    assert "Set up internet" not in existing
    with_budget = _objective("lower my internet bill to under $60", "comcast")
    assert "under $60 a month" in with_budget
    # A move with an explicit bill word stays on the retention path.
    moving_bill = _objective("moving next month, lower my internet bill before I go", "comcast")
    assert moving_bill.startswith("Review the client's current bill")


def test_system_prompt_names_the_scenario():
    text = router.SYSTEM_PROMPT.format(cap=5)
    for phrase in ("Stanford student", "off-campus apartment", "student budget", "Palo Alto on October 25",
                   "1-bedroom", "flat or hourly", "installation fee waived", "no contract",
                   "new-patient dental cleaning", "date to be confirmed", "At most 5 calls"):  # fmt: skip
        assert phrase in text
    assert "{cap}" not in text
    prompt = router._user_prompt(DEMO, VENDORS, PROFILE, STUDENT)
    assert "Details found in the request: date October 25, bedrooms 1-bedroom, destination Palo Alto" in prompt
    assert "quote: student move discount if any, no deposit above 10 percent" in prompt
    assert "negotiate: student rate, waive the installation or activation fee" in prompt
    assert "+1x" not in prompt


async def test_plan_never_raises():
    class BrokenStore:
        def get_plan_cache(self, key):
            raise RuntimeError("db down")

        def list_vendors(self, user_id):
            raise RuntimeError("db down")

    assert await router.plan(BrokenStore(), "move me", "u1") == []
    assert await router.plan(FakeStore(vendors=[]), "move me", "u1") == []


def _row(vendor_key, name, outcome, ended="2026-10-03T15:12:00-07:00"):
    return {"vendor_key": vendor_key, "target_name": name, "track": outcome["track"], "outcome": outcome,
            "ended_at": ended, "state": "done"}  # fmt: skip


QUOTE_1240 = {"track": "quote", "amount_usd": 1240, "unit": "flat", "caveats": [], "note": None}


def test_prior_context_quote():
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving", QUOTE_1240)])
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    assert router.prior_context_detail(store, "u1", "bearhug", "quote")["source"] == "last_outcome"


def test_prior_context_schedule():
    slot = "2026-10-15T14:30:00-07:00"
    day = f"{datetime.fromisoformat(slot):%a}"
    store = FakeStore(outcomes=[_row("chen", "Dr. Chen", {"track": "schedule", "slot_iso": slot, "confirmed": True, "note": None})])
    assert router.prior_context(store, "u1", "chen", "schedule") == f"Last time (Oct 3) Dr. Chen booked {day} 2:30pm."


def test_prior_context_negotiate():
    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 25, "effective_date": "2026-11-01", "note": None}
    store = FakeStore(outcomes=[_row("comcast", "Comcast", outcome)])
    assert router.prior_context(store, "u1", "comcast", "negotiate") == (
        "Last time (Oct 3) Comcast reduced the bill by $25/mo effective Nov 1."
    )


def test_prior_context_none_when_nothing_useful():
    assert router.prior_context(FakeStore(), "u1", "bearhug", "quote") is None
    assert router.prior_context_detail(FakeStore(), "u1", "bearhug", "quote") == {"text": None, "source": "none"}
    empty = {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": "voicemail"}
    assert router.prior_context(FakeStore(outcomes=[_row("bearhug", "BearHug Moving", empty)]), "u1", "bearhug", "quote") is None

    class Broken:
        def last_outcomes_for_vendor(self, *a, **k):
            raise RuntimeError("db down")

    assert router.prior_context(Broken(), "u1", "bearhug", "quote") is None


def test_prior_context_skips_empty_to_older_row():
    empty = {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": "voicemail"}
    good = {"track": "quote", "amount_usd": 980.5, "unit": None, "caveats": [], "note": None}
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving", empty), _row("bearhug", "BearHug Moving", good, ended="2026-09-20T10:00:00Z")])
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Sep 20) BearHug Moving quoted $980.50."


# ---------------------------------------------------------------------------------------------
# Self-improvement hook: the analysis layer first, last outcome as the fallback, one compute per plan.

INSIGHT = "BearHug Moving answered 3 of 3 calls and was the cheapest mover twice; ask for the flat rate."


class FakeAnalysis:
    """Stands in for analysis.insights.compute_insights and analysis.context.prior_context_from_insights."""

    def __init__(self, sentence_for=None, compute_error=None, context_error=None, report=None):
        self.sentence_for = sentence_for if sentence_for is not None else {"bearhug": INSIGHT}
        self.compute_error = compute_error
        self.context_error = context_error
        self.report = report
        self.compute_calls = 0
        self.compute_kwargs: list[dict] = []

    def compute_insights(self, store, user_id, **kwargs):
        self.compute_calls += 1
        self.compute_kwargs.append(kwargs)
        if self.compute_error:
            raise self.compute_error
        if self.report is not None:
            return self.report
        return {"generated_at": "2026-10-11T20:00:00+00:00", "user_id": user_id,
                "recommendations": [{"kind": "call_first", "vendor_key": "bearhug"}]}  # fmt: skip

    def prior_context_from_insights(self, insights, vendor_key, track):
        if self.context_error:
            raise self.context_error
        return self.sentence_for.get(vendor_key, "")

    def install(self, monkeypatch):
        monkeypatch.setattr(router, "_analysis_funcs", lambda: (self.compute_insights, self.prior_context_from_insights))
        return self


def test_prior_context_prefers_insights_then_falls_back(monkeypatch):
    fake = FakeAnalysis().install(monkeypatch)
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving", QUOTE_1240), _row("swift", "Swift Movers", QUOTE_1240)])
    assert router.prior_context(store, "u1", "bearhug", "quote") == INSIGHT
    assert router.prior_context_detail(store, "u1", "bearhug", "quote") == {"text": INSIGHT, "source": "insights"}
    # No insight sentence for swift: the last substantive outcome still speaks.
    assert router.prior_context(store, "u1", "swift", "quote") == "Last time (Oct 3) Swift Movers quoted $1,240 flat."
    assert router.prior_context_detail(store, "u1", "swift", "quote")["source"] == "last_outcome"
    assert router.prior_context_detail(store, "u1", "chen", "schedule") == {"text": None, "source": "none"}
    assert fake.compute_calls == 1
    assert fake.compute_kwargs[0] == {"max_runs": router.DEFAULT_INSIGHTS_MAX_RUNS}


def test_insights_failures_fall_back_quietly(monkeypatch):
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving", QUOTE_1240)])
    FakeAnalysis(compute_error=RuntimeError("analysis down")).install(monkeypatch)
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    router.clear_context_cache()
    FakeAnalysis(context_error=ValueError("bad report")).install(monkeypatch)
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    router.clear_context_cache()
    FakeAnalysis(report=["not", "a", "dict"]).install(monkeypatch)
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    assert router.plan_context(store, "u1")["source"] == "none"


def test_insights_signature_without_max_runs(monkeypatch):
    fake = FakeAnalysis()

    def old_style(store, user_id):
        return fake.compute_insights(store, user_id)

    monkeypatch.setattr(router, "_analysis_funcs", lambda: (old_style, fake.prior_context_from_insights))
    assert router.prior_context(FakeStore(), "u1", "bearhug", "quote") == INSIGHT
    assert fake.compute_calls == 1


async def test_insights_computed_once_per_plan(monkeypatch):
    fake = FakeAnalysis().install(monkeypatch)
    store = FakeStore()
    monkeypatch.setattr(llm, "structured", fake_llm([]))
    plans = await router.plan(store, DEMO, "u1")
    assert len(plans) == 4
    assert fake.compute_calls == 1
    for p in plans:
        router.prior_context(store, "u1", p["vendor_key"], p["track"])
    router.plan_context(store, "u1")
    assert fake.compute_calls == 1, "five prior_context calls and plan_context must reuse the warm insights"
    # The next plan() refreshes once; a different user is a separate cache entry.
    await router.plan(store, DEMO, "u1")
    assert fake.compute_calls == 2
    router.prior_context(store, "u2", "bearhug", "quote")
    assert fake.compute_calls == 3
    router.clear_context_cache()
    router.prior_context(store, "u1", "bearhug", "quote")
    assert fake.compute_calls == 4


def test_insights_cache_expires(monkeypatch):
    fake = FakeAnalysis().install(monkeypatch)
    monkeypatch.setenv("INSIGHTS_CACHE_SECONDS", "0")
    store = FakeStore()
    router.prior_context(store, "u1", "bearhug", "quote")
    router.prior_context(store, "u1", "bearhug", "quote")
    assert fake.compute_calls == 2


def test_plan_context_shape(monkeypatch):
    FakeAnalysis().install(monkeypatch)
    ctx = router.plan_context(FakeStore(), "u1")
    assert ctx["source"] == "analysis"
    assert ctx["vendors_with_insight"] == ["bearhug"]
    assert ctx["context"] == {"bearhug": INSIGHT}
    assert ctx["n_recommendations"] == 1
    assert ctx["generated_at"] == "2026-10-11T20:00:00+00:00"
    assert ctx["insights"]["user_id"] == "u1"

    router.clear_context_cache()
    monkeypatch.setattr(router, "_analysis_funcs", lambda: None)
    assert router.plan_context(FakeStore(), "u1") == {
        "source": "none", "insights": {}, "context": {}, "vendors_with_insight": [],
        "n_recommendations": 0, "generated_at": None,
    }  # fmt: skip

    class Broken:
        pass

    assert router.plan_context(Broken(), "u1")["source"] == "none"


def test_analysis_import_paths(monkeypatch):
    """The real import path: a fake analysis package in sys.modules is used; a missing one means no insights."""
    monkeypatch.setattr(router, "_analysis_funcs", REAL_ANALYSIS_FUNCS)
    fake = FakeAnalysis()
    pkg = types.ModuleType("analysis")
    insights_mod = types.ModuleType("analysis.insights")
    insights_mod.compute_insights = fake.compute_insights
    context_mod = types.ModuleType("analysis.context")
    context_mod.prior_context_from_insights = fake.prior_context_from_insights
    pkg.insights, pkg.context = insights_mod, context_mod
    monkeypatch.setitem(sys.modules, "analysis", pkg)
    monkeypatch.setitem(sys.modules, "analysis.insights", insights_mod)
    monkeypatch.setitem(sys.modules, "analysis.context", context_mod)
    assert router.prior_context(FakeStore(), "u1", "bearhug", "quote") == INSIGHT
    assert fake.compute_calls == 1

    router.clear_context_cache()
    monkeypatch.setitem(sys.modules, "analysis", None)  # import raises ModuleNotFoundError (an ImportError)
    monkeypatch.setitem(sys.modules, "analysis.insights", None)
    monkeypatch.setitem(sys.modules, "analysis.context", None)
    assert router._analysis_funcs() is None
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving", QUOTE_1240)])
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    assert router.plan_context(store, "u1")["insights"] == {}


class Tiny(BaseModel):
    answer: int


@pytest.mark.live
async def test_live_structured_helper():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY in .env")
    result = await llm.structured("Reply with the integer only.", "What is 6 times 7?", Tiny, timeout=30.0)
    assert isinstance(result, Tiny)
    assert result.answer == 42
