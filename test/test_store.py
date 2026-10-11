"""Store semantics that both backends must share: monotonic refusal, exactly-once claim, run assembly,
prior outcomes per vendor, due ledger rows.

The Supabase case runs only when SUPABASE_URL is set and RUN_SUPABASE_TESTS=1. It creates a throwaway
auth user with the admin API (profiles.id references auth.users) and deletes it afterwards, which
cascades to every row the test wrote.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta

import pytest

from server.store import SqliteStore, SupabaseStore

BACKENDS = ["sqlite", "supabase"]


def _supabase_enabled() -> bool:
    return bool(os.environ.get("SUPABASE_URL")) and os.environ.get("RUN_SUPABASE_TESTS") == "1"


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path):
    """Yields (store, user_id) with a profile row already present."""
    if request.param == "sqlite":
        store = SqliteStore(tmp_path / "store.db")
        user_id = str(uuid.uuid4())
        store.upsert_profile(user_id, {"display_name": "test"})
        yield store, user_id
        return

    if not _supabase_enabled():
        pytest.skip("set SUPABASE_URL and RUN_SUPABASE_TESTS=1 to run the Supabase store tests")
    store = SupabaseStore()
    email = f"dormammu-test-{uuid.uuid4().hex[:12]}@example.com"
    created = store.client.auth.admin.create_user(
        {"email": email, "email_confirm": True, "password": uuid.uuid4().hex}
    )
    user_id = created.user.id
    store.upsert_profile(user_id, {"display_name": "test"})
    try:
        yield store, user_id
    finally:
        store.client.auth.admin.delete_user(user_id)


def _vendors(store, user_id):
    return store.upsert_vendors(
        user_id,
        [
            {
                "key": "movers_a",
                "name": "Pack & Go",
                "track": "quote",
                "phone": "+15550000001",
                "is_demo": True,
            },
            {
                "key": "isp_a",
                "name": "Fiber Co",
                "track": "negotiate",
                "phone": "+15550000002",
                "is_demo": True,
            },
        ],
    )


def _call_row(run_id, vendor, **extra):
    row = {
        "run_id": run_id,
        "vendor_id": vendor["id"],
        "vendor_key": vendor["key"],
        "track": vendor["track"],
        "target_name": vendor["name"],
        "target_phone": vendor["phone"],
        "objective": "get a quote",
        "prior_context": None,
        "simulated": False,
    }
    row.update(extra)
    return row


def test_profile_round_trip(backend):
    store, user_id = backend
    row = store.upsert_profile(user_id, {"prefs": {"budget_ceiling_usd": 900}, "cohort": "student"})
    assert row["id"] == user_id
    assert row["prefs"] == {"budget_ceiling_usd": 900}
    assert row["cohort"] == "student"
    again = store.get_profile(user_id)
    assert again is not None and again["prefs"]["budget_ceiling_usd"] == 900


def test_vendors_upsert_by_owner_and_key(backend):
    store, user_id = backend
    first = _vendors(store, user_id)
    assert {v["key"] for v in first} == {"movers_a", "isp_a"}
    assert all(v["owner_id"] == user_id for v in first)
    assert first[0]["is_demo"] is True
    store.upsert_vendors(
        user_id, [{"key": "movers_a", "name": "Pack & Go Movers", "track": "quote", "phone": "+15550000009"}]
    )
    listed = store.list_vendors(user_id)
    assert len(listed) == 2
    movers = next(v for v in listed if v["key"] == "movers_a")
    assert movers["name"] == "Pack & Go Movers"
    assert movers["phone"] == "+15550000009"
    assert movers["id"] == first[0]["id"] if first[0]["key"] == "movers_a" else True


def test_update_call_state_is_monotonic(backend):
    store, user_id = backend
    vendor = _vendors(store, user_id)[0]
    run = store.create_run(user_id, "move me")
    (call,) = store.insert_calls([_call_row(run["id"], vendor)])
    assert call["state"] == "planned" and call["state_rank"] == 0 and call["simulated"] is False

    assert store.update_call_state(call["id"], "dialing", 1) is True
    assert store.update_call_state(call["id"], "talking", 3) is True
    assert store.update_call_state(call["id"], "ringing", 2) is False
    assert store.update_call_state(call["id"], "talking", 3) is False
    row = store.get_call(call["id"])
    assert row["state"] == "talking" and row["state_rank"] == 3

    assert store.update_call_state(call["id"], "done", 6, {"ended_at": "2026-10-11T20:00:00+00:00"}) is True
    assert store.update_call_state(call["id"], "failed", 6) is False
    row = store.get_call(call["id"])
    assert row["state"] == "done" and row["ended_at"].startswith("2026-10-11T20:00:00")


def test_claim_extraction_exactly_once(backend):
    store, user_id = backend
    vendor = _vendors(store, user_id)[0]
    run = store.create_run(user_id, "move me")
    (call,) = store.insert_calls([_call_row(run["id"], vendor)])
    store.update_call_state(call["id"], "extracting", 4)

    assert store.claim_extraction(call["id"]) is True
    assert store.claim_extraction(call["id"]) is False
    row = store.get_call(call["id"])
    assert row["state"] == "extracting"
    assert store.update_call_state(call["id"], "extracting", 4) is False
    assert store.update_call_state(call["id"], "done", 6) is True

    store.update_call(
        call["id"],
        {"outcome": {"track": "quote", "amount_usd": 1.0, "unit": None, "caveats": [], "note": None}},
    )
    assert store.claim_extraction(call["id"]) is False
    assert store.get_call(call["id"])["outcome"]["amount_usd"] == 1.0


def test_get_run_assembles_calls_in_order(backend):
    store, user_id = backend
    vendors = _vendors(store, user_id)
    run = store.create_run(user_id, "move me")
    assert run["status"] == "dispatching"
    rows = store.insert_calls([_call_row(run["id"], vendors[0]), _call_row(run["id"], vendors[1])])
    assert len(rows) == 2

    full = store.get_run(run["id"], user_id)
    assert full is not None
    assert full["request"] == "move me"
    assert [c["id"] for c in full["calls"]] == [r["id"] for r in rows]
    assert [c["started_at"] for c in full["calls"]] == sorted(c["started_at"] for c in full["calls"])
    assert store.get_run(run["id"], str(uuid.uuid4())) is None
    assert store.get_run(str(uuid.uuid4())) is None

    store.set_run_status(run["id"], "complete", completed_at="2026-10-11T20:00:00+00:00")
    again = store.get_run(run["id"])
    assert again["status"] == "complete" and again["completed_at"]
    assert [r["id"] for r in store.list_runs(user_id)] == [run["id"]]

    store.update_call_state(rows[0]["id"], "done", 6)
    assert [c["id"] for c in store.list_calls(run["id"], nonterminal_only=True)] == [rows[1]["id"]]


def test_last_outcomes_for_vendor(backend):
    store, user_id = backend
    vendors = _vendors(store, user_id)
    movers, isp = vendors[0], vendors[1]
    if movers["key"] != "movers_a":
        movers, isp = isp, movers
    other_user = str(uuid.uuid4())

    run_old = store.create_run(user_id, "first")
    (old,) = store.insert_calls([_call_row(run_old["id"], movers, started_at="2026-01-01T00:00:00+00:00")])
    store.update_call(old["id"], {"outcome": {"track": "quote", "amount_usd": 100.0}})
    run_new = store.create_run(user_id, "second")
    (new,) = store.insert_calls([_call_row(run_new["id"], movers, started_at="2026-06-01T00:00:00+00:00")])
    store.update_call(new["id"], {"outcome": {"track": "quote", "amount_usd": 90.0}})
    (pending,) = store.insert_calls(
        [_call_row(run_new["id"], movers, started_at="2026-07-01T00:00:00+00:00")]
    )
    (isp_call,) = store.insert_calls([_call_row(run_new["id"], isp)])
    store.update_call(isp_call["id"], {"outcome": {"track": "negotiate", "action": "reduced"}})

    rows = store.last_outcomes_for_vendor(user_id, "movers_a")
    assert [r["id"] for r in rows] == [new["id"], old["id"]]
    assert rows[0]["outcome"]["amount_usd"] == 90.0
    assert pending["id"] not in {r["id"] for r in rows}
    assert store.last_outcomes_for_vendor(user_id, "movers_a", limit=1)[0]["id"] == new["id"]
    assert store.last_outcomes_for_vendor(other_user, "movers_a") == []
    assert store.last_outcomes_for_vendor(user_id, "nobody") == []


def test_events_and_plan_cache(backend):
    store, user_id = backend
    run = store.create_run(user_id, "move me")
    first = store.insert_event(user_id, "run_requested", {"request": "move me"}, run_id=run["id"])
    second = store.insert_event(user_id, "run_aggregated", {"n_calls": 0}, run_id=run["id"])
    assert first["type"] == "run_requested" and first["data"] == {"request": "move me"}
    listed = store.list_events(user_id, limit=10)
    assert [e["id"] for e in listed][:2] == [second["id"], first["id"]]

    request_hash = f"test-{uuid.uuid4().hex}"
    assert store.get_plan_cache(request_hash) is None
    plans = [{"vendor_key": "movers_a", "track": "quote", "objective": "quote", "voice_gender": "female"}]
    try:
        store.put_plan_cache(request_hash, plans)
        store.put_plan_cache(request_hash, plans)
        assert store.get_plan_cache(request_hash) == plans
    finally:
        # plan_cache has no user_id, so deleting the auth user does not clean it up.
        if isinstance(store, SupabaseStore):
            store.client.table("plan_cache").delete().eq("request_hash", request_hash).execute()


def test_due_ledger(backend):
    store, user_id = backend
    vendor = _vendors(store, user_id)[0]
    run = store.create_run(user_id, "move me")
    (call,) = store.insert_calls([_call_row(run["id"], vendor)])
    today = date(2026, 10, 11)
    inserted = store.insert_ledger(
        [
            {
                "user_id": user_id,
                "vendor_id": vendor["id"],
                "kind": "promo_expiry",
                "label": "Fiber Co promo",
                "amount_usd": 89.0,
                "effective_on": (today - timedelta(days=365)).isoformat(),
                "expires_on": (today - timedelta(days=1)).isoformat(),
                "source_call": call["id"],
            },
            {
                "user_id": user_id,
                "vendor_id": vendor["id"],
                "kind": "renewal",
                "label": "Movers deposit",
                "amount_usd": None,
                "expires_on": (today + timedelta(days=30)).isoformat(),
                "source_call": call["id"],
            },
            {"user_id": user_id, "kind": "quote", "label": "undated", "amount_usd": 12.5},
        ]
    )
    assert len(inserted) == 3
    assert inserted[0]["kind"] == "promo_expiry" and inserted[0]["source_call"] == call["id"]
    assert float(inserted[0]["amount_usd"]) == 89.0

    due = store.due_ledger(user_id, today.isoformat())
    assert [r["label"] for r in due] == ["Fiber Co promo"]
    assert str(due[0]["expires_on"])[:10] == (today - timedelta(days=1)).isoformat()
    assert [r["label"] for r in store.due_ledger(user_id, f"{today.isoformat()}T12:00:00+00:00")] == [
        "Fiber Co promo"
    ]
    later = store.due_ledger(user_id, (today + timedelta(days=60)).isoformat())
    assert [r["label"] for r in later] == ["Fiber Co promo", "Movers deposit"]
    assert store.due_ledger(str(uuid.uuid4()), today.isoformat()) == []

    listed = store.list_ledger(user_id)
    assert [r["label"] for r in listed] == ["Fiber Co promo", "Movers deposit", "undated"]


def test_user_call_history_and_counts(backend):
    """list_calls_for_user, count_events_by_type, count_calls_by_state, count_calls_by_simulated and
    real_dial_calls: same answers from both backends (GET /calls, /evidence, /accuracy depend on them)."""
    from server.store import REAL_CALL_COLUMNS

    store, user_id = backend
    vendors = _vendors(store, user_id)
    movers, isp = vendors[0], vendors[1]
    if movers["key"] != "movers_a":
        movers, isp = isp, movers
    tag = uuid.uuid4().hex[:8]

    run_a = store.create_run(user_id, "first request")
    run_b = store.create_run(user_id, "second request")
    rows = store.insert_calls(
        [
            _call_row(
                run_a["id"],
                movers,
                started_at="2026-10-01T00:00:00+00:00",
                ended_at="2026-10-01T00:01:00+00:00",
                dial_call_id=f"dc-{tag}-1",
                state="done",
                state_rank=6,
                termination_type="completed",
                duration_seconds=40.0,
            ),
            _call_row(
                run_a["id"],
                isp,
                started_at="2026-10-02T00:00:00+00:00",
                simulated=True,
                state="done",
                state_rank=6,
            ),
            _call_row(
                run_b["id"],
                movers,
                started_at="2026-10-03T00:00:00+00:00",
                dial_call_id=f"dc-{tag}-2",
                state="failed",
                state_rank=6,
                termination_type="voicemail",
            ),
            _call_row(run_b["id"], isp, started_at="2026-10-04T00:00:00+00:00"),
        ]
    )
    done_real, done_sim, failed_real, planned = rows
    store.insert_event(user_id, "run_requested", {"request": "first request"}, run_id=run_a["id"])
    store.insert_event(user_id, "call_dispatched", {}, run_id=run_a["id"], call_id=done_real["id"])
    store.insert_event(user_id, "call_dispatched", {}, run_id=run_a["id"], call_id=done_sim["id"])

    listed = store.list_calls_for_user(user_id)
    assert [c["id"] for c in listed] == [planned["id"], failed_real["id"], done_sim["id"], done_real["id"]]
    assert [c["request"] for c in listed] == ["second request"] * 2 + ["first request"] * 2
    assert "runs" not in listed[0]
    assert listed[0]["simulated"] is False and listed[2]["simulated"] is True
    assert listed[-1]["dial_call_id"] == f"dc-{tag}-1" and listed[-1]["target_phone"] == movers["phone"]
    page = store.list_calls_for_user(user_id, limit=2, offset=1)
    assert [c["id"] for c in page] == [failed_real["id"], done_sim["id"]]
    assert store.list_calls_for_user(user_id, limit=1, offset=3)[0]["id"] == done_real["id"]
    assert store.list_calls_for_user(user_id, limit=0) == []
    assert store.list_calls_for_user(user_id, offset=50) == []
    assert store.list_calls_for_user(str(uuid.uuid4())) == []

    assert store.count_events_by_type(user_id) == {"call_dispatched": 2, "run_requested": 1}
    assert store.count_events_by_type(str(uuid.uuid4())) == {}
    assert store.count_calls_by_state(user_id) == {"done": 2, "failed": 1, "planned": 1}
    assert store.count_calls_by_state(str(uuid.uuid4())) == {}
    assert store.count_calls_by_simulated(user_id) == {"real_dial": 2, "simulated": 1, "undialed": 1}
    assert store.count_calls_by_simulated(str(uuid.uuid4())) == {
        "real_dial": 0,
        "simulated": 0,
        "undialed": 0,
    }

    real = store.real_dial_calls(user_id)
    assert [r["id"] for r in real] == [failed_real["id"], done_real["id"]]
    assert all(set(r) == set(REAL_CALL_COLUMNS) for r in real)
    assert real[0]["termination_type"] == "voicemail" and real[0]["state"] == "failed"
    assert real[1]["dial_call_id"] == f"dc-{tag}-1" and float(real[1]["duration_seconds"]) == 40.0
    assert real[1]["target_name"] == movers["name"] and real[1]["track"] == "quote"
    assert str(real[1]["ended_at"]).startswith("2026-10-01T00:01:00")
    assert [r["id"] for r in store.real_dial_calls(user_id, limit=1)] == [failed_real["id"]]
    assert store.real_dial_calls(str(uuid.uuid4())) == []


# ---------------------------------------------------------------------------------------------
# Proposals (0002_proposals.sql) and the vendors columns it adds
# ---------------------------------------------------------------------------------------------


def test_proposal_crud(backend):
    store, user_id = backend
    body = {
        "request": "move me",
        "items": [
            {"id": "a", "category": "internet", "vendor": {"name": "FiberFast", "phone": "+15550100001"}},
            {"id": "b", "category": "dentist", "vendor": {"name": "Smiles", "phone": None}},
        ],
        "regulations": [],
        "errors": [],
        "meta": {"web_search": True},
    }
    row = store.create_proposal(user_id, "move me", body)
    assert row["user_id"] == user_id and row["request"] == "move me"
    assert row["status"] == "proposed"
    assert row["proposal"] == body
    assert row["approved_item_ids"] == [] and row["run_id"] is None
    assert row["created_at"] and row["updated_at"]

    assert store.get_proposal(row["id"])["id"] == row["id"]
    assert store.get_proposal(row["id"], user_id)["proposal"]["items"][1]["id"] == "b"
    assert store.get_proposal(row["id"], str(uuid.uuid4())) is None
    assert store.get_proposal(str(uuid.uuid4())) is None

    failed = store.create_proposal(user_id, "nothing found", {"items": [], "errors": ["no sources"]}, "failed")
    assert failed["status"] == "failed"
    with pytest.raises(ValueError):
        store.create_proposal(user_id, "bad", {}, status="done")

    listed = store.list_proposals(user_id)
    assert [p["id"] for p in listed] == [failed["id"], row["id"]], "newest first"
    assert all("proposal" not in p for p in listed), "the listing never carries the body"
    assert [p["n_items"] for p in listed] == [0, 2]
    assert [p["status"] for p in listed] == ["failed", "proposed"]
    assert set(listed[0]) == {
        "id",
        "user_id",
        "request",
        "status",
        "approved_item_ids",
        "run_id",
        "created_at",
        "updated_at",
        "n_items",
    }
    assert [p["id"] for p in store.list_proposals(user_id, limit=1)] == [failed["id"]]
    assert store.list_proposals(str(uuid.uuid4())) == []

    run = store.create_run(user_id, "move me")
    updated = store.update_proposal(
        row["id"],
        {"status": "dispatched", "run_id": run["id"], "approved_item_ids": ["a"], "user_id": "ignored", "id": "x"},
    )
    assert updated["id"] == row["id"] and updated["user_id"] == user_id
    assert updated["status"] == "dispatched" and updated["run_id"] == run["id"]
    assert updated["approved_item_ids"] == ["a"]
    assert updated["proposal"] == body
    assert updated["updated_at"] >= row["updated_at"]
    assert store.get_proposal(row["id"])["status"] == "dispatched"
    assert store.update_proposal(str(uuid.uuid4()), {"status": "rejected"}) is None
    with pytest.raises(ValueError):
        store.update_proposal(row["id"], {"status": "whatever"})
    assert store.update_proposal(failed["id"], {"status": "rejected"})["status"] == "rejected"


def test_vendors_persist_source_and_website(backend):
    store, user_id = backend
    (row,) = store.upsert_vendors(
        user_id,
        [
            {
                "key": "r_internet-fiberfast",
                "name": "FiberFast",
                "track": "negotiate",
                "phone": "+15550100001",
                "is_demo": False,
                "source": "research",
                "website": "https://fiberfast.example/plans",
            }
        ],
    )
    assert row["source"] == "research"
    assert row["website"] == "https://fiberfast.example/plans"
    assert row["is_demo"] is False
    plain = _vendors(store, user_id)[0]
    assert plain["source"] == "manual" and plain["website"] is None
    # An upsert that omits source and website leaves them alone.
    (again,) = store.upsert_vendors(
        user_id,
        [{"key": "r_internet-fiberfast", "name": "FiberFast Inc", "track": "negotiate", "phone": "+15550100001"}],
    )
    assert again["id"] == row["id"] and again["name"] == "FiberFast Inc"
    assert again["source"] == "research" and again["website"] == "https://fiberfast.example/plans"


def test_sqlite_self_migrates_older_database(tmp_path):
    """A .calls/dormammu.db created before 0002 gets the vendors columns and the proposals table on open."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        create table profiles (id text primary key, display_name text, phone text, prefs text not null default '{}',
          cohort text not null default 'student', created_at text not null);
        create table vendors (id text primary key, owner_id text not null, key text not null, name text not null,
          track text not null, phone text not null, is_demo integer not null default 0, unique (owner_id, key));
        insert into profiles (id, created_at) values ('u1', '2026-01-01T00:00:00+00:00');
        insert into vendors (id, owner_id, key, name, track, phone, is_demo)
          values ('v1', 'u1', 'movers_a', 'Pack & Go', 'quote', '+15550000001', 1);
        """
    )
    conn.commit()
    conn.close()

    store = SqliteStore(path)
    (vendor,) = store.list_vendors("u1")
    assert vendor["source"] == "manual" and vendor["website"] is None and vendor["is_demo"] is True
    row = store.create_proposal("u1", "move me", {"items": []})
    assert store.get_proposal(row["id"])["status"] == "proposed"
    # Opening it again is a no-op.
    again = SqliteStore(path)
    assert again.list_vendors("u1")[0]["source"] == "manual"
