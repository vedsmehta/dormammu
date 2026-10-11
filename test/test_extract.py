"""Extractor tests: clean, rambling, empty, no Summary line, voicemail one-sided. Valid Outcome, never throws."""

from __future__ import annotations

import pytest

from aggregator import extract as ex
from router import llm

SHAPES = {
    "quote": {"track", "amount_usd", "unit", "caveats", "note"},
    "schedule": {"track", "slot_iso", "confirmed", "note"},
    "negotiate": {"track", "action", "saved_usd", "effective_date", "note"},
}

CLEAN_TURNS = [
    {"speaker": "agent", "text": "Hi, this is an AI assistant calling on behalf of Vedant about a move to SF."},
    {"speaker": "callee", "text": "Sure, a one bedroom from Palo Alto is twelve forty flat, includes the truck."},
    {"speaker": "agent", "text": "Does that include packing?"},
    {"speaker": "callee", "text": "No, packing is extra, two hundred."},
    {"speaker": "agent", "text": "Summary: BearHug quoted $1,240 flat for a one bedroom move, packing not included."},
]
RAMBLING = (
    "Agent: Hi, this is an AI assistant calling on behalf of Vedant. Callee: Oh hey, hold on, let me get a pen, "
    "my dog is barking, sorry about that. So what did you need? Agent: A quote for a one bedroom move to SF. "
    "Callee: Right, right, well, it depends, last week we did one for nine hundred but that was a studio, "
    "yours would be, let me think, probably around fourteen hundred, maybe thirteen fifty if it's a weekday, "
    "and we charge hourly actually, one fifty an hour, so figure nine hours. Agent: So about $1,350 on a weekday? "
    "Callee: Yeah, ballpark. Agent: Summary: about $1,350 per job hourly at $150 an hour, weekday rate. Bye."
)
VOICEMAIL = [
    {"speaker": "agent", "text": "Hi, this is an AI assistant calling on behalf of Vedant about a quote."},
    {"speaker": "agent", "text": "I will call back later. Goodbye."},
]


def assert_outcome(track: str, out: dict) -> None:
    assert set(out) == SHAPES[track]
    assert out["track"] == track
    if track == "quote":
        assert out["amount_usd"] is None or isinstance(out["amount_usd"], float)
        assert isinstance(out["caveats"], list)
    if track == "schedule":
        assert isinstance(out["confirmed"], bool)
    if track == "negotiate":
        assert out["action"] in ("reduced", "canceled", "refused", "none")


def fake(payload):
    """structured() replacement: builds the caller's schema from payload, or returns None, or raises."""

    async def structured(system, user, schema, timeout=20.0):
        if isinstance(payload, Exception):
            raise payload
        if payload is None:
            return None
        return schema(**payload)

    return structured


async def test_clean_transcript_quote(monkeypatch):
    seen = {}

    async def structured(system, user, schema, timeout=20.0):
        seen["user"] = user
        return schema(amount_usd=1240, unit="flat", caveats=["packing not included"], note=None)

    monkeypatch.setattr(llm, "structured", structured)
    out = await ex.extract("quote", CLEAN_TURNS)
    assert_outcome("quote", out)
    assert out == {"track": "quote", "amount_usd": 1240.0, "unit": "flat", "caveats": ["packing not included"],
                   "note": None}
    assert "Callee: Sure, a one bedroom" in seen["user"]
    assert "Summary:" in seen["user"]


async def test_rambling_string_transcript(monkeypatch):
    monkeypatch.setattr(llm, "structured", fake({"amount_usd": 1350, "unit": "per job, hourly at $150",
                                                  "caveats": ["weekday rate"], "note": None}))
    out = await ex.extract("quote", RAMBLING)
    assert_outcome("quote", out)
    assert out["amount_usd"] == 1350.0


@pytest.mark.parametrize("transcript", ["", "   ", [], None, [{"speaker": "agent", "text": ""}]])
async def test_empty_transcript_skips_llm(monkeypatch, transcript):
    async def boom(*args, **kwargs):
        raise AssertionError("LLM must not be called for an empty transcript")

    monkeypatch.setattr(llm, "structured", boom)
    for track in SHAPES:
        out = await ex.extract(track, transcript)
        assert_outcome(track, out)
        assert out == ex.empty_outcome(track, "empty transcript")


