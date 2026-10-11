"""Service tests: FastAPI TestClient against SqliteStore with a fake Dial.

Only the Dial HTTP layer, the router's LLM plan, and the extractor's LLM call are faked. dispatch_run,
ingest_call_update, advance_simulated, aggregate and ledger_rows are the real modules. The fake Dial
answers two calls and refuses the rest with 429 call_limit_reached, so 2 rows are live and 3 are
labeled simulated, which is the shape the demo runs under (account cap is 2).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from call import dial_client, dispatch, ingest
from call.dial_client import DialError
from router.instructions import AI_DISCLOSURE
from server import app as app_module
from server.store import SqliteStore

USER = "00000000-0000-4000-8000-000000000001"
MCP_SECRET = "test-mcp-secret"
P = "/api/py"

VENDORS = [
    {"key": "movers_a", "name": "Pack & Go", "track": "quote", "phone": "+15550000001", "is_demo": True},
    {"key": "movers_b", "name": "Bay Haulers", "track": "quote", "phone": "+15550000002", "is_demo": True},
    {"key": "isp_a", "name": "Fiber Co", "track": "negotiate", "phone": "+15550000003", "is_demo": True},
    {
        "key": "dentist_a",
        "name": "Smile Dental",
        "track": "schedule",
        "phone": "+15550000004",
        "is_demo": True,
    },
    {"key": "storage_a", "name": "Box Storage", "track": "quote", "phone": "+15550000005", "is_demo": True},
]

TRANSCRIPT_TURNS = [
    {
        "speaker": "agent",
        "text": "Hi, I am an AI assistant calling on behalf of a client.",
        "startMs": 0,
        "endMs": 4000,
    },
    {"speaker": "user", "text": "Sure, how can I help?", "startMs": 4500, "endMs": 6000},
    {
        "speaker": "agent",
        "text": "I would like a quote for a two bedroom move on November first.",
        "startMs": 6500,
        "endMs": 11000,
    },
    {
        "speaker": "user",
        "text": "That would be fourteen fifty flat, excluding packing.",
        "startMs": 12000,
        "endMs": 17000,
    },
    {
        "speaker": "agent",
        "text": "Summary: fourteen fifty flat, excluding packing. Goodbye.",
        "startMs": 18000,
        "endMs": 24000,
    },
]
TRANSCRIPT_TEXT = "\n".join(f"{t['speaker']}: {t['text']}" for t in TRANSCRIPT_TURNS)

FAKE_OUTCOMES = {
    "quote": {
        "track": "quote",
        "amount_usd": 1450.0,
        "unit": "flat",
        "caveats": ["excludes packing"],
        "note": None,
    },
    "schedule": {
        "track": "schedule",
        "slot_iso": "2026-10-20T14:00:00-07:00",
        "confirmed": True,
        "note": None,
    },
    "negotiate": {
        "track": "negotiate",
        "action": "reduced",
        "saved_usd": 20.0,
        "effective_date": "2026-11-01",
        "note": None,
    },
}


class FakeDial:
    """Queued -> Ringing -> In-Progress -> Terminated/completed (transcript null) -> transcript present."""

    def __init__(self, limit: int = 2) -> None:
        self.limit = limit
        self.placed: list[str] = []
        self.polls: dict[str, int] = {}
        self.instructions: dict[str, str] = {}

    async def place_call(
        self,
        to: str,
        instruction: str,
        *,
        from_number: str | None = None,
        voice_gender: str = "female",
        max_duration_seconds: int = 180,
        transfer_to: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        assert instruction.startswith("Your first words on this call") and AI_DISCLOSURE in instruction, (
            "AI disclosure must come first in every instruction"
        )
        assert max_duration_seconds == 180
        assert idempotency_key
        if len(self.placed) >= self.limit:
            raise DialError(429, "call_limit_reached", "Concurrent call limit reached")
        call_id = f"dc_{len(self.placed) + 1:03d}"
        self.placed.append(call_id)
        self.polls[call_id] = 0
        self.instructions[call_id] = instruction
        return self._call(call_id, "Queued", None)

    def _call(
        self,
        call_id: str,
        state: str,
        termination: str | None,
        *,
        transcript: bool = False,
        duration: int = 0,
    ) -> dict:
        return {
            "id": call_id,
            "phoneNumberId": "pn_1",
            "from": "+15550009999",
            "to": "+15550000000",
            "direction": "outbound",
            "duration": duration,
            "transcript": TRANSCRIPT_TEXT if transcript else None,
            "transcriptTurns": TRANSCRIPT_TURNS if transcript else None,
            "instruction": self.instructions.get(call_id),
            "terminationType": termination,
            "terminatedAt": "2026-10-11T20:31:00.000Z" if termination else None,
            "status": {
                "state": state,
                "terminationType": termination,
                "cancelRequested": False,
                "cancelPending": False,
                "label": state,
            },
            "createdAt": "2026-10-11T20:30:00.000Z",
        }

    async def get_call(self, call_id: str) -> dict:
        n = self.polls[call_id]
        self.polls[call_id] = n + 1
        if n == 0:
            return self._call(call_id, "Queued", None)
        if n == 1:
            return self._call(call_id, "Ringing", None)
        if n == 2:
            return self._call(call_id, "In-Progress", None)
        if n == 3:
            return self._call(call_id, "Terminated", "completed", transcript=False, duration=42)
        return self._call(call_id, "Terminated", "completed", transcript=True, duration=42)


@pytest.fixture
def fake_dial(monkeypatch):
    fake = FakeDial()
    monkeypatch.setattr(dial_client, "place_call", fake.place_call)
    monkeypatch.setattr(dial_client, "get_call", fake.get_call)
    monkeypatch.setattr(dispatch, "place_call", fake.place_call, raising=False)
    monkeypatch.setattr(ingest, "get_call", fake.get_call, raising=False)
    return fake


@pytest.fixture
def client(tmp_path, monkeypatch, fake_dial):
    monkeypatch.setenv("DORMAMMU_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("DEV_USER_ID", USER)
    monkeypatch.setenv("MCP_SHARED_SECRET", MCP_SECRET)
    monkeypatch.setenv("SIMULATED_TALK_SECONDS", "1")
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "5")
    monkeypatch.delenv("MCP_USER_ID", raising=False)
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)

    async def fake_plan(store, request, user_id, **_kwargs):
        return [
            {
                "vendor_key": v["key"],
                "track": v["track"],
                "objective": f"{request} ({v['track']})",
                "voice_gender": "female",
            }
            for v in store.list_vendors(user_id)
        ]

    async def fake_extract(track, transcript):
        assert transcript, "extraction must never run on an empty transcript"
        return dict(FAKE_OUTCOMES[track])

    monkeypatch.setattr(app_module.planner, "plan", fake_plan)
    monkeypatch.setattr(app_module.extract_mod, "extract", fake_extract)

    store = SqliteStore(tmp_path / "app.db")
    app_module.set_store(store)
    with TestClient(app_module.app) as c:
        yield c, store
    app_module.set_store(None)


def _parse(ts: str) -> datetime:
    parsed = datetime.fromisoformat(ts)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _split(run: dict) -> tuple[list[dict], list[dict]]:
    real = [c for c in run["calls"] if not c["simulated"]]
    sim = [c for c in run["calls"] if c["simulated"]]
    return real, sim


def test_health_sets_no_store(client):
    c, _ = client
    r = c.get(f"{P}/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "store": "sqlite"}
    assert r.headers["cache-control"] == "no-store"


def test_vendors_upsert(client):
    c, _ = client
    r = c.post(f"{P}/vendors", json={"vendors": VENDORS})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 5
    assert {v["key"] for v in rows} == {v["key"] for v in VENDORS}
    assert all(v["owner_id"] == USER for v in rows)
    r = c.post(f"{P}/vendors", json={"vendors": [{**VENDORS[0], "name": "Pack & Go Movers"}]})
    assert r.status_code == 200
    listed = c.get(f"{P}/vendors").json()
    assert len(listed) == 5
    assert next(v for v in listed if v["key"] == "movers_a")["name"] == "Pack & Go Movers"
    assert c.get(f"{P}/profile").json()["id"] == USER
    bad = c.post(
        f"{P}/vendors", json={"vendors": [{"key": "x", "name": "x", "track": "sell", "phone": "+1"}]}
    )
    assert bad.status_code == 422


def test_run_requires_vendors_and_matching_plans(client, monkeypatch):
    c, _ = client
    r = c.post(f"{P}/runs", json={"request": "move me"})
    assert r.status_code == 400
    c.post(f"{P}/vendors", json={"vendors": VENDORS})

    async def ghost_plan(store, request, user_id, **_kwargs):
        return [{"vendor_key": "ghost", "track": "quote", "objective": request, "voice_gender": "female"}]

    monkeypatch.setattr(app_module.planner, "plan", ghost_plan)
    r = c.post(f"{P}/runs", json={"request": "move me"})
    assert r.status_code == 400
    assert "no plan" in r.json()["detail"]
    assert c.get(f"{P}/runs").json() == []


def test_full_run_lifecycle(client, fake_dial, monkeypatch):
    c, _ = client
    assert c.post(f"{P}/vendors", json={"vendors": VENDORS}).status_code == 200

    r = c.post(f"{P}/runs", json={"request": "I'm moving to SF next month, handle it"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["status"] == "running"
    assert run["user_id"] == USER
    assert len(run["calls"]) == 5
    assert all(call["state"] == "dialing" for call in run["calls"])
    assert "server_now" in run and run["aggregate"]["n_calls"] == 5
    real, sim = _split(run)
    assert len(real) == 2 and len(sim) == 3
    assert all(call["dial_call_id"] for call in real)
    assert all(call["dial_call_id"] is None and call["error"] is None for call in sim)
    assert len(fake_dial.placed) == 2
    assert {call["vendor_key"] for call in run["calls"]} == {v["key"] for v in VENDORS}
    assert all(call["target_phone"].startswith("+1555") for call in run["calls"])

    events = c.get(f"{P}/events?limit=100").json()
    types = [e["type"] for e in events]
    assert types.count("run_requested") == 1
    assert types.count("call_dispatched") == 5
    assert types.count("call_simulated") == 3

    base = max(_parse(call["started_at"]) for call in run["calls"])

    def tick(seconds: float) -> dict:
        frozen = base + timedelta(seconds=seconds)
        monkeypatch.setattr(app_module, "utcnow", lambda: frozen)
        resp = c.post(f"{P}/runs/{run['id']}/tick")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["server_now"] == frozen.isoformat()
        return body

    t = tick(2)
    real, sim = _split(t)
    assert {call["state"] for call in real} == {"dialing"}
    assert {call["state"] for call in sim} == {"dialing"}

    t = tick(6)
    real, sim = _split(t)
    assert {call["state"] for call in real} == {"ringing"}
    assert {call["state"] for call in sim} == {"ringing"}

    t = tick(15)
    real, sim = _split(t)
    assert {call["state"] for call in real} == {"talking"}
    assert {call["state"] for call in sim} == {"talking"}
    assert t["status"] == "running"

    t = tick(20)
    real, sim = _split(t)
    assert {call["state"] for call in real} == {"extracting"}, (
        "Terminated/completed without a transcript waits"
    )
    assert all(call["outcome"] is None for call in real)
    assert all(call["termination_type"] == "completed" for call in real)
    assert all(call["duration_seconds"] == 42 for call in real)
    assert t["status"] == "running"

    t = tick(600)
    assert t["status"] == "complete", t
    assert t["completed_at"]
    real, sim = _split(t)
    assert {call["state"] for call in real} == {"done"}
    assert {call["state"] for call in sim} == {"done"}
    for call in real:
        assert call["outcome"] == FAKE_OUTCOMES[call["track"]]
        assert call["transcript"] == TRANSCRIPT_TEXT
        assert call["transcript_turns"] == TRANSCRIPT_TURNS
        assert call["ended_at"]
        assert call["state_rank"] == 6
    for call in sim:
        assert call["outcome"]["track"] == call["track"]
        assert "simulated" in (call["outcome"].get("note") or "")
        assert call["transcript"] and call["transcript_turns"]
    agg = t["aggregate"]
    assert agg["n_calls"] == 5 and agg["n_simulated"] == 3

    events = c.get(f"{P}/events?limit=200").json()
    types = [e["type"] for e in events]
    assert types.count("run_aggregated") == 1
    assert types.count("outcome_extracted") == 5
    assert types.count("call_connected") >= 2
    assert types.count("call_terminated") >= 2
    real_extracted = [
        e for e in events if e["type"] == "outcome_extracted" and not e["data"].get("simulated")
    ]
    assert len(real_extracted) == 2

    # A tick after completion is a no-op: no second aggregation, no state movement.
    again = tick(700)
    assert again["status"] == "complete"
    assert [call["state"] for call in again["calls"]] == ["done"] * 5
    types = [e["type"] for e in c.get(f"{P}/events?limit=200").json()]
    assert types.count("run_aggregated") == 1

    fetched = c.get(f"{P}/runs/{run['id']}").json()
    assert fetched["status"] == "complete"
    assert fetched["aggregate"]["n_calls"] == 5
    assert [r["id"] for r in c.get(f"{P}/runs").json()] == [run["id"]]
    assert c.get(f"{P}/runs/00000000-0000-4000-8000-00000000dead").status_code == 404

    ledger = c.get(f"{P}/ledger").json()
    assert isinstance(ledger, list)
    try:
        import aggregator.ledger  # noqa: F401
    except ImportError:
        return
    assert ledger, "ledger_rows should produce dated facts from these outcomes"
    assert all(row["user_id"] == USER for row in ledger)
    by_call = {call["id"]: call for call in again["calls"]}
    for row in ledger:
        source = by_call[row["source_call"]]
        assert row["vendor_id"] == source["vendor_id"]
        assert ("(simulated)" in row["label"]) == bool(source["simulated"])
    assert types.count("ledger_written") == 1


def test_ledger_fire_dispatches_retention_run(client, fake_dial):
    c, store = client
    c.post(f"{P}/vendors", json={"vendors": VENDORS})
    vendors = {v["key"]: v for v in c.get(f"{P}/vendors").json()}
    store.insert_ledger(
        [
            {
                "user_id": USER,
                "vendor_id": vendors["isp_a"]["id"],
                "kind": "promo_expiry",
                "label": "Fiber Co promo rate",
                "amount_usd": 89.0,
                "effective_on": "2025-11-01",
                "expires_on": "2026-10-01",
            },
            {
                "user_id": USER,
                "vendor_id": vendors["movers_a"]["id"],
                "kind": "quote",
                "label": "Pack & Go quote",
                "amount_usd": 1450.0,
                "expires_on": "2027-01-01",
            },
        ]
    )
    assert c.post(f"{P}/ledger/fire", json={"as_of": "2020-01-01"}).status_code == 400

    r = c.post(f"{P}/ledger/fire", json={"as_of": "2026-10-11"})
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["request"] == "Retention: Fiber Co promo rate"
    assert run["status"] == "running"
    assert len(run["calls"]) == 1
    call = run["calls"][0]
    assert call["track"] == "negotiate"
    assert call["vendor_key"] == "isp_a"
    assert call["target_name"] == "Fiber Co"
    assert call["objective"] == "Our promotional rate ends 2026-10-01; keep the rate or get a better one"
    assert call["prior_context"] == "Last year you quoted $89 for Fiber Co promo rate"
    assert call["state"] == "dialing" and call["simulated"] is False
    assert "$89" in fake_dial.instructions[call["dial_call_id"]]
    types = [e["type"] for e in c.get(f"{P}/events").json()]
    assert "run_requested" in types and "call_dispatched" in types


def test_mcp_json_rpc(client, monkeypatch):
    c, _ = client
    prefs = {
        "budget_ceiling_usd": 900,
        "preferred_times": ["weekday afternoons"],
        "availability": [
            {"start": "2026-10-15T14:00:00-07:00", "end": "2026-10-15T17:00:00-07:00"},
            {"start": "2026-10-22T09:00:00-07:00", "end": "2026-10-22T11:00:00-07:00"},
        ],
    }
    assert c.post(f"{P}/profile", json={"prefs": prefs}).json()["prefs"] == prefs

    tools_list = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert c.post(f"{P}/mcp", json=tools_list).status_code == 401
    assert c.post(f"{P}/mcp", json=tools_list, headers={"Authorization": "Bearer wrong"}).status_code == 401

    headers = {"Authorization": f"Bearer {MCP_SECRET}", "X-Dormammu-User": USER}
    r = c.post(f"{P}/mcp", json=tools_list, headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0" and body["id"] == 1
    assert {t["name"] for t in body["result"]["tools"]} == {
        "check_calendar_availability",
        "get_user_preference",
    }
    assert all("inputSchema" in t for t in body["result"]["tools"])

    init = {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "dial", "version": "1"},
        },
    }
    r = c.post(f"{P}/mcp", json=init, headers=headers)
    assert r.status_code == 200
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in r.json()["result"]["capabilities"]

    r = c.post(f"{P}/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    assert r.status_code == 202 and r.content == b""

    def call_tool(name: str, arguments: dict, hdrs: dict = headers):
        body = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return c.post(f"{P}/mcp", json=body, headers=hdrs)

    r = call_tool("get_user_preference", {"key": "budget_ceiling_usd"})
    assert r.status_code == 200
    content = r.json()["result"]["content"][0]
    assert content["type"] == "text"
    assert json.loads(content["text"]) == {"key": "budget_ceiling_usd", "value": 900}
    assert (
        json.loads(call_tool("get_user_preference", {"key": "nope"}).json()["result"]["content"][0]["text"])[
            "value"
        ]
        is None
    )

    r = call_tool("check_calendar_availability", {"date_range": "2026-10-15"})
    slots = json.loads(r.json()["result"]["content"][0]["text"])
    assert slots["free_slots"] == prefs["availability"][:1]
    r = call_tool("check_calendar_availability", {"date_range": "2026-10-14 to 2026-10-23"})
    assert len(json.loads(r.json()["result"]["content"][0]["text"])["free_slots"]) == 2
    r = call_tool("check_calendar_availability", {"date_range": "next week"})
    assert len(json.loads(r.json()["result"]["content"][0]["text"])["free_slots"]) == 2

    r = call_tool("launch_missiles", {})
    assert r.status_code == 200 and r.json()["error"]["code"] == -32602
    r = c.post(f"{P}/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, headers=headers)
    assert r.json()["error"]["code"] == -32601

    secret_only = {"Authorization": f"Bearer {MCP_SECRET}"}
    assert call_tool("get_user_preference", {"key": "budget_ceiling_usd"}, secret_only).status_code == 400
    monkeypatch.setenv("MCP_USER_ID", USER)
    r = call_tool("get_user_preference", {"key": "budget_ceiling_usd"}, secret_only)
    assert r.status_code == 200
    assert json.loads(r.json()["result"]["content"][0]["text"])["value"] == 900


def test_auth_paths(client, monkeypatch, httpx_mock):
    c, _ = client
    monkeypatch.delenv("DEV_USER_ID")
    assert c.get(f"{P}/profile").status_code == 401

    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    r = c.get(f"{P}/profile", headers={"X-Internal-Secret": "internal-secret", "X-Dormammu-User": USER})
    assert r.status_code == 200 and r.json()["id"] == USER
    assert (
        c.get(f"{P}/profile", headers={"X-Internal-Secret": "wrong", "X-Dormammu-User": USER}).status_code
        == 401
    )
    assert c.get(f"{P}/profile", headers={"X-Internal-Secret": "internal-secret"}).status_code == 401

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
    httpx_mock.add_response(
        url="https://example.supabase.co/auth/v1/user",
        match_headers={"apikey": "sb_publishable_test", "Authorization": "Bearer good-token"},
        json={"id": USER, "email": "x@example.com"},
    )
    httpx_mock.add_response(
        url="https://example.supabase.co/auth/v1/user",
        match_headers={"Authorization": "Bearer bad-token"},
        status_code=401,
        json={"msg": "invalid JWT"},
    )
    r = c.get(f"{P}/profile", headers={"Authorization": "Bearer good-token"})
    assert r.status_code == 200 and r.json()["id"] == USER
    # Second call with the same token is served from the 60s cache: no new GoTrue request.
    assert c.get(f"{P}/profile", headers={"Authorization": "Bearer good-token"}).status_code == 200
    assert c.get(f"{P}/profile", headers={"Authorization": "Bearer bad-token"}).status_code == 401
    assert len(httpx_mock.get_requests()) == 2


# ---------------------------------------------------------------------------------------------
# POST /auth/signup: password accounts via the GoTrue admin API, no email delivery anywhere
# ---------------------------------------------------------------------------------------------

SIGNUP_USER = "00000000-0000-4000-8000-0000000000aa"


class _FakeUser:
    def __init__(self, user_id: str, email: str) -> None:
        self.id = user_id
        self.email = email


class _FakeUserResponse:
    def __init__(self, user_id: str, email: str) -> None:
        self.user = _FakeUser(user_id, email)


class FakeSupabaseAdmin:
    """Stands in for create_client(SUPABASE_URL, SUPABASE_SECRET_KEY): only auth.admin.create_user."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.created: list[dict] = []
        self.fail_with = fail_with
        self.auth = self
        self.admin = self

    def create_user(self, attributes: dict) -> _FakeUserResponse:
        if self.fail_with is not None:
            raise self.fail_with
        self.created.append(dict(attributes))
        return _FakeUserResponse(SIGNUP_USER, attributes["email"])


