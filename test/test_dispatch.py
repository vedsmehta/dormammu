import asyncio

import pytest

from call import dial_client, dispatch
from call.dial_client import DialError
from test.fakes import FakeStore, call_row

USER = "user-1"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("DIAL_NUMBER", "+14155550100")


def seed(n=3, **overrides):
    store = FakeStore()
    rows = []
    for i in range(n):
        fields = {
            "vendor_key": f"vendor_{i}",
            "target_name": f"Vendor {i}",
            "target_phone": f"+1415555010{i}",
        }
        rows.append(call_row(**{**fields, **overrides}))
    run = store.seed_run(USER, calls=rows)
    profile = {"id": USER, "display_name": "Vedant", "phone": None, "prefs": {}, "cohort": "student"}
    return store, run, run["calls"], profile


class Recorder:
    def __init__(self, fail_for=None, exc_factory=None, delay=0.02):
        self.calls = []
        self.fail_for = fail_for or set()
        self.exc_factory = exc_factory or (lambda: DialError(429, "call_limit_reached", "limit"))
        self.delay = delay
        self.active = self.peak = 0

    async def place_call(self, to, instruction, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(self.delay)
        self.active -= 1
        self.calls.append({"to": to, "instruction": instruction, **kwargs})
        if to in self.fail_for:
            raise self.exc_factory()
        return {"id": f"dc_{len(self.calls)}", "status": {"state": "Queued", "terminationType": None}}


async def test_dispatch_places_every_call_concurrently(monkeypatch):
    store, run, calls, profile = seed(3)
    rec = Recorder()
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)

    rows = await dispatch.dispatch_run(store, run, calls, profile)

    assert rec.peak == 3
    assert [r["id"] for r in rows] == [c["id"] for c in calls]
    for row in rows:
        assert row["state"] == "dialing" and row["state_rank"] == 1
        assert row["dial_call_id"] and row["simulated"] is False and row["error"] is None
    assert len(store.events_of_type("call_dispatched")) == 3
    assert store.events_of_type("call_simulated") == []
    dispatched = store.events_of_type("call_dispatched")[0]
    assert dispatched["user_id"] == USER and dispatched["run_id"] == run["id"] and dispatched["call_id"]


async def test_dispatch_request_shape(monkeypatch):
    store, run, calls, profile = seed(1, prior_context="Last time you quoted $1,200.")
    rec = Recorder()
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)
    [row] = calls

    await dispatch.dispatch_run(store, run, calls, profile)

    [placed] = rec.calls
    assert placed["to"] == row["target_phone"]
    assert placed["idempotency_key"] == row["id"]
    assert placed["max_duration_seconds"] == 180
    assert placed["voice_gender"] == "female"
    text = placed["instruction"]
    assert "AI" in text.splitlines()[0]
    assert row["objective"] in text
    assert "Last time you quoted $1,200." in text
    assert "Summary:" in text


async def test_limit_error_becomes_labeled_simulated_row(monkeypatch):
    store, run, calls, profile = seed(3)
    rec = Recorder(fail_for={calls[1]["target_phone"]})
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)

    rows = await dispatch.dispatch_run(store, run, calls, profile)

    assert [r["simulated"] for r in rows] == [False, True, False]
    assert all(r["state"] == "dialing" for r in rows)
    assert rows[1]["dial_call_id"] is None and rows[1]["error"] is None
    [simulated] = store.events_of_type("call_simulated")
    assert simulated["call_id"] == calls[1]["id"]
    assert simulated["data"]["limit"] is True and "call_limit_reached" in simulated["data"]["reason"]
    assert len(store.events_of_type("call_dispatched")) == 3
    dispatched = {e["call_id"]: e["data"]["simulated"] for e in store.events_of_type("call_dispatched")}
    assert dispatched[calls[1]["id"]] is True and dispatched[calls[0]["id"]] is False


async def test_any_exception_becomes_simulated_and_never_raises(monkeypatch):
    store, run, calls, profile = seed(2)
    rec = Recorder(fail_for={c["target_phone"] for c in calls}, exc_factory=lambda: RuntimeError("dns down"))
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)

    rows = await dispatch.dispatch_run(store, run, calls, profile)

    assert all(r["simulated"] and r["state"] == "dialing" for r in rows)
    reasons = [e["data"]["reason"] for e in store.events_of_type("call_simulated")]
    assert reasons == ["RuntimeError: dns down"] * 2
    assert all(e["data"]["limit"] is False for e in store.events_of_type("call_simulated"))


async def test_row_without_vendor_is_never_dialed(monkeypatch):
    store, run, calls, profile = seed(1, vendor_id=None, vendor_key=None)
    rec = Recorder()
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)

    [row] = await dispatch.dispatch_run(store, run, calls, profile)

    assert rec.calls == []
    assert row["simulated"] is True and row["state"] == "dialing"
    assert "vendor" in store.events_of_type("call_simulated")[0]["data"]["reason"]


async def test_store_failure_does_not_raise(monkeypatch):
    store, run, calls, profile = seed(2)
    rec = Recorder()
    monkeypatch.setattr(dial_client, "place_call", rec.place_call)

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(store, "update_call", boom)
    rows = await dispatch.dispatch_run(store, run, calls, profile)
    assert len(rows) == 2
    assert len(store.events_of_type("call_dispatched")) == 2


async def test_empty_and_malformed_inputs():
    store = FakeStore()
    assert await dispatch.dispatch_run(store, {"id": "r", "user_id": USER}, [], None) == []
    assert await dispatch.dispatch_run(store, {}, [{"no": "id"}], None) == []
