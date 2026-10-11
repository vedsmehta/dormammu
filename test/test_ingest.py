import pytest

from call.ingest import has_callee_turn, ingest_call_update
from test.fakes import FakeStore, call_row

USER = "user-1"


@pytest.fixture(autouse=True)
def no_keewano(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")


def dial(state, termination=None, *, transcript=None, turns=None, duration=0, call_id="dc_1"):
    return {
        "id": call_id,
        "duration": duration,
        "transcript": transcript,
        "transcriptTurns": turns,
        "terminationType": termination,
        "terminatedAt": "2026-10-11T21:00:00.000Z" if state == "Terminated" else None,
        "status": {
            "state": state,
            "terminationType": termination,
            "cancelRequested": False,
            "cancelPending": False,
        },
    }


GOOD_TURNS = [
    {
        "speaker": "agent",
        "text": "Hi, I'm an AI assistant calling on behalf of a client.",
        "startMs": 0,
        "endMs": 3000,
    },
    {"speaker": "user", "text": "Sure, we can do it for 1200 flat.", "startMs": 3500, "endMs": 7000},
    {"speaker": "agent", "text": "Summary: 1200 flat. Goodbye.", "startMs": 7500, "endMs": 9000},
]
ONE_SIDED_TURNS = [
    {
        "speaker": "agent",
        "text": "Hi, I'm an AI assistant calling on behalf of a client.",
        "startMs": 0,
        "endMs": 3000,
    },
    {"speaker": "agent", "text": "Summary: nothing agreed. Goodbye.", "startMs": 20000, "endMs": 23000},
]


@pytest.fixture
def seeded():
    store = FakeStore()
    run = store.seed_run(USER, calls=[call_row(state="dialing", state_rank=1, dial_call_id="dc_1")])
    return store, run["calls"][0]


def test_ringing_moves_forward(seeded):
    store, row = seeded
    out = ingest_call_update(store, row, dial("Ringing"))
    assert out["state"] == "ringing" and out["state_rank"] == 2
    assert store.events == []


def test_talking_logs_call_connected_once(seeded):
    store, row = seeded
    out = ingest_call_update(store, row, dial("In-Progress"))
    assert out["state"] == "talking"
    ingest_call_update(store, out, dial("In-Progress"))
    connected = store.events_of_type("call_connected")
    assert len(connected) == 1
    assert connected[0]["user_id"] == USER and connected[0]["call_id"] == row["id"] and connected[0]["run_id"]


def test_completed_with_callee_turn_goes_to_extracting_and_stores_transcript(seeded):
    store, row = seeded
    text = "\n".join(f"{t['speaker']}: {t['text']}" for t in GOOD_TURNS)
    out = ingest_call_update(
        store, row, dial("Terminated", "completed", transcript=text, turns=GOOD_TURNS, duration=42)
    )
    assert out["state"] == "extracting" and out["state_rank"] == 4
    assert out["transcript"] == text
    assert out["transcript_turns"] == GOOD_TURNS
    assert out["duration_seconds"] == 42
    assert out["termination_type"] == "completed"
    assert out["ended_at"] == "2026-10-11T21:00:00.000Z"
    terminated = store.events_of_type("call_terminated")
    assert len(terminated) == 1 and terminated[0]["data"]["voicemail"] is False
    # skipped In-Progress between polls still counts as a connection
    assert len(store.events_of_type("call_connected")) == 1


def test_voicemail_guard_no_callee_turn(seeded):
    store, row = seeded
    text = "\n".join(f"{t['speaker']}: {t['text']}" for t in ONE_SIDED_TURNS)
    out = ingest_call_update(
        store, row, dial("Terminated", "completed", transcript=text, turns=ONE_SIDED_TURNS, duration=40)
    )
    assert out["state"] == "failed" and out["state_rank"] == 6
    assert out["termination_type"] == "voicemail"
    assert out["transcript"] == text
    terminated = store.events_of_type("call_terminated")
    assert (
        terminated[0]["data"]["voicemail"] is True
        and terminated[0]["data"]["termination_type"] == "voicemail"
    )
    assert store.events_of_type("call_connected") == []


def test_voicemail_guard_short_duration_even_with_callee_turn(seeded):
    store, row = seeded
    out = ingest_call_update(
        store, row, dial("Terminated", "completed", transcript="x", turns=GOOD_TURNS, duration=9)
    )
    assert out["state"] == "failed" and out["termination_type"] == "voicemail"


def test_completed_before_transcript_waits_in_extracting_then_applies_guard(seeded):
    store, row = seeded
    first = ingest_call_update(store, row, dial("Terminated", "completed", duration=30))
    assert first["state"] == "extracting" and first["transcript"] is None
    # Transcription lands on a later poll: one-sided, so the guard now fires from extracting.
    second = ingest_call_update(
        store,
        first,
        dial("Terminated", "completed", transcript="agent: hi", turns=ONE_SIDED_TURNS, duration=30),
    )
    assert second["state"] == "failed" and second["termination_type"] == "voicemail"
    assert second["transcript"] == "agent: hi"
    assert len(store.events_of_type("call_terminated")) == 1


def test_late_transcript_is_stored_without_touching_state(seeded):
    store, row = seeded
    first = ingest_call_update(store, row, dial("Terminated", "completed", duration=30))
    assert first["state"] == "extracting"
    second = ingest_call_update(
        store,
        first,
        dial("Terminated", "completed", transcript="agent: hi\nuser: yes", turns=GOOD_TURNS, duration=31),
    )
    assert second["state"] == "extracting"
    assert second["transcript"] == "agent: hi\nuser: yes" and second["transcript_turns"] == GOOD_TURNS
    assert second["duration_seconds"] == 31
    assert len(store.events_of_type("call_terminated")) == 1


@pytest.mark.parametrize("termination", ["busy", "no-answer", "failed", "canceled", None])
def test_other_terminations_fail(seeded, termination):
    store, row = seeded
    out = ingest_call_update(store, row, dial("Terminated", termination, duration=0))
    assert out["state"] == "failed" and out["termination_type"] == termination
    assert out["ended_at"] and out["duration_seconds"] == 0
    assert len(store.events_of_type("call_terminated")) == 1


def test_stale_update_cannot_revert_terminal_row(seeded):
    store, row = seeded
    done = ingest_call_update(
        store, row, dial("Terminated", "completed", transcript="t", turns=GOOD_TURNS, duration=30)
    )
    store.update_call_state(done["id"], "done", 6, {"outcome": {"track": "quote", "amount_usd": 1200}})
    done = store.get_call(done["id"])
    out = ingest_call_update(store, done, dial("In-Progress"))
    assert out["state"] == "done" and out["outcome"]["amount_usd"] == 1200
    assert store.events_of_type("call_connected") and len(store.events_of_type("call_terminated")) == 1


def test_unknown_status_returns_none_and_changes_nothing(seeded):
    store, row = seeded
    assert ingest_call_update(store, row, {"id": "dc_1", "status": {"state": "Unknown"}}) is None
    assert ingest_call_update(store, row, {"id": "dc_1"}) is None
    assert store.get_call(row["id"])["state"] == "dialing"


def test_missing_row_returns_none():
    store = FakeStore()
    assert ingest_call_update(store, call_row(id="nope"), dial("Ringing")) is None
    assert ingest_call_update(store, {}, dial("Ringing")) is None


def test_has_callee_turn_variants():
    assert has_callee_turn(GOOD_TURNS, None) is True
    assert has_callee_turn(ONE_SIDED_TURNS, None) is False
    assert has_callee_turn([{"speaker": "transfer_target", "text": "hello"}], None) is True
    assert has_callee_turn(None, None) is None
    assert has_callee_turn(None, "Agent: hello\nUser: hi there") is True
    assert has_callee_turn(None, "Agent: hello\nAgent: goodbye") is False
    assert has_callee_turn([], "") is None
