import asyncio
import itertools

import pytest

from events import keewano
from events import log as event_log

USER = "11111111-1111-1111-1111-111111111111"


class FakeStore:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self._ids = itertools.count(1)

    def insert_event(self, user_id, type, data, run_id=None, call_id=None):
        row = {
            "id": next(self._ids),
            "user_id": user_id,
            "run_id": run_id,
            "call_id": call_id,
            "type": type,
            "data": data,
            "created_at": "2026-10-11T20:00:00+00:00",
        }
        self.events.append(row)
        return row


class BrokenStore:
    def insert_event(self, *args, **kwargs):
        raise RuntimeError("postgrest down")


@pytest.fixture
def sink(monkeypatch):
    """Replace the network send with a recorder and turn the sink on."""
    calls: list[tuple[str, list[dict]]] = []

    async def fake_send(user_id, events):
        calls.append((user_id, events))
        return True

    monkeypatch.setattr(keewano, "send_events", fake_send)
    monkeypatch.setenv("KEEWANO_ENABLED", "true")
    monkeypatch.setenv("KEEWANO_TOKEN", "test-token")
    return calls


def test_inserts_row_first_and_returns_it(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    store = FakeStore()
    row = event_log.log_event(store, USER, "run_requested", {"request": "cheaper internet"}, run_id="r1")
    assert store.events == [row]
    assert row["type"] == "run_requested"
    assert row["data"] == {"request": "cheaper internet"}
    assert row["run_id"] == "r1" and row["call_id"] is None


def test_none_data_becomes_empty_dict(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    row = event_log.log_event(FakeStore(), USER, "call_dispatched", call_id="c1")
    assert row["data"] == {}
    assert row["call_id"] == "c1"


def test_store_failure_never_raises(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    row = event_log.log_event(BrokenStore(), USER, "call_terminated", {"state": "done"}, call_id="c1")
    assert row["type"] == "call_terminated"
    assert row["data"] == {"state": "done"}
    assert row["call_id"] == "c1"


async def test_schedules_keewano_when_enabled_and_loop_running(sink):
    store = FakeStore()
    row = event_log.log_event(store, USER, "run_requested", {"request": "x"}, run_id="r1")
    for _ in range(3):
        await asyncio.sleep(0)
    assert sink == [(USER, [row])]
    assert not event_log._TASKS


async def test_skips_keewano_when_disabled(sink, monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "0")
    event_log.log_event(FakeStore(), USER, "run_requested", {"request": "x"})
    await asyncio.sleep(0)
    assert sink == []


async def test_skips_keewano_without_token(sink, monkeypatch):
    monkeypatch.delenv("KEEWANO_TOKEN", raising=False)
    event_log.log_event(FakeStore(), USER, "run_requested", {"request": "x"})
    await asyncio.sleep(0)
    assert sink == []


def test_sync_caller_without_loop_skips_silently(sink):
    row = event_log.log_event(FakeStore(), USER, "run_requested", {"request": "x"})
    assert row["type"] == "run_requested"
    assert sink == []


async def test_sink_failure_is_contained(monkeypatch):
    async def exploding_send(user_id, events):
        raise RuntimeError("keewano exploded")

    monkeypatch.setattr(keewano, "send_events", exploding_send)
    monkeypatch.setenv("KEEWANO_ENABLED", "true")
    monkeypatch.setenv("KEEWANO_TOKEN", "test-token")
    row = event_log.log_event(FakeStore(), USER, "run_requested", {"request": "x"})
    for _ in range(3):
        await asyncio.sleep(0)
    assert row["type"] == "run_requested"
    assert not event_log._TASKS


async def test_store_failure_still_reaches_sink(sink):
    event_log.log_event(BrokenStore(), USER, "ledger_written", {"rows": 2}, run_id="r9")
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(sink) == 1
    assert sink[0][1][0]["type"] == "ledger_written"
    assert sink[0][1][0]["run_id"] == "r9"


# -- call context for the Keewano copy ----------------------------------------------------------


class ContextStore(FakeStore):
    def __init__(self, call_row=None, fail=False) -> None:
        super().__init__()
        self.call_row = call_row
        self.fail = fail
        self.lookups: list[str] = []

    def get_call(self, call_id):
        self.lookups.append(call_id)
        if self.fail:
            raise RuntimeError("postgrest down")
        return self.call_row


async def _drain():
    if event_log._TASKS:
        await asyncio.gather(*list(event_log._TASKS), return_exceptions=True)


async def test_call_scoped_event_gets_call_context_for_keewano_only(sink):
    store = ContextStore({"id": "c1", "track": "quote", "vendor_key": "comcast", "simulated": 0})
    data = {"termination_type": "completed", "duration_seconds": 90}
    row = event_log.log_event(store, USER, "call_terminated", data, run_id="r1", call_id="c1")
    await _drain()
    assert store.lookups == ["c1"]
    assert row["data"] == data  # the stored row is exactly what the caller passed
    assert store.events[0]["data"] == data
    (sent,) = sink
    assert sent[1][0]["data"] == {**data, "track": "quote", "vendor_key": "comcast", "simulated": 0}
    assert sent[1][0]["call_id"] == "c1"


async def test_no_lookup_when_event_already_has_call_context(sink):
    store = ContextStore({"id": "c1", "track": "quote", "vendor_key": "comcast", "simulated": False})
    data = {"track": "quote", "vendor_key": "comcast", "simulated": False, "dial_call_id": "d1"}
    event_log.log_event(store, USER, "call_dispatched", data, call_id="c1")
    await _drain()
    assert store.lookups == []
    assert len(sink) == 1 and sink[0][1][0]["data"] == data


async def test_run_scoped_event_never_looks_up_a_call(sink):
    store = ContextStore({"id": "c1", "track": "quote"})
    event_log.log_event(store, USER, "run_aggregated", {"multiplier": 2.5}, run_id="r1")
    await _drain()
    assert store.lookups == []
    assert len(sink) == 1


async def test_call_context_lookup_failure_still_sends(sink):
    store = ContextStore(fail=True)
    event_log.log_event(store, USER, "call_connected", {"track": "quote"}, call_id="c1")
    await _drain()
    assert len(sink) == 1 and sink[0][1][0]["data"] == {"track": "quote"}
    assert not event_log._TASKS


def test_with_call_context_fills_only_missing_fields():
    event = {"type": "call_terminated", "call_id": "c1", "data": {"track": "schedule", "duration_seconds": 5}}
    out = event_log.with_call_context(event, {"track": "quote", "vendor_key": "v", "simulated": True, "id": "c1"})
    assert out["data"] == {"track": "schedule", "duration_seconds": 5, "vendor_key": "v", "simulated": True}
    assert event["data"] == {"track": "schedule", "duration_seconds": 5}
    assert event_log.with_call_context(event, None) is event