def test_signup_without_supabase_is_400(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    r = c.post(f"{P}/auth/signup", json={"email": "new@example.com", "password": "correct horse"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Supabase is not configured; use DEV_USER_ID locally"


def test_signup_validates_body(client):
    c, _ = client
    assert c.post(f"{P}/auth/signup", json={"email": "nope", "password": "longenough"}).status_code == 422
    assert c.post(f"{P}/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 422
    assert c.post(f"{P}/auth/signup", json={"password": "longenough"}).status_code == 422


def test_signup_creates_confirmed_user_and_profile(client, monkeypatch):
    c, store = client
    fake = FakeSupabaseAdmin()
    monkeypatch.setattr(app_module, "supabase_admin", lambda: fake)

    r = c.post(
        f"{P}/auth/signup",
        json={"email": "  New.Person@Example.com ", "password": "correct horse battery", "display_name": " Ved "},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"id": SIGNUP_USER, "email": "new.person@example.com"}

    assert len(fake.created) == 1
    sent = fake.created[0]
    assert sent["email"] == "new.person@example.com"
    assert sent["password"] == "correct horse battery"
    assert sent["email_confirm"] is True, "email_confirm must be set so no confirmation mail is needed"
    assert sent["user_metadata"] == {"display_name": "Ved"}

    profile = store.get_profile(SIGNUP_USER)
    assert profile is not None
    assert profile["display_name"] == "Ved"
    assert profile["cohort"] == "student"

    # No bearer was needed and no session was minted here: the browser signs in afterwards.
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_signup_without_display_name_leaves_profile_default(client, monkeypatch):
    c, store = client
    fake = FakeSupabaseAdmin()
    monkeypatch.setattr(app_module, "supabase_admin", lambda: fake)
    r = c.post(f"{P}/auth/signup", json={"email": "plain@example.com", "password": "correct horse"})
    assert r.status_code == 200, r.text
    assert fake.created[0]["user_metadata"] == {}
    assert store.get_profile(SIGNUP_USER)["display_name"] is None


def test_signup_duplicate_is_409(client, monkeypatch):
    c, store = client
    fake = FakeSupabaseAdmin(fail_with=Exception("A user with this email address has already been registered"))
    monkeypatch.setattr(app_module, "supabase_admin", lambda: fake)
    r = c.post(f"{P}/auth/signup", json={"email": "dupe@example.com", "password": "correct horse"})
    assert r.status_code == 409
    assert r.json()["detail"] == "account exists, sign in instead"
    assert store.get_profile(SIGNUP_USER) is None


def test_signup_other_admin_error_is_502(client, monkeypatch):
    c, _ = client
    fake = FakeSupabaseAdmin(fail_with=Exception("Database error creating new user"))
    monkeypatch.setattr(app_module, "supabase_admin", lambda: fake)
    r = c.post(f"{P}/auth/signup", json={"email": "boom@example.com", "password": "correct horse"})
    assert r.status_code == 502
    assert "Database error" in r.json()["detail"]
    assert "correct horse" not in r.text, "the password must never be echoed"
