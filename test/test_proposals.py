"""Research-first flow: POST /research stores a proposal, POST /proposals/{id}/approve turns the approved
items into a run, POST /proposals/{id}/reject closes one.

The research engine (research.engine.research, another lane's module) is replaced with a fixture proposal of
three items: one external vendor with a verified phone, one with no phone, one whose phone matches an is_demo
roster vendor. dispatch_run is a recorder. The store, the routes, the vendor upsert and the tick's simulate
path are the real modules.
"""

from __future__ import annotations

import copy
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from server import app as app_module
from server.store import SqliteStore

USER = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
P = "/api/py"
NOTE = "approved, not dialed: external calling disabled"
REQUEST = "I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist"

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
]

ISP, DENTIST, MOVERS = "isp-fiberfast-student-300", "dentist-palo-alto-smiles", "movers-pack-and-go"

PROPOSAL = {
    "summary": "Three calls cover the move: one mover quote, internet at the new address, a dentist near campus.",
    "items": [
        {
            "id": ISP,
            "category": "internet",
            "track": "negotiate",
            "recommended": True,
            "vendor": {
                "name": "FiberFast",
                "phone": "+15550100001",
                "website": "https://fiberfast.example/plans",
                "address": None,
            },
            "plan": {"name": "Student 300", "monthly_usd": 35.0, "one_time_usd": 0.0, "term": "no contract"},
            "discounts": [
                {
                    "label": "student rate",
                    "amount": "$10/mo off",
                    "eligibility": ".edu email",
                    "citations": ["https://fiberfast.example/students"],
                }
            ],
            "why": "Cheapest no-contract plan serving the new address; the student rate is on the plans page.",
            "strategy": {
                "opening": "Set up FiberFast internet at the new Palo Alto address for move-in on October 25.",
                "asks": ["student rate", "installation fee waived", "no contract"],
                "fallback": "If the student rate is refused, ask for the new-customer promotion instead.",
            },
            "regulations": ["California requires a written notice of any early-termination fee."],
            "citations": [
                {
                    "url": "https://fiberfast.example/plans",
                    "title": "Plans",
                    "fetched_at": "2026-10-11T18:00:00Z",
                }
            ],
            "phone_verified": True,
            "source": "research",
        },
        {
            "id": DENTIST,
            "category": "dentist",
            "track": "schedule",
            "recommended": False,
            "vendor": {
                "name": "Palo Alto Smiles",
                "phone": None,
                "website": "https://paloaltosmiles.example",
            },
            "plan": None,
            "discounts": [],
            "why": "Closest in-network dentist to campus, but no phone number appeared on any fetched page.",
            "strategy": {"opening": "Book the earliest new-patient cleaning.", "asks": ["student insurance"]},
            "citations": [{"url": "https://paloaltosmiles.example", "title": "Home"}],
            "phone_verified": False,
            "source": "research",
        },
        {
            "id": MOVERS,
            "category": "movers",
            "track": "quote",
            "recommended": True,
            "vendor": {"name": "Pack & Go", "phone": "+15550000001", "website": None},
            "plan": None,
            "discounts": [{"label": "student move discount", "amount": None, "citations": []}],
            "why": "Already on the roster; quoted well last time.",
            "strategy": {
                "opening": "Get an all-in quote from Pack & Go to move a 1-bedroom to Palo Alto on October 25.",
                "asks": ["student move discount if any", "no deposit above 10 percent"],
            },
            "citations": [],
            "phone_verified": True,
            "source": "roster",
        },
    ],
    "regulations": [
        {
            "topic": "early termination fees",
            "note": "California requires a written notice of any early-termination fee.",
            "citations": ["https://example.gov/etf"],
        }
    ],
    "errors": [],
    "meta": {"web_search": True, "seconds": 41.2, "model": "test", "generated_at": "2026-10-11T18:01:00Z"},
}


