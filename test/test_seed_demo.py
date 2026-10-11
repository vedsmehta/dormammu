"""scripts/seed_demo.py on a SqliteStore: labels on every row it writes, determinism, and a wipe that
removes exactly those rows and nothing else."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from scripts import seed_demo
from server.store import SqliteStore

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "seed.db")


def _all_rows(store, table: str, where: str, params: list) -> list[dict]:
    return store._many(table, f"select * from {table} where {where}", params)


def test_generate_labels_everything(store):
    user_id = str(uuid.uuid4())
    summary = seed_demo.generate(store, user_id, runs=8, days=30, seed=11, add_roster=True, now=NOW)

    assert summary["n_runs"] == 8
    assert set(summary["vendors_added"]) == set(seed_demo.ROSTER_KEYS)
    vendors = store.list_vendors(user_id)
    assert len(vendors) == 12
    for v in vendors:
        assert v["is_demo"] is True and v["phone"].startswith(seed_demo.DEMO_PHONE_PREFIX)

    runs = store.list_runs(user_id, limit=100)
    assert len(runs) == 8
    created = []
    n_calls = n_done = n_failed = n_with_context = 0
    for run in runs:
        assert run["request"].startswith("[seeded] ")
        assert run["status"] == "complete" and run["completed_at"] > run["created_at"]
        created.append(run["created_at"])
        calls = store.list_calls(run["id"])
        assert 2 <= len(calls) <= 5
        assert len({c["vendor_key"] for c in calls}) == len(calls)
        for c in calls:
            n_calls += 1
            assert c["simulated"] is True and c["error"] == "seeded"
            assert c["state"] in ("done", "failed") and c["state_rank"] == 6
            assert c["ended_at"] > c["started_at"] >= run["created_at"]
            assert c["target_phone"].startswith(seed_demo.DEMO_PHONE_PREFIX)
            if c["prior_context"]:
                n_with_context += 1
                assert c["prior_context"].startswith("Last time")
            if c["state"] == "done":
                n_done += 1
                assert c["termination_type"] == "completed"
                assert 45 <= c["duration_seconds"] <= 190
                assert isinstance(c["outcome"], dict) and "seeded" in c["outcome"]["note"]
                assert c["outcome"]["track"] == c["track"]
                assert c["transcript"] and isinstance(c["transcript_turns"], list)
                if c["track"] == "quote":
                    assert f"${c['outcome']['amount_usd']:,.0f}" in c["transcript"]
            else:
                n_failed += 1
                assert c["termination_type"] in ("voicemail", "no-answer", "busy")
                assert c["outcome"] is None
    assert n_calls == summary["n_calls"] and n_done == summary["n_done"] and n_failed == summary["n_failed"]
    assert n_with_context >= 1
    assert min(created) >= (NOW.replace(day=1) - (NOW - NOW)).isoformat()[:4]  # same year at least
    assert max(created) < NOW.isoformat()

    events = store.list_events(user_id, limit=5000)
    assert len(events) == summary["n_events"] > n_calls * 3
    types = {e["type"] for e in events}
    assert types == {
        "run_requested",
        "call_dispatched",
        "call_simulated",
        "call_connected",
        "call_terminated",
        "outcome_extracted",
        "run_aggregated",
        "ledger_written",
    }
    for e in events:
        assert e["data"]["seeded"] is True
        assert e["data"]["at"] == e["created_at"]
        if e["type"].startswith("call_") or e["type"] == "outcome_extracted":
            assert e["call_id"] and e["data"]["simulated"] is True
    assert sum(1 for e in events if e["type"] == "run_requested") == 8
    assert sum(1 for e in events if e["type"] == "outcome_extracted") == n_done

    ledger = store.list_ledger(user_id)
    assert len(ledger) == summary["n_ledger"] >= 1
    for row in ledger:
        assert row["label"].startswith("[seeded] ")
        assert row["source_call"] and row["kind"] in ("quote", "appointment", "promo_expiry")

    json.dumps(summary["events"])


def test_generate_is_deterministic(tmp_path):
    a = SqliteStore(tmp_path / "a.db")
    b = SqliteStore(tmp_path / "b.db")
    user_id = str(uuid.uuid4())
    seed_demo.generate(a, user_id, runs=6, days=20, seed=5, add_roster=True, now=NOW)
    seed_demo.generate(b, user_id, runs=6, days=20, seed=5, add_roster=True, now=NOW)

    def shape(store):
        out = []
        for run in sorted(store.list_runs(user_id, limit=50), key=lambda r: r["created_at"]):
            calls = store.list_calls(run["id"])
            out.append((run["request"], run["created_at"], [(c["vendor_key"], c["state"], c["outcome"]) for c in calls]))
        return out

    assert shape(a) == shape(b)
    c = SqliteStore(tmp_path / "c.db")
    seed_demo.generate(c, user_id, runs=6, days=20, seed=6, add_roster=True, now=NOW)
    assert shape(c) != shape(a)


def test_generate_reuses_existing_roster_when_large_enough(store):
    user_id = str(uuid.uuid4())
    store.upsert_profile(user_id, {"display_name": "has vendors"})
    store.upsert_vendors(
        user_id,
        [
            {"key": "movers_a", "name": "Pack & Go", "track": "quote", "phone": "+15550000001", "is_demo": True},
            {"key": "movers_b", "name": "Bay Movers", "track": "quote", "phone": "+15550000002", "is_demo": True},
            {"key": "movers_c", "name": "Golden", "track": "quote", "phone": "+15550000003", "is_demo": True},
            {"key": "dentist", "name": "Smile", "track": "schedule", "phone": "+15550000004", "is_demo": True},
            {"key": "internet_retention", "name": "ISP", "track": "negotiate", "phone": "+15550000005", "is_demo": True},
        ],
    )
    summary = seed_demo.generate(store, user_id, runs=5, days=15, seed=2, now=NOW)
    assert summary["vendors_added"] == [] and summary["vendors_total"] == 5
    assert {v["phone"] for v in store.list_vendors(user_id)} == {f"+1555000000{i}" for i in range(1, 6)}
    keys = {c["vendor_key"] for r in store.list_runs(user_id, limit=50) for c in store.list_calls(r["id"])}
    assert keys <= {"movers_a", "movers_b", "movers_c", "dentist", "internet_retention"}


def test_wipe_removes_exactly_the_seeded_rows(store):
    user_id = str(uuid.uuid4())
    other_user = str(uuid.uuid4())
    store.upsert_profile(user_id, {"display_name": "demo"})
    store.upsert_profile(other_user, {"display_name": "other"})

    # Real-looking rows that must survive, for this user and for another user.
    keep_vendor = store.upsert_vendors(
        user_id, [{"key": "movers_a", "name": "Real Movers", "track": "quote", "phone": "+14155550123", "is_demo": False}]
    )[0]
    keep_run = store.create_run(user_id, "I'm moving to SF next month, handle it")
    keep_call = store.insert_calls(
        [
            {
                "run_id": keep_run["id"],
                "vendor_id": keep_vendor["id"],
                "vendor_key": "movers_a",
                "track": "quote",
                "target_name": "Real Movers",
                "target_phone": "+14155550123",
                "objective": "quote",
                "state": "done",
                "state_rank": 6,
                "simulated": True,
                "error": None,
                "outcome": {"track": "quote", "amount_usd": 1500.0, "unit": "flat", "caveats": [], "note": "simulated"},
            }
        ]
    )[0]
    store.insert_event(user_id, "run_requested", {"request": "real", "simulated": True}, run_id=keep_run["id"])
    store.insert_event(user_id, "note", {"seeded": False}, run_id=keep_run["id"])
    store.insert_ledger(
        [{"user_id": user_id, "vendor_id": keep_vendor["id"], "kind": "quote", "label": "Real Movers quote $1500", "source_call": keep_call["id"]}]
    )
    other_summary = seed_demo.generate(store, other_user, runs=3, days=10, seed=9, add_roster=True, now=NOW)

    summary = seed_demo.generate(store, user_id, runs=6, days=30, seed=4, add_roster=True, now=NOW)
    assert summary["vendors_added"] and "movers_a" not in summary["vendors_added"]
    assert len(store.list_vendors(user_id)) == 12
    before_events = len(store.list_events(user_id, limit=5000))
    assert before_events == summary["n_events"] + 2

    counts = seed_demo.wipe(store, user_id)
    assert counts == {
        "ledger": summary["n_ledger"],
        "events": summary["n_events"],
        "calls": summary["n_calls"],
        "runs": summary["n_runs"],
        "vendors": 11,
    }

    runs = store.list_runs(user_id, limit=100)
    assert [r["id"] for r in runs] == [keep_run["id"]]
    assert [c["id"] for c in store.list_calls(keep_run["id"])] == [keep_call["id"]]
    events = store.list_events(user_id, limit=5000)
    assert len(events) == 2 and all(not e["data"].get("seeded") for e in events)
    assert [row["label"] for row in store.list_ledger(user_id)] == ["Real Movers quote $1500"]
    vendors = store.list_vendors(user_id)
    assert [v["key"] for v in vendors] == ["movers_a"] and vendors[0]["phone"] == "+14155550123"

    # The other user's seeded data is untouched.
    assert len(store.list_runs(other_user, limit=100)) == other_summary["n_runs"]
    assert len(store.list_events(other_user, limit=5000)) == other_summary["n_events"]
    assert len(store.list_vendors(other_user)) == 12

    # A second wipe is a no-op.
    assert all(v == 0 for v in seed_demo.wipe(store, user_id).values())


async def test_send_to_keewano_reports_without_raising(monkeypatch):
    from events import keewano

    sent: list[int] = []

    async def fake_send(user_id, events):
        sent.append(len(events))
        return len(sent) == 1

    monkeypatch.setattr(keewano, "send_events", fake_send)
    events = [{"id": i, "type": "run_requested", "data": {"seeded": True}, "created_at": f"2026-10-0{1 + i % 9}T00:00:00+00:00"} for i in range(250)]
    lines: list[str] = []
    result = await seed_demo.send_to_keewano("u", events, batch_size=100, log=lines.append)
    assert sent == [100, 100, 50]
    assert result["batches"] == 3 and result["ok"] == 1 and result["events"] == 250
    assert len(lines) == 3 and "rejected" in lines[1]
