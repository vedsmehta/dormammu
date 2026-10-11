from datetime import UTC, date, datetime, timedelta

import pytest

from call.simulate import (
    DIALING_SECONDS,
    RINGING_UNTIL_SECONDS,
    SPOKEN_DISCLOSURE,
    advance_simulated,
    fixture_outcome,
    fixture_transcript,
    talk_seconds,
)
from test.fakes import FakeStore, call_row

USER = "user-1"
T0 = datetime(2026, 10, 11, 20, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("SIMULATED_TALK_SECONDS", "75")


def seed(track="quote", **overrides):
    store = FakeStore()
    fields = {
        "track": track,
        "state": "dialing",
        "state_rank": 1,
        "simulated": True,
        "started_at": T0.isoformat(),
    }
    row = call_row(**{**fields, **overrides})
    run = store.seed_run(USER, calls=[row])
    return store, run["calls"][0]


def test_fixture_outcome_is_deterministic_and_plausible():
    for track in ("quote", "schedule", "negotiate"):
        a = fixture_outcome(track, "call-123", today=date(2026, 10, 11))
        b = fixture_outcome(track, "call-123", today=date(2026, 10, 11))
        assert a == b and a["track"] == track and a["note"]
    assert fixture_outcome("quote", "call-123") != fixture_outcome("quote", "call-124")


def test_quote_fixture_ranges():
    for i in range(30):
        out = fixture_outcome("quote", f"seed-{i}")
        assert 900 <= out["amount_usd"] <= 2100
        assert out["unit"] in ("flat", "per hour")
        assert out["caveats"] and all(isinstance(c, str) for c in out["caveats"])


def test_schedule_fixture_is_next_week_and_confirmed():
    today = date(2026, 10, 11)  # a Sunday
    for i in range(20):
        out = fixture_outcome("schedule", f"seed-{i}", today=today)
        slot = datetime.fromisoformat(out["slot_iso"])
        assert slot.tzinfo is not None
        assert date(2026, 10, 12) <= slot.date() <= date(2026, 10, 16)
        assert out["confirmed"] is True


def test_negotiate_fixture_reduces_next_month():
    for i in range(20):
        out = fixture_outcome("negotiate", f"seed-{i}", today=date(2026, 10, 11))
        assert out["action"] == "reduced"
        assert 15 <= out["saved_usd"] <= 40
        assert out["effective_date"] == "2026-11-01"
    assert fixture_outcome("negotiate", "x", today=date(2026, 12, 3))["effective_date"] == "2027-01-01"


@pytest.mark.parametrize("track", ["quote", "schedule", "negotiate"])
def test_fixture_transcript_shape(track):
    turns = fixture_transcript(track, "Bay Movers", "seed-1")
    assert turns[0]["speaker"] == "agent" and turns[0]["text"].startswith(SPOKEN_DISCLOSURE)
    assert turns[-1]["speaker"] == "agent" and turns[-1]["text"].startswith("Just to confirm, ")
    assert any(t["speaker"] == "user" for t in turns)
    assert all(set(t) == {"speaker", "text"} for t in turns)
    assert "Bay Movers" in turns[-1]["text"]


def test_talk_seconds_is_seeded_within_jitter(monkeypatch):
    assert talk_seconds("abc") == talk_seconds("abc")
    for i in range(40):
        assert 55 <= talk_seconds(f"s{i}") <= 95
    monkeypatch.setenv("SIMULATED_TALK_SECONDS", "30")
    assert 15 <= talk_seconds("abc") <= 50


def test_timeline_dialing_ringing_talking_done():
    store, row = seed()
    talk = talk_seconds(row["id"])

    out = advance_simulated(store, row, T0 + timedelta(seconds=1))
    assert out["state"] == "dialing"

    out = advance_simulated(store, out, T0 + timedelta(seconds=DIALING_SECONDS + 1))
    assert out["state"] == "ringing" and out["state_rank"] == 2

    out = advance_simulated(store, out, T0 + timedelta(seconds=RINGING_UNTIL_SECONDS + 1))
    assert out["state"] == "talking" and out["state_rank"] == 3
    assert len(store.events_of_type("call_connected")) == 1
    advance_simulated(store, out, T0 + timedelta(seconds=RINGING_UNTIL_SECONDS + 5))
    assert len(store.events_of_type("call_connected")) == 1

    out = advance_simulated(store, out, T0 + timedelta(seconds=RINGING_UNTIL_SECONDS + talk + 1))
    assert out["state"] == "done" and out["state_rank"] == 6
    assert out["simulated"] is True
    assert out["outcome"]["track"] == "quote" and out["outcome"]["amount_usd"]
    assert out["transcript"].startswith(f"agent: {SPOKEN_DISCLOSURE}")
    assert "Just to confirm" in out["transcript"].splitlines()[-1]
    assert out["transcript_turns"][0]["startMs"] == 0 and out["transcript_turns"][-1]["endMs"] <= talk * 1000
    assert out["duration_seconds"] == float(talk)
    assert out["termination_type"] == "completed"
    assert datetime.fromisoformat(out["ended_at"]) == T0 + timedelta(seconds=RINGING_UNTIL_SECONDS + talk)
    assert len(store.events_of_type("call_terminated")) == 1
    [extracted] = store.events_of_type("outcome_extracted")
    assert extracted["data"]["simulated"] is True and extracted["data"]["outcome"] == out["outcome"]


def test_jump_straight_to_done_still_logs_connected_and_is_idempotent():
    store, row = seed(track="negotiate")
    late = T0 + timedelta(seconds=500)
    out = advance_simulated(store, row, late)
    assert out["state"] == "done" and out["outcome"]["action"] == "reduced"
    again = advance_simulated(store, out, late + timedelta(seconds=10))
    assert again == out
    assert len(store.events_of_type("call_connected")) == 1
    assert len(store.events_of_type("call_terminated")) == 1
    assert len(store.events_of_type("outcome_extracted")) == 1


def test_done_transcript_matches_outcome():
    store, row = seed(track="schedule", target_name="Downtown Dental")
    out = advance_simulated(store, row, T0 + timedelta(seconds=1000))
    slot = datetime.fromisoformat(out["outcome"]["slot_iso"])
    assert slot.strftime("%A") in out["transcript"].splitlines()[-1]
    assert "Downtown Dental" in out["transcript"]


def test_non_simulated_row_is_ignored():
    store = FakeStore()
    run = store.seed_run(USER, calls=[call_row(state="dialing", state_rank=1, simulated=False)])
    assert advance_simulated(store, run["calls"][0], T0 + timedelta(seconds=1000)) is None
    assert store.get_call(run["calls"][0]["id"])["state"] == "dialing"


def test_missing_started_at_is_set_to_now():
    store, row = seed(started_at=None)
    out = advance_simulated(store, row, T0)
    assert out["state"] == "dialing"
    assert datetime.fromisoformat(out["started_at"]) == T0
