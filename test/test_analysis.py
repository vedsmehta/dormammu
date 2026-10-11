"""analysis.* over a SqliteStore: zeros on an empty store, real numbers on seeded history, null-safety on
broken rows, prior context sentences, and the Keewano MCP client against a mocked endpoint."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from analysis import keewano_queries
from analysis.context import prior_context_for, prior_context_from_insights
from analysis.insights import compute_insights
from scripts.seed_demo import generate
from server.store import SqliteStore

NOW = datetime(2026, 10, 11, 20, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "analysis.db")


@pytest.fixture
def seeded(store):
    user_id = str(uuid.uuid4())
    store.upsert_profile(user_id, {"display_name": "demo"})
    summary = generate(store, user_id, runs=14, days=45, seed=3, add_roster=True, now=NOW)
    return store, user_id, summary


def test_empty_store_returns_zeros(store):
    user_id = str(uuid.uuid4())
    store.upsert_profile(user_id, {"display_name": "nobody"})
    report = compute_insights(store, user_id, now=NOW)
    json.dumps(report)
    assert report["coverage"]["n_runs"] == 0
    assert report["coverage"]["n_calls"] == 0
    assert report["coverage"]["label"] == "no calls yet"
    assert report["vendor_scorecards"] == {}
    assert report["recommendations"] == []
    assert report["failure_modes"]["n_failed"] == 0
    assert report["failure_modes"]["failure_rate"] is None
    assert report["time_of_day"]["enough_data"] is False
    assert report["context_effect"]["n_repeat_vendors"] == 0
    for track in ("quote", "schedule", "negotiate"):
        assert report["track_stats"][track]["n_calls"] == 0
        assert report["track_stats"][track]["median_outcome"] is None


def test_unknown_user_and_broken_store_do_not_raise(store):
    report = compute_insights(store, "not-a-user", now=NOW)
    assert report["coverage"]["n_runs"] == 0

    class Broken:
        def list_runs(self, *a, **k):
            raise RuntimeError("down")

        def get_run(self, *a, **k):
            raise RuntimeError("down")

        def list_events(self, *a, **k):
            raise RuntimeError("down")

    report = compute_insights(Broken(), "u", now=NOW)
    assert report["coverage"]["n_calls"] == 0


def test_seeded_history_produces_scorecards_and_recommendations(seeded):
    store, user_id, summary = seeded
    report = compute_insights(store, user_id, now=NOW)
    json.dumps(report)

    cov = report["coverage"]
    assert cov["n_runs"] == summary["n_runs"] == 14
    assert cov["n_calls"] == summary["n_calls"] > 20
    assert cov["n_seeded"] == cov["n_calls"]
    assert cov["label"] == "all seeded or simulated demo data"
    assert cov["n_events"] == summary["n_events"] and cov["n_events_seeded"] == cov["n_events"]

    cards = report["vendor_scorecards"]
    assert cards
    for key, card in cards.items():
        assert card["vendor_key"] == key
        assert card["n_calls"] == card["n_done"] + card["n_failed"] + card["n_live"]
        assert isinstance(card["takeaway"], str) and card["takeaway"]
        assert card["answer_rate"] is None or 0 <= card["answer_rate"] <= 1
        assert set(card["failure_breakdown"]) <= {"voicemail", "no-answer", "busy", "unknown"}
        if card["track"] == "quote" and card.get("n_quotes"):
            assert card["best_quote_usd"] <= card["latest_quote_usd"]
            assert card["quote_trend"] in (None, "up", "down", "flat")
        if card["track"] == "schedule":
            assert card["confirmed_rate"] is None or 0 <= card["confirmed_rate"] <= 1
        if card["track"] == "negotiate":
            assert card["total_saved_usd_per_month"] >= 0

    stats = report["track_stats"]
    assert stats["quote"]["n_calls"] > 0 and stats["quote"]["median_outcome"] > 0
    assert stats["quote"]["multiplier_label"] == "est."
    assert stats["negotiate"]["median_outcome_label"].startswith("median saved")

    modes = report["failure_modes"]
    assert modes["n_failed"] == cov["n_failed"]
    assert sum(modes["counts"].values()) == modes["n_failed"]
    if modes["n_failed"]:
        assert abs(sum(modes["share"].values()) - 1.0) < 0.01

    effect = report["context_effect"]
    assert effect["n_repeat_vendors"] >= 1
    assert effect["n_later_calls"] >= effect["n_later_with_prior_context"] >= 1

    recs = report["recommendations"]
    assert recs and recs[0]["rank"] == 1
    for rec in recs:
        assert rec["text"] and rec["kind"] and isinstance(rec["evidence"], dict)
    assert [r["score"] for r in recs] == sorted((r["score"] for r in recs), reverse=True)


def test_cheapest_mover_is_recommended_first(seeded):
    store, user_id, _summary = seeded
    report = compute_insights(store, user_id, now=NOW)
    card = report["vendor_scorecards"]["movers_a"]
    assert card["n_compared"] >= 2
    assert card["times_cheapest"] / card["n_compared"] >= 0.5
    kinds = {(r["kind"], r["vendor_key"]) for r in report["recommendations"]}
    assert ("call_first", "movers_a") in kinds


def test_null_safe_on_broken_rows(store):
    user_id = str(uuid.uuid4())
    store.upsert_profile(user_id, {"display_name": "edge"})
    run = store.create_run(user_id, "edge cases")
    store.insert_calls(
        [
            {
                "run_id": run["id"],
                "vendor_key": "ghost",
                "track": "quote",
                "target_name": "Ghost",
                "target_phone": "+15550109999",
                "objective": "x",
                "state": "failed",
                "state_rank": 6,
                "termination_type": None,
                "started_at": "2026-10-01T17:00:00+00:00",
            },
            {
                "run_id": run["id"],
                "vendor_key": "ghost",
                "track": "quote",
                "target_name": "Ghost",
                "target_phone": "+15550109999",
                "objective": "x",
                "state": "done",
                "state_rank": 6,
                "outcome": {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": "unreadable"},
                "started_at": "2026-10-02T17:00:00+00:00",
                "ended_at": None,
            },
            {
                "run_id": run["id"],
                "vendor_key": "ghost",
                "track": "quote",
                "target_name": "Ghost",
                "target_phone": "+15550109999",
                "objective": "x",
                "state": "done",
                "state_rank": 6,
                "outcome": {"track": "quote", "amount_usd": "1200", "unit": "flat", "caveats": [], "note": None},
                "duration_seconds": None,
                "started_at": "not a timestamp",
                "ended_at": "2026-10-03T17:05:00+00:00",
            },
            {
                "run_id": run["id"],
                "vendor_key": None,
                "track": "negotiate",
                "target_name": "",
                "target_phone": "+15550109998",
                "objective": "x",
                "state": "planned",
                "state_rank": 0,
                "started_at": "2026-10-03T17:00:00+00:00",
            },
            {
                "run_id": run["id"],
                "vendor_key": "clinic",
                "track": "schedule",
                "target_name": "Clinic",
                "target_phone": "+15550109997",
                "objective": "x",
                "state": "done",
                "state_rank": 6,
                "outcome": {"track": "schedule", "slot_iso": "garbage", "confirmed": True, "note": None},
                "started_at": "2026-10-03T18:00:00+00:00",
                "ended_at": "2026-10-03T18:02:00+00:00",
            },
        ]
    )
    # An outcome stored as a JSON string must also be readable.
    row = store.insert_calls(
        [
            {
                "run_id": run["id"],
                "vendor_key": "ghost",
                "track": "quote",
                "target_name": "Ghost",
                "target_phone": "+15550109999",
                "objective": "x",
                "state": "done",
                "state_rank": 6,
                "started_at": "2026-10-04T17:00:00+00:00",
                "ended_at": "2026-10-04T17:03:00+00:00",
            }
        ]
    )[0]
    with store._lock, store._conn:
        store._conn.execute("update calls set outcome = ? where id = ?", ['"not an object"', row["id"]])
    store.insert_event(user_id, "weird", {"seeded": "yes"}, run_id=run["id"])

    report = compute_insights(store, user_id, now=NOW)
    json.dumps(report)
    ghost = report["vendor_scorecards"]["ghost"]
    assert ghost["n_calls"] == 4 and ghost["n_done"] == 3 and ghost["n_failed"] == 1
    assert ghost["failure_breakdown"] == {"unknown": 1}
    assert ghost["best_quote_usd"] == 1200.0 and ghost["n_quotes"] == 1
    assert ghost["avg_duration_seconds"] is None
    assert "unknown" in report["vendor_scorecards"]
    assert report["track_stats"]["schedule"]["median_outcome"] is None
    assert report["coverage"]["n_live"] == 1
    assert report["coverage"]["label"] == "live calls only"


def test_prior_context_from_insights(seeded):
    store, user_id, _summary = seeded
    report = compute_insights(store, user_id, now=NOW)
    text = prior_context_from_insights(report, "movers_a", "quote")
    assert text and text.startswith("Last time (") and "quoted $" in text
    assert "Across" in text and "calls they" in text
    assert text.count(".") <= 3

    dentist = prior_context_from_insights(report, "dentist")
    assert dentist and ("booked" in dentist or "confirmed" in dentist)

    assert prior_context_from_insights(report, "nobody_here", "quote") is None
    assert prior_context_from_insights(None, "movers_a", "quote") is None
    assert prior_context_from_insights({"vendor_scorecards": {"x": "bad"}}, "x") is None

    via_store = prior_context_for(store, user_id, "movers_a", "quote")
    assert via_store == text
    assert prior_context_for(store, user_id, "movers_a", "quote", insights=report) == text


def test_prior_context_without_outcome_uses_pattern_only():
    report = {
        "vendor_scorecards": {
            "comcast": {
                "name": "Comcast Retention",
                "track": "negotiate",
                "n_done": 0,
                "n_failed": 3,
                "failure_breakdown": {"voicemail": 3},
                "last_outcome": None,
                "time_of_day": {"before_11": {"n_calls": 3, "n_answered": 0, "n_voicemail": 3}},
            }
        }
    }
    text = prior_context_from_insights(report, "comcast")
    assert text == "Across 3 calls they never answered (voicemail 3). They went to voicemail 3 of 3 times before 11am."


# ---------------------------------------------------------------------------------------------
# Keewano MCP client


def _rpc(result: dict, *, is_error: bool = False) -> dict:
    content = [{"type": "text", "text": json.dumps(result)}]
    payload: dict = {"content": content, "structuredContent": result}
    if is_error:
        payload = {"content": content, "isError": True}
    return {"jsonrpc": "2.0", "id": 1, "result": payload}


def test_render_arguments_substitutes_user_and_window():
    query = keewano_queries.QUERIES_BY_NAME["activity_by_day"]
    args = keewano_queries.render_arguments(query, "proj", "user-1", since=NOW, until=NOW)
    assert args["projectId"] == "proj"
    assert "user-1" in args["sqlQuery"] and "{" not in args["sqlQuery"]
    lua = keewano_queries.render_arguments(keewano_queries.QUERIES_BY_NAME["runs_and_gaps"], "proj", "user-1")
    assert lua["filters"] == [{"operation": 13, "value": ["user-1"]}]
    assert "process_user" in lua["luaScript"]


def test_every_query_declares_its_needs():
    for query in keewano_queries.KEEWANO_QUERIES:
        assert query["tool"] in ("run_sql_query", "run_script_query")
        assert query["status"] in (
            keewano_queries.RUNNABLE_TODAY,
            keewano_queries.AFTER_INGEST,
            keewano_queries.NEEDS_SCALARS,
        )
        assert isinstance(query["needs"]["event_types"], list)
        if query["status"] == keewano_queries.NEEDS_SCALARS:
            proposed = {t["name"] for t in keewano_queries.PROPOSED_EVENT_TYPES}
            assert set(query["needs"]["event_types"]) & proposed


async def test_keewano_insights_without_token_is_none(monkeypatch):
    monkeypatch.delenv("KEEWANO_PAT", raising=False)
    monkeypatch.delenv("KEEWANO_TOKEN", raising=False)
    assert await keewano_queries.keewano_insights("user-1") is None


async def test_keewano_insights_prefers_pat_and_parses_results(monkeypatch, httpx_mock):
    monkeypatch.setenv("KEEWANO_PAT", "pat-token")
    monkeypatch.setenv("KEEWANO_TOKEN", "project-key")
    monkeypatch.delenv("KEEWANO_PROJECT_ID", raising=False)
    seen: list[dict] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"token": request.headers.get("k-token"), "name": body["params"]["name"], "args": body["params"]["arguments"]})
        name = body["params"]["name"]
        if name == "list_projects":
            return httpx.Response(200, json=_rpc({"projects": [{"id": "proj-1", "name": "SpeedHacks26"}]}))
        sql = body["params"]["arguments"].get("sqlQuery", "")
        if name == "run_sql_query" and any(f'"{col}"' in sql for col in keewano_queries.custom_event_ids()):
            return httpx.Response(200, json=_rpc({"error": 'unknown column "run_requested"'}, is_error=True))
        if name == "run_script_query":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "unknown event name"}})
        return httpx.Response(200, json=_rpc({"columns": [{"name": "users"}], "rows": [[0]]}))

    httpx_mock.add_callback(reply, url=keewano_queries.MCP_URL, method="POST", is_reusable=True)
    report = await keewano_queries.keewano_insights("user-1", since=NOW, until=NOW)
    assert report is not None
    assert report["project_id"] == "proj-1"
    assert all(s["token"] == "pat-token" for s in seen)
    assert seen[0]["name"] == "list_projects"
    queries = report["queries"]
    assert queries["project_activity"]["ok"] is True
    assert queries["project_activity"]["result"]["rows"] == [[0]]
    assert queries["activity_by_day"]["ok"] is False and "unknown column" in queries["activity_by_day"]["error"]
    assert queries["runs_and_gaps"]["ok"] is False and "unknown event name" in queries["runs_and_gaps"]["error"]
    assert queries["failure_modes"]["skipped"] == keewano_queries.NEEDS_SCALARS
    assert report["n_ok"] == 1
    user_scoped = [s["args"]["sqlQuery"] for s in seen if "user_id = '" in s["args"].get("sqlQuery", "")]
    assert user_scoped and all("user-1" in sql for sql in user_scoped)


async def test_keewano_insights_transport_failure_is_none(monkeypatch, httpx_mock):
    monkeypatch.setenv("KEEWANO_PAT", "pat-token")
    monkeypatch.delenv("KEEWANO_PROJECT_ID", raising=False)
    httpx_mock.add_response(url=keewano_queries.MCP_URL, method="POST", status_code=500, text="boom")
    assert await keewano_queries.keewano_insights("user-1") is None


async def test_keewano_insights_stops_at_plan_limit(monkeypatch, httpx_mock):
    monkeypatch.setenv("KEEWANO_PAT", "pat-token")
    monkeypatch.setenv("KEEWANO_PROJECT_ID", "proj-env")
    calls = 0

    def reply(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_rpc({"message": "Your organization's Keewano plan limit has been reached"}, is_error=True))

    httpx_mock.add_callback(reply, url=keewano_queries.MCP_URL, method="POST", is_reusable=True)
    report = await keewano_queries.keewano_insights("user-1")
    assert report is not None and report["project_id"] == "proj-env"
    assert calls == 1
    assert "plan limit" in report["note"]