def _engine_module(monkeypatch) -> types.ModuleType:
    """research.engine, or a stand-in registered in sys.modules when the other lane's module is not there."""
    try:
        from research import engine  # type: ignore[import-not-found]

        return engine
    except Exception:  # noqa: BLE001  (absent or mid-write: the route must still work against a fake)
        pkg = types.ModuleType("research")
        pkg.__path__ = []  # type: ignore[attr-defined]
        engine = types.ModuleType("research.engine")
        pkg.engine = engine  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "research", pkg)
        monkeypatch.setitem(sys.modules, "research.engine", engine)
        return engine


@pytest.fixture
def fake_research(monkeypatch):
    engine = _engine_module(monkeypatch)
    rec: dict = {"calls": [], "result": copy.deepcopy(PROPOSAL)}

    async def research(profile, request, roster):
        rec["calls"].append({"profile": profile, "request": request, "roster": roster})
        result = rec["result"]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    monkeypatch.setattr(engine, "research", research, raising=False)
    return rec


class FakeDispatch:
    """Stands in for call.dispatch.dispatch_run: records the rows and marks them dialing with a fake dial id."""

    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    async def dispatch_run(self, store, run, calls, profile):
        self.batches.append([dict(c) for c in calls])
        for i, row in enumerate(calls):
            store.update_call(row["id"], {"dial_call_id": f"dc_{len(self.batches)}_{i}", "error": None})
            store.update_call_state(row["id"], "dialing", 1)
        return [store.get_call(r["id"]) for r in calls]

    @property
    def rows(self) -> list[dict]:
        return [r for batch in self.batches for r in batch]


@pytest.fixture
def client(tmp_path, monkeypatch, fake_research):
    monkeypatch.setenv("DORMAMMU_DB_PATH", str(tmp_path / "proposals.db"))
    monkeypatch.setenv("DEV_USER_ID", USER)
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("SIMULATED_TALK_SECONDS", "1")
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "5")
    monkeypatch.delenv("ALLOW_EXTERNAL_CALLS", raising=False)
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    fake = FakeDispatch()
    monkeypatch.setattr(app_module.dispatch, "dispatch_run", fake.dispatch_run)
    store = SqliteStore(tmp_path / "proposals.db")
    app_module.set_store(store)
    with TestClient(app_module.app) as c:
        assert c.post(f"{P}/vendors", json={"vendors": VENDORS}).status_code == 200
        yield c, store, fake
    app_module.set_store(None)


def _research(c) -> dict:
    r = c.post(f"{P}/research", json={"request": REQUEST})
    assert r.status_code == 200, r.text
    return r.json()


def _events(c, type_: str) -> list[dict]:
    return [e for e in c.get(f"{P}/events?limit=200").json() if e["type"] == type_]


def _by_key(run: dict) -> dict[str, dict]:
    return {call["vendor_key"]: call for call in run["calls"]}


# ---------------------------------------------------------------------------------------------
# POST /research
# ---------------------------------------------------------------------------------------------


def test_research_stores_and_returns_proposal(client, fake_research):
    c, store, _ = client
    row = _research(c)
    assert row["user_id"] == USER
    assert row["status"] == "proposed"
    assert row["request"] == REQUEST
    assert row["run_id"] is None and row["approved_item_ids"] == []
    assert row["created_at"] and row["updated_at"]
    body = row["proposal"]
    assert [it["id"] for it in body["items"]] == [ISP, DENTIST, MOVERS]
    assert body["items"][0]["vendor"]["phone"] == "+15550100001"
    assert body["regulations"] and body["meta"]["web_search"] is True and body["errors"] == []
    assert body["request"] == REQUEST

    # The engine saw the profile, the request and the roster; the roster is what makes "already on file" possible.
    assert len(fake_research["calls"]) == 1
    seen = fake_research["calls"][0]
    assert seen["profile"]["id"] == USER
    assert seen["request"] == REQUEST
    assert {v["key"] for v in seen["roster"]} == {v["key"] for v in VENDORS}

    listed = c.get(f"{P}/proposals").json()
    assert [p["id"] for p in listed] == [row["id"]]
    assert listed[0]["n_items"] == 3 and listed[0]["status"] == "proposed"
    assert "proposal" not in listed[0], "the listing never carries the body"
    assert c.get(f"{P}/proposals/{row['id']}").json() == row
    assert store.get_proposal(row["id"])["proposal"] == body

    (event,) = _events(c, "research_completed")
    data = event["data"]
    assert data["proposal_id"] == row["id"] and data["status"] == "proposed"
    assert data["items"] == 3
    assert data["phones_verified"] == 2, "the dentist has no phone"
    assert data["categories"] == 3
    assert data["regulations"] == 1
    assert data["web_search"] is True
    assert isinstance(data["seconds"], (int, float)) and data["seconds"] >= 0


