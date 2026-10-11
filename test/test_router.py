"""Router tests: cache hit skips the LLM, unknown keys dropped, empty LLM -> fallback, cap, prior context."""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from pydantic import BaseModel

from router import llm
from router import plan as router
from router.cohorts import COHORTS

VENDORS = [
    {"id": "v1", "owner_id": "u1", "key": "bearhug", "name": "BearHug Moving", "track": "quote", "phone": "+1x"},
    {"id": "v2", "owner_id": "u1", "key": "swift", "name": "Swift Movers", "track": "quote", "phone": "+1x"},
    {"id": "v3", "owner_id": "u1", "key": "chen", "name": "Dr. Chen", "track": "schedule", "phone": "+1x"},
    {"id": "v4", "owner_id": "u1", "key": "comcast", "name": "Comcast", "track": "negotiate", "phone": "+1x"},
]
PROFILE = {"id": "u1", "display_name": "Vedant", "phone": "+1x", "prefs": {}, "cohort": "student"}
ASK_LINE = "Ask for: " + ", ".join(COHORTS["student"]["asks"]) + "."


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


async def test_cohort_asks_injected_and_cached(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(
        llm,
        "structured",
        fake_llm([{"vendor_key": "comcast", "track": "negotiate", "objective": "Lower it", "voice_gender": "female"}]),
    )
    plans = await router.plan(store, "lower my internet bill", "u1")
    assert plans[0]["objective"] == "Lower it. " + ASK_LINE
    assert store.cache[router.request_hash("lower my internet bill")] == plans
    assert plans[0]["objective"].count("Ask for:") == 1


async def test_empty_llm_falls_back(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(llm, "structured", fake_llm([]))
    plans = await router.plan(store, "I need a dentist appointment", "u1")
    assert [p["vendor_key"] for p in plans] == ["chen"]
    assert plans[0]["objective"]
    assert ASK_LINE in plans[0]["objective"]


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
    assert _keys(VENDORS, "hello there") == ["bearhug", "swift", "chen", "comcast"]
    assert _keys(VENDORS, "remove the facebook booking") == ["chen"]
    only_quote = [v for v in VENDORS if v["track"] == "quote"]
    assert _keys(only_quote, "dentist") == ["bearhug", "swift"]
    assert router.fallback_plans([], "anything") == []
    for p in router.fallback_plans(VENDORS, "hello"):
        assert p["objective"].endswith(".")
        assert p["voice_gender"] in ("female", "male")


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
            "ended_at": ended, "state": "done"}


def test_prior_context_quote():
    store = FakeStore(outcomes=[_row("bearhug", "BearHug Moving",
                                     {"track": "quote", "amount_usd": 1240, "unit": "flat", "caveats": [], "note": None})])
    assert router.prior_context(store, "u1", "bearhug", "quote") == "Last time (Oct 3) BearHug Moving quoted $1,240 flat."


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


class Tiny(BaseModel):
    answer: int


@pytest.mark.live
async def test_live_structured_helper():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY in .env")
    result = await llm.structured("Reply with the integer only.", "What is 6 times 7?", Tiny, timeout=30.0)
    assert isinstance(result, Tiny)
    assert result.answer == 42