async def test_no_summary_line_still_valid(monkeypatch):
    turns = [t for t in CLEAN_TURNS if not t["text"].startswith("Summary:")]
    monkeypatch.setattr(llm, "structured", fake({"amount_usd": 1240, "unit": "flat", "caveats": [], "note": None}))
    out = await ex.extract("quote", turns)
    assert_outcome("quote", out)
    assert out["amount_usd"] == 1240.0


async def test_voicemail_one_sided(monkeypatch):
    monkeypatch.setattr(llm, "structured", fake({"amount_usd": None, "unit": None, "caveats": [],
                                                  "note": "voicemail, no person reached"}))
    out = await ex.extract("quote", VOICEMAIL)
    assert_outcome("quote", out)
    assert out["amount_usd"] is None
    assert out["note"]


async def test_llm_none_and_exception_never_throw(monkeypatch):
    monkeypatch.setattr(llm, "structured", fake(None))
    for track in SHAPES:
        out = await ex.extract(track, CLEAN_TURNS)
        assert out == ex.empty_outcome(track, "extraction failed")
    monkeypatch.setattr(llm, "structured", fake(RuntimeError("boom")))
    for track in SHAPES:
        out = await ex.extract(track, CLEAN_TURNS)
        assert_outcome(track, out)
        assert out["note"].startswith("extraction error")


async def test_schedule_normalizes_slot(monkeypatch):
    monkeypatch.setattr(llm, "structured", fake({"slot_iso": "2026-10-15T14:30:00-07:00", "confirmed": True,
                                                  "note": None}))
    out = await ex.extract("schedule", CLEAN_TURNS)
    assert out == {"track": "schedule", "slot_iso": "2026-10-15T14:30:00-07:00", "confirmed": True, "note": None}

    monkeypatch.setattr(llm, "structured", fake({"slot_iso": "Thursday-ish", "confirmed": True, "note": None}))
    out = await ex.extract("schedule", CLEAN_TURNS)
    assert_outcome("schedule", out)
    assert out["slot_iso"] is None
    assert out["confirmed"] is False
    assert "unparseable" in out["note"]


async def test_negotiate_normalizes(monkeypatch):
    monkeypatch.setattr(llm, "structured", fake({"action": "reduced", "saved_usd": 25, "effective_date": "2026-11-01",
                                                  "note": None}))
    out = await ex.extract("negotiate", CLEAN_TURNS)
    assert out == {"track": "negotiate", "action": "reduced", "saved_usd": 25.0, "effective_date": "2026-11-01",
                   "note": None}
    monkeypatch.setattr(llm, "structured", fake({"action": "none", "saved_usd": None, "effective_date": None,
                                                  "note": None}))
    out = await ex.extract("negotiate", CLEAN_TURNS)
    assert_outcome("negotiate", out)
    assert out["note"] == "no outcome stated"


async def test_transcript_capped(monkeypatch):
    seen = {}

    async def structured(system, user, schema, timeout=20.0):
        seen["user"] = user
        return schema(amount_usd=None, unit=None, caveats=[], note="x")

    monkeypatch.setattr(llm, "structured", structured)
    huge = "Callee: blah blah " * 5000 + "Agent: Summary: no price."
    await ex.extract("quote", huge)
    assert len(seen["user"]) < ex.MAX_TRANSCRIPT_CHARS + 600
    assert "Summary: no price." in seen["user"]
    assert ex.TRIM_MARKER.strip() in seen["user"]


def test_render_transcript_shapes():
    assert ex.render_transcript([{"role": "user", "content": "hi"}, {"speaker": "x", "text": " "}]) == "User: hi"
    assert ex.render_transcript({"turns": [{"speaker": "agent", "text": "yo"}]}) == "Agent: yo"
    assert ex.render_transcript(["plain line"]) == "Unknown: plain line"
    assert ex.render_transcript(" text ") == "text"


def test_empty_outcome_shapes():
    for track in SHAPES:
        assert_outcome(track, ex.empty_outcome(track, "n"))