def test_research_items_get_ids_when_engine_omits_them(client, fake_research):
    c, _, _ = client
    fake_research["result"] = {
        "items": [{"category": "movers", "vendor": {"name": "X", "phone": "+15550100009"}}]
    }
    row = _research(c)
    assert row["status"] == "proposed"
    assert row["proposal"]["items"][0]["id"] == "item-1"
    assert row["proposal"]["errors"] == [] and row["proposal"]["regulations"] == []


def test_research_failure_is_a_failed_proposal(client, fake_research):
    c, _, _ = client
    fake_research["result"] = {"items": [], "errors": ["web search unavailable", "no provider pages fetched"]}
    row = _research(c)
    assert row["status"] == "failed"
    assert row["proposal"]["errors"] == ["web search unavailable", "no provider pages fetched"]
    (event,) = _events(c, "research_completed")
    assert (
        event["data"]["status"] == "failed" and event["data"]["items"] == 0 and event["data"]["errors"] == 2
    )

    fake_research["result"] = RuntimeError("engine exploded")
    row = _research(c)
    assert row["status"] == "failed"
    assert row["proposal"]["errors"] == ["RuntimeError: engine exploded"]

    # Approving a failed proposal is refused; it never became a plan.
    assert c.post(f"{P}/proposals/{row['id']}/approve", json={"item_ids": ["x"]}).status_code == 409


def test_research_without_engine_is_503(client, monkeypatch):
    c, _, _ = client
    monkeypatch.setitem(sys.modules, "research", None)
    monkeypatch.setitem(sys.modules, "research.engine", None)
    r = c.post(f"{P}/research", json={"request": REQUEST})
    assert r.status_code == 503
    assert r.json()["detail"].startswith("research engine unavailable")
    assert c.get(f"{P}/proposals").json() == []


def test_research_validates_body(client):
    c, _, _ = client
    assert c.post(f"{P}/research", json={}).status_code == 422
    assert c.post(f"{P}/research", json={"request": "   "}).status_code == 400


# ---------------------------------------------------------------------------------------------
# POST /proposals/{id}/approve
# ---------------------------------------------------------------------------------------------


def test_approve_without_external_calls(client, fake_research, monkeypatch):
    c, _store, fake = client
    proposal = _research(c)

    r = c.post(f"{P}/proposals/{proposal['id']}/approve", json={"item_ids": [ISP, DENTIST, MOVERS]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["external_calls_enabled"] is False
    assert out["skipped"] == [{"item_id": DENTIST, "reason": "no verified phone"}]
    run = out["run"]
    assert run["request"] == REQUEST
    assert run["status"] == "running"
    assert "aggregate" in run and "server_now" in run
    assert len(run["calls"]) == 2
    calls = _by_key(run)

    # The external vendor: a research vendors row, and a labeled simulated row with the exact note.
    external = next(call for key, call in calls.items() if key.startswith("r_"))
    assert external["vendor_key"] == "r_internet-fiberfast"
    assert external["target_name"] == "FiberFast"
    assert external["target_phone"] == "+15550100001"
    assert external["track"] == "negotiate"
    assert external["simulated"] is True
    assert external["error"] == NOTE
    assert external["state"] == "dialing" and external["state_rank"] == 1
    assert external["dial_call_id"] is None
    opening = PROPOSAL["items"][0]["strategy"]["opening"]
    assert external["objective"].startswith(opening)
    assert PROPOSAL["items"][0]["why"] in external["objective"]
    assert external["objective"].endswith("Ask for: student rate, installation fee waived, no contract.")
    assert len(external["objective"]) < 600

    # The roster vendor (matched by phone): reused, not duplicated, and handed to dispatch_run.
    roster = calls["movers_a"]
    assert roster["target_name"] == "Pack & Go" and roster["track"] == "quote"
    assert roster["simulated"] is False
    assert roster["dial_call_id"] == "dc_1_0" and roster["state"] == "dialing"
    assert roster["objective"].startswith(PROPOSAL["items"][2]["strategy"]["opening"])
    assert [r["id"] for r in fake.rows] == [roster["id"]]
    assert fake.rows[0]["voice_gender"] in ("female", "male")

    vendors = {v["key"]: v for v in c.get(f"{P}/vendors").json()}
    assert len(vendors) == len(VENDORS) + 1
    research_vendor = vendors["r_internet-fiberfast"]
    assert research_vendor["source"] == "research"
    assert research_vendor["website"] == "https://fiberfast.example/plans"
    assert research_vendor["is_demo"] is False and research_vendor["phone"] == "+15550100001"
    assert research_vendor["id"] == external["vendor_id"]
    assert vendors["movers_a"]["source"] == "manual" and vendors["movers_a"]["id"] == roster["vendor_id"]

    (approved,) = _events(c, "proposal_approved")
    assert approved["run_id"] == run["id"]
    assert approved["data"]["proposal_id"] == proposal["id"]
    assert approved["data"]["approved"] == 2
    assert approved["data"]["skipped"] == out["skipped"]
    assert approved["data"]["dialed"] == 1
    assert approved["data"]["simulated"] == 1
    (requested,) = _events(c, "run_requested")
    assert requested["data"]["kind"] == "proposal" and requested["data"]["n_calls"] == 2
    (simulated,) = _events(c, "call_simulated")
    assert simulated["call_id"] == external["id"] and simulated["data"]["reason"] == NOTE
    dispatched = _events(c, "call_dispatched")
    assert [e["call_id"] for e in dispatched] == [external["id"]], "the fake dispatch logs nothing"

    updated = c.get(f"{P}/proposals/{proposal['id']}").json()
    assert updated["status"] == "dispatched"
    assert updated["run_id"] == run["id"]
    assert updated["approved_item_ids"] == [ISP, MOVERS]
    assert updated["updated_at"] >= proposal["updated_at"]
    assert out["proposal"] == updated
    assert c.get(f"{P}/proposals").json()[0]["status"] == "dispatched"
    assert [r["id"] for r in c.get(f"{P}/runs").json()] == [run["id"]]

    # Approving twice does not dispatch twice.
    again = c.post(f"{P}/proposals/{proposal['id']}/approve", json={"item_ids": [ISP]})
    assert again.status_code == 409
    assert len(c.get(f"{P}/runs").json()) == 1

    # The tick's simulate path drives the held row to a labeled fixture outcome; the dialed row waits on Dial.
    base = datetime.fromisoformat(external["started_at"])
    base = base if base.tzinfo else base.replace(tzinfo=UTC)
    monkeypatch.setattr(app_module, "utcnow", lambda: base + timedelta(seconds=600))
    ticked = c.post(f"{P}/runs/{run['id']}/tick")
    assert ticked.status_code == 200, ticked.text
    after = _by_key(ticked.json())
    done = after["r_internet-fiberfast"]
    assert done["state"] == "done" and done["simulated"] is True
    assert done["outcome"]["track"] == "negotiate" and "simulated" in done["outcome"]["note"]
    assert done["transcript"]
    assert after["movers_a"]["state"] == "dialing"


def test_approve_with_external_calls_enabled(client, monkeypatch):
    c, _, fake = client
    monkeypatch.setenv("ALLOW_EXTERNAL_CALLS", "1")
    proposal = _research(c)
    r = c.post(f"{P}/proposals/{proposal['id']}/approve", json={"item_ids": [ISP, MOVERS, DENTIST]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["external_calls_enabled"] is True
    assert out["skipped"] == [{"item_id": DENTIST, "reason": "no verified phone"}]
    calls = _by_key(out["run"])
    assert set(calls) == {"r_internet-fiberfast", "movers_a"}
    assert all(call["simulated"] is False for call in calls.values())
    assert all(call["error"] is None for call in calls.values())
    assert {call["dial_call_id"] for call in calls.values()} == {"dc_1_0", "dc_1_1"}
    assert len(fake.batches) == 1 and {r["vendor_key"] for r in fake.rows} == set(calls)
    assert {r["voice_gender"] for r in fake.rows} == {"female", "male"}
    vendors = {v["key"]: v for v in c.get(f"{P}/vendors").json()}
    assert vendors["r_internet-fiberfast"]["source"] == "research"
    (approved,) = _events(c, "proposal_approved")
    assert approved["data"]["dialed"] == 2 and approved["data"]["simulated"] == 0
    assert _events(c, "call_simulated") == []


@pytest.mark.parametrize("value", ["true", "yes", "TRUE"])
def test_external_calls_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ALLOW_EXTERNAL_CALLS", value)
    assert app_module.external_calls_enabled() is True
    monkeypatch.setenv("ALLOW_EXTERNAL_CALLS", "0")
    assert app_module.external_calls_enabled() is False
    monkeypatch.delenv("ALLOW_EXTERNAL_CALLS")
    assert app_module.external_calls_enabled() is False


def test_approve_subset_and_skips(client, fake_research):
    c, _, fake = client
    proposal = _research(c)
    r = c.post(
        f"{P}/proposals/{proposal['id']}/approve",
        json={"item_ids": [MOVERS, "ghost", MOVERS, DENTIST]},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert [c_["vendor_key"] for c_ in out["run"]["calls"]] == ["movers_a"]
    assert out["skipped"] == [
        {"item_id": "ghost", "reason": "not in this proposal"},
        {"item_id": DENTIST, "reason": "no verified phone"},
    ]
    assert len(fake.rows) == 1
    assert c.get(f"{P}/proposals/{proposal['id']}").json()["approved_item_ids"] == [MOVERS]
    assert len(c.get(f"{P}/vendors").json()) == len(VENDORS), "no research vendor row for a roster match"

    # Nothing dialable: no run is created and the proposal stays proposed.
    second = _research(c)
    r = c.post(f"{P}/proposals/{second['id']}/approve", json={"item_ids": [DENTIST, "nope"]})
    assert r.status_code == 400
    assert "no verified phone" in r.json()["detail"]
    assert c.get(f"{P}/proposals/{second['id']}").json()["status"] == "proposed"
    assert len(c.get(f"{P}/runs").json()) == 1
    assert c.post(f"{P}/proposals/{second['id']}/approve", json={"item_ids": []}).status_code == 422


def test_approve_dedupes_same_vendor_and_respects_cap(client, fake_research, monkeypatch):
    c, _, _ = client
    items = copy.deepcopy(PROPOSAL["items"])
    twin = copy.deepcopy(items[0])
    twin["id"] = "isp-fiberfast-gig"
    twin["plan"] = {"name": "Gig", "monthly_usd": 60.0}
    extra = {
        "id": "movers-bay-haulers",
        "category": "movers",
        "track": "quote",
        "vendor": {"name": "Bay Haulers", "phone": "+15550000002"},
        "why": "Second quote.",
        "strategy": {"opening": "Get a quote from Bay Haulers.", "asks": []},
    }
    fake_research["result"] = {**PROPOSAL, "items": [*items, twin, extra]}
    monkeypatch.setenv("MAX_CALLS_PER_RUN", "2")
    proposal = _research(c)
    r = c.post(
        f"{P}/proposals/{proposal['id']}/approve",
        json={"item_ids": [ISP, "isp-fiberfast-gig", MOVERS, "movers-bay-haulers"]},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(_by_key(out["run"])) == {"r_internet-fiberfast", "movers_a"}
    assert out["skipped"] == [
        {"item_id": "isp-fiberfast-gig", "reason": f"same vendor as {ISP}"},
        {"item_id": "movers-bay-haulers", "reason": "over the MAX_CALLS_PER_RUN cap of 2"},
    ]


def test_objective_stays_under_600_chars():
    item = {
        "why": "because " * 200,
        "strategy": {"opening": "Open.", "asks": ["a", "b"]},
    }
    text = app_module.item_objective(item, "fallback")
    assert len(text) < 600
    assert text.startswith("Open. because")
    assert text.endswith("Ask for: a, b.")
    assert app_module.item_objective({}, "the request") == "the request"
    assert app_module.item_objective({"strategy": {"opening": "x" * 700}}, "f").endswith("...")


def test_research_vendor_key_and_phone_match():
    assert app_module.research_vendor_key("internet", "FiberFast") == "r_internet-fiberfast"
    assert app_module.research_vendor_key("Movers", "Pack & Go, Inc.") == "r_movers-pack-go-inc"
    assert app_module.research_vendor_key(None, None) == "r_vendor"
    long = app_module.research_vendor_key(
        "dentist", "A Very Long Dental Practice Name In Palo Alto California"
    )
    assert len(long) <= 40 and long.startswith("r_dentist-")
    assert app_module.same_phone("+15550000001", "(555) 000-0001")
    assert app_module.same_phone("5550000001", "+1 555 000 0001")
    assert not app_module.same_phone("+15550000001", "+15550000002")
    assert not app_module.same_phone(None, "+15550000001")


# ---------------------------------------------------------------------------------------------
# Reject, isolation
# ---------------------------------------------------------------------------------------------


def test_reject(client):
    c, _, fake = client
    proposal = _research(c)
    r = c.post(f"{P}/proposals/{proposal['id']}/reject")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "rejected" and r.json()["id"] == proposal["id"]
    assert c.get(f"{P}/proposals").json()[0]["status"] == "rejected"
    assert c.post(f"{P}/proposals/{proposal['id']}/approve", json={"item_ids": [ISP]}).status_code == 409
    assert fake.rows == [] and c.get(f"{P}/runs").json() == []
    (event,) = _events(c, "proposal_rejected")
    assert event["data"]["proposal_id"] == proposal["id"]
    # Rejecting twice is idempotent; rejecting a dispatched proposal is refused.
    assert c.post(f"{P}/proposals/{proposal['id']}/reject").status_code == 200
    second = _research(c)
    assert c.post(f"{P}/proposals/{second['id']}/approve", json={"item_ids": [MOVERS]}).status_code == 200
    assert c.post(f"{P}/proposals/{second['id']}/reject").status_code == 409


def test_other_users_proposal_is_404(client, monkeypatch):
    c, _, _ = client
    proposal = _research(c)
    monkeypatch.setenv("DEV_USER_ID", OTHER)
    assert c.get(f"{P}/proposals").json() == []
    assert c.get(f"{P}/proposals/{proposal['id']}").status_code == 404
    assert c.post(f"{P}/proposals/{proposal['id']}/approve", json={"item_ids": [ISP]}).status_code == 404
    assert c.post(f"{P}/proposals/{proposal['id']}/reject").status_code == 404
    assert c.get(f"{P}/proposals/00000000-0000-4000-8000-00000000dead").status_code == 404
    monkeypatch.setenv("DEV_USER_ID", USER)
    assert c.get(f"{P}/proposals/{proposal['id']}").json()["status"] == "proposed"
