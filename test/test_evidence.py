"""Past calls, evidence and accuracy routes: FastAPI TestClient against SqliteStore.

Rows go in straight through the store (no Dial, no LLM), so the numbers asserted here are exact. Only
dial_client.get_account / list_numbers are faked, for the Dial block of /evidence. Full phone numbers and
the Keewano token must never appear in any response body.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from call import dial_client
from call.dial_client import DialError
from server import app as app_module
from server.accuracy import (
    accuracy_report,
    amount_variants,
    keewano_token_kind,
    mask_phone,
    recap_agrees,
    recap_line,
    slot_day_variants,
)
from server.store import SqliteStore

USER = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
P = "/api/py"

FROM_NUMBER = "+16505550199"
DIAL_KEY = "dial-test-key-do-not-leak"
VENDORS = [
    {"key": "movers_a", "name": "Pack & Go", "track": "quote", "phone": "+14155550167", "is_demo": True},
    {"key": "movers_b", "name": "Bay Haulers", "track": "quote", "phone": "+14155550168", "is_demo": True},
    {
        "key": "dentist_a",
        "name": "Smile Dental",
        "track": "schedule",
        "phone": "+14155550169",
        "is_demo": True,
    },
    {"key": "isp_a", "name": "Fiber Co", "track": "negotiate", "phone": "+14155550170", "is_demo": True},
]
FULL_PHONES = [v["phone"] for v in VENDORS] + [FROM_NUMBER, "+14155550999"]

FAKE_ACCOUNT = {
    "id": "acct_1",
    "email": "owner@example.com",
    "role": "owner",
    "maxCallDurationSeconds": None,
    "limits": {"maxCallDurationSeconds": 300, "maxConcurrentCalls": 2},
    "apiKeys": [{"id": "k1", "label": "default", "preview": "dk_1234", "createdAt": "2026-10-11T00:00:00Z"}],
}
FAKE_NUMBERS = [
    {
        "id": "pn_1",
        "number": FROM_NUMBER,
        "nickname": "Dormammu line",
        "country": "US",
        "capabilities": ["sms", "call"],
        "callingEnabled": True,
        "setupStatus": "ready",
    }
]


def _turns(*pairs: tuple[str, str]) -> list[dict]:
    return [{"speaker": s, "text": t} for s, t in pairs]


def _text(turns: list[dict]) -> str:
    return "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)


OPENER = "Hi, this is Alex, am I speaking with {name}?"
AI_LINE = "I'm an AI assistant calling for a client, {ask}."

QUOTE_TURNS = _turns(
    ("agent", OPENER.format(name="Pack & Go")),
    ("user", "Yes, this is Pack & Go."),
    ("agent", AI_LINE.format(ask="they need a quote for a two-bedroom move on November first")),
    ("user", "For a two-bedroom on that date we would be at $1,450 flat. That excludes packing."),
    (
        "agent",
        "Just to confirm, Pack & Go is at $1,450 for the November first move, excluding packing. Thanks.",
    ),
)
QUOTE_NO_AMOUNT_TURNS = _turns(
    ("agent", OPENER.format(name="Bay Haulers")),
    ("user", "Speaking."),
    ("agent", AI_LINE.format(ask="they need a moving quote")),
    ("user", "I can't quote over the phone, I'll email it tonight."),
    ("agent", "Just to confirm, you'll email the quote tonight. Thanks, bye."),
)
NEGOTIATE_TURNS = _turns(
    ("agent", OPENER.format(name="Fiber Co")),
    ("user", "Yes."),
    ("agent", AI_LINE.format(ask="they want to lower their monthly internet bill")),
    ("user", "I can apply a loyalty discount of $20 per month starting November first."),
    ("agent", "Just to confirm, Fiber Co is taking $25 a month off starting November 1, no contract change."),
)
LONG_EVENT = {"request": "x" * 400, "n_calls": 4}


def seed(store: SqliteStore) -> dict:
    """Four calls for USER across two runs plus one call for OTHER. Returns ids by role."""
    store.upsert_profile(USER, {"display_name": "Ved"})
    vendors = {v["key"]: v for v in store.upsert_vendors(USER, VENDORS)}
    run_a = store.create_run(USER, "Moving to SF on Nov 1, get me quotes and book the dentist")
    run_b = store.create_run(USER, "Cut the internet bill")

    def base(run: dict, key: str, **extra) -> dict:
        vendor = vendors[key]
        row = {
            "run_id": run["id"],
            "vendor_id": vendor["id"],
            "vendor_key": key,
            "track": vendor["track"],
            "target_name": vendor["name"],
            "target_phone": vendor["phone"],
            "objective": "demo",
            "prior_context": None,
            "simulated": False,
        }
        row.update(extra)
        return row

    rows = store.insert_calls(
        [
            base(
                run_a,
                "movers_a",
                dial_call_id="dc_001",
                state="done",
                state_rank=6,
                termination_type="completed",
                duration_seconds=42.0,
                started_at="2026-10-11T18:00:00+00:00",
                ended_at="2026-10-11T18:01:00+00:00",
                transcript=_text(QUOTE_TURNS),
                transcript_turns=QUOTE_TURNS,
                outcome={
                    "track": "quote",
                    "amount_usd": 1450.0,
                    "unit": "flat",
                    "caveats": ["excludes packing"],
                    "note": None,
                },
            ),
            base(
                run_a,
                "movers_b",
                simulated=True,
                state="done",
                state_rank=6,
                termination_type="completed",
                duration_seconds=60.0,
                started_at="2026-10-11T18:01:00+00:00",
                ended_at="2026-10-11T18:02:30+00:00",
                transcript=_text(QUOTE_NO_AMOUNT_TURNS),
                transcript_turns=QUOTE_NO_AMOUNT_TURNS,
                outcome={
                    "track": "quote",
                    "amount_usd": None,
                    "unit": None,
                    "caveats": [],
                    "note": "simulated: vendor will email the quote",
                },
            ),
            base(
                run_a,
                "dentist_a",
                dial_call_id="dc_002",
                state="failed",
                state_rank=6,
                termination_type="voicemail",
                duration_seconds=8.0,
                started_at="2026-10-11T18:02:00+00:00",
                ended_at="2026-10-11T18:02:20+00:00",
            ),
            base(
                run_b,
                "isp_a",
                simulated=True,
                state="done",
                state_rank=6,
                termination_type="completed",
                duration_seconds=75.0,
                started_at="2026-10-11T18:03:00+00:00",
                ended_at="2026-10-11T18:04:30+00:00",
                transcript=_text(NEGOTIATE_TURNS),
                transcript_turns=NEGOTIATE_TURNS,
                outcome={
                    "track": "negotiate",
                    "action": "reduced",
                    "saved_usd": 20.0,
                    "effective_date": "2026-11-01",
                    "note": None,
                },
            ),
        ]
    )
    quote_real, quote_sim, voicemail, negotiate = rows
    store.insert_event(USER, "run_requested", LONG_EVENT, run_id=run_a["id"])
    for row in rows:
        store.insert_event(
            USER,
            "call_dispatched",
            {"vendor_key": row["vendor_key"]},
            run_id=row["run_id"],
            call_id=row["id"],
        )
    for row in (quote_real, quote_sim, negotiate):
        store.insert_event(
            USER,
            "outcome_extracted",
            {"track": row["track"], "outcome": row["outcome"], "simulated": row["simulated"]},
            run_id=row["run_id"],
            call_id=row["id"],
        )

    store.upsert_profile(OTHER, {"display_name": "Someone else"})
    (other_vendor,) = store.upsert_vendors(
        OTHER, [{"key": "secret_a", "name": "Other Co", "track": "quote", "phone": "+14155550999"}]
    )
    other_run = store.create_run(OTHER, "not yours")
    store.insert_calls(
        [
            {
                "run_id": other_run["id"],
                "vendor_id": other_vendor["id"],
                "vendor_key": "secret_a",
                "track": "quote",
                "target_name": "Other Co",
                "target_phone": "+14155550999",
                "objective": "x",
                "dial_call_id": "dc_other",
                "state": "done",
                "state_rank": 6,
            }
        ]
    )
    store.insert_event(OTHER, "run_requested", {}, run_id=other_run["id"])
    return {
        "quote_real": quote_real["id"],
        "quote_sim": quote_sim["id"],
        "voicemail": voicemail["id"],
        "negotiate": negotiate["id"],
        "run_a": run_a["id"],
        "run_b": run_b["id"],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DORMAMMU_DB_PATH", str(tmp_path / "evidence.db"))
    monkeypatch.setenv("DEV_USER_ID", USER)
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("DIAL_API_KEY", DIAL_KEY)
    monkeypatch.setenv("DIAL_NUMBER", FROM_NUMBER)
    monkeypatch.delenv("KEEWANO_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL_GIT_COMMIT_SHA", raising=False)

    async def fake_account() -> dict:
        return json.loads(json.dumps(FAKE_ACCOUNT))

    async def fake_numbers() -> list[dict]:
        return json.loads(json.dumps(FAKE_NUMBERS))

    monkeypatch.setattr(dial_client, "get_account", fake_account)
    monkeypatch.setattr(dial_client, "list_numbers", fake_numbers)
    monkeypatch.setattr(app_module, "EVAL_REPORT_PATH", tmp_path / "eval-report.json")

    store = SqliteStore(tmp_path / "evidence.db")
    app_module.set_store(store)
    with TestClient(app_module.app) as c:
        yield c, store
    app_module.set_store(None)


def _assert_no_leak(text: str) -> None:
    for phone in FULL_PHONES:
        assert phone not in text, f"full phone {phone[-4:]} leaked"
        assert phone.lstrip("+") not in text
    assert DIAL_KEY not in text


# ---------------------------------------------------------------------------------------------
# GET /calls
# ---------------------------------------------------------------------------------------------


def test_calls_route_masks_phones_and_pages(client):
    c, store = client
    ids = seed(store)
    r = c.get(f"{P}/calls")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    rows = r.json()
    assert [row["id"] for row in rows] == [
        ids["negotiate"],
        ids["voicemail"],
        ids["quote_sim"],
        ids["quote_real"],
    ]
    assert rows[0]["request"] == "Cut the internet bill"
    assert rows[-1]["request"].startswith("Moving to SF")
    assert rows[-1]["target_phone"] == "+1 (415) ***-**67"
    assert rows[0]["target_phone"] == "+1 (415) ***-**70"
    assert rows[-1]["transcript_turns"] == QUOTE_TURNS
    assert rows[-1]["outcome"]["amount_usd"] == 1450.0
    assert rows[2]["simulated"] is True and rows[-1]["simulated"] is False
    _assert_no_leak(r.text)
    assert "dc_other" not in r.text and "Other Co" not in r.text

    page = c.get(f"{P}/calls?limit=2&offset=2").json()
    assert [row["id"] for row in page] == [ids["quote_sim"], ids["quote_real"]]
    assert len(c.get(f"{P}/calls?limit=0").json()) == 1, "limit is clamped to at least 1"
    assert c.get(f"{P}/calls?offset=99").json() == []


# ---------------------------------------------------------------------------------------------
# GET /evidence
# ---------------------------------------------------------------------------------------------


def test_evidence_shape_dial_events_and_counts(client):
    c, store = client
    ids = seed(store)
    r = c.get(f"{P}/evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"dial", "keewano", "events", "calls", "policy", "build", "generated_at"}
    _assert_no_leak(r.text)

    dial = body["dial"]
    account = dial["account"]
    assert account["configured"] is True and account["error"] is None
    assert account["limits"] == {"maxCallDurationSeconds": 300, "maxConcurrentCalls": 2}
    assert account["from_number"] == "+1 (650) ***-**99"
    assert account["api_keys"] == 1
    assert account["numbers"] == [
        {
            "number": "+1 (650) ***-**99",
            "nickname": "Dormammu line",
            "country": "US",
            "setup_status": "ready",
            "calling_enabled": True,
        }
    ]
    assert "owner@example.com" not in r.text, "account email is not evidence"
    assert [row["id"] for row in dial["real_calls"]] == [ids["voicemail"], ids["quote_real"]]
    assert [row["dial_call_id"] for row in dial["real_calls"]] == ["dc_002", "dc_001"]
    assert dial["real_calls"][1]["target_phone"] == "+1 (415) ***-**67"
    assert dial["real_calls"][1]["termination_type"] == "completed"
    assert dial["real_calls"][1]["duration_seconds"] == 42.0
    assert dial["n_real_calls"] == 2 and dial["n_simulated_calls"] == 2 and dial["n_undialed_calls"] == 0

    events = body["events"]
    assert events["by_type"] == {"call_dispatched": 4, "outcome_extracted": 3, "run_requested": 1}
    assert len(events["recent"]) == 8
    assert events["recent"][0]["type"] == "outcome_extracted"
    assert all(isinstance(e["data"], str) and len(e["data"]) <= 300 for e in events["recent"])
    long_one = next(e for e in events["recent"] if e["type"] == "run_requested")
    assert long_one["truncated"] is True and long_one["run_id"] == ids["run_a"]
    assert all(e["truncated"] is False for e in events["recent"] if e["type"] != "run_requested")
    assert {e["call_id"] for e in events["recent"] if e["type"] == "call_dispatched"} == {
        ids["quote_real"],
        ids["quote_sim"],
        ids["voicemail"],
        ids["negotiate"],
    }

    assert body["calls"]["by_state"] == {"done": 3, "failed": 1}
    assert body["calls"]["by_simulated"] == {"real_dial": 2, "simulated": 2, "undialed": 0}

    keewano = body["keewano"]
    assert keewano["token_kind"] == "missing" and keewano["enabled"] is False
    assert "event_types" in keewano and "status" in keewano and "last_error" in keewano
    if keewano["event_types"] is not None:
        assert keewano["event_types"]["run_requested"] == 2001

    policy = body["policy"]
    assert policy["multiplier_label"] == "est." and policy["only_vendor_rows_dialable"] is True
    assert policy["ai_disclosure"] and "AI assistant" in policy["ai_disclosure"]
    assert policy["summary_closer"] and "Just to confirm" in policy["summary_closer"]

    build = body["build"]
    assert build["python"].startswith("3.") and build["store"] == "sqlite" and build["deployed_at"] is None
    assert build["commit"] is None or (isinstance(build["commit"], str) and 7 <= len(build["commit"]) <= 40)
    assert body["generated_at"]


def test_evidence_captures_dial_errors(client, monkeypatch):
    c, store = client
    seed(store)

    async def broken_account() -> dict:
        raise DialError(401, "unauthorized", "Invalid API key")

    monkeypatch.setattr(dial_client, "get_account", broken_account)
    r = c.get(f"{P}/evidence")
    assert r.status_code == 200
    account = r.json()["dial"]["account"]
    assert account["limits"] is None
    assert account["error"].startswith("account: DialError")
    assert account["numbers"] and account["from_number"] == "+1 (650) ***-**99"
    _assert_no_leak(r.text)

    monkeypatch.delenv("DIAL_API_KEY")
    account = c.get(f"{P}/evidence").json()["dial"]["account"]
    assert account["configured"] is False and account["error"] == "DIAL_API_KEY is not set"


def test_evidence_keewano_token_kind(client, monkeypatch):
    c, store = client
    seed(store)
    project_key = "eyJhbGciOiJSUzI1NiJ9.eyJwcm9qZWN0SWQiOiJ0ZXN0In0.signature-part-never-shown"
    personal = "kwpat-0123456789abcdef-never-shown"

    monkeypatch.setenv("KEEWANO_TOKEN", project_key)
    r = c.get(f"{P}/evidence")
    kw = r.json()["keewano"]
    assert kw["token_kind"] == "project_key"
    assert kw["auth_hint"] is None
    assert project_key not in r.text and "signature-part" not in r.text
    assert kw["enabled"] is False, "KEEWANO_ENABLED=false wins even with a project key"

    monkeypatch.setenv("KEEWANO_ENABLED", "true")
    kw = c.get(f"{P}/evidence").json()["keewano"]
    assert kw["enabled"] is True
    assert kw["status"] is None or isinstance(kw["status"], dict)
    if isinstance(kw["status"], dict):
        assert kw["status"].get("token_present") is True
        assert not any("token" in k and isinstance(v, str) for k, v in kw["status"].items())

    monkeypatch.setenv("KEEWANO_TOKEN", personal)
    r = c.get(f"{P}/evidence")
    kw = r.json()["keewano"]
    assert kw["token_kind"] == "personal_token"
    assert kw["auth_hint"] and "Project API Key" in kw["auth_hint"]
    assert personal not in r.text and "0123456789abcdef" not in r.text

    monkeypatch.delenv("KEEWANO_TOKEN")
    kw = c.get(f"{P}/evidence").json()["keewano"]
    assert kw["token_kind"] == "missing" and kw["enabled"] is False


# ---------------------------------------------------------------------------------------------
# GET /accuracy
# ---------------------------------------------------------------------------------------------


def test_accuracy_numbers_on_known_set(client):
    c, store = client
    ids = seed(store)
    r = c.get(f"{P}/accuracy")
    assert r.status_code == 200, r.text
    body = r.json()
    _assert_no_leak(r.text)

    assert body["tracks"]["quote"] == {"n_done": 2, "n_with_value": 1, "success_rate": 0.5}
    assert body["tracks"]["negotiate"] == {"n_done": 1, "n_with_value": 1, "success_rate": 1.0}
    assert body["tracks"]["schedule"] == {"n_done": 0, "n_with_value": 0, "success_rate": None}

    overall = body["overall"]
    assert overall["n_calls"] == 4 and overall["n_done"] == 3 and overall["n_failed"] == 1
    assert overall["n_terminal"] == 4 and overall["n_simulated"] == 2
    assert overall["answer_rate"] == 0.75
    assert overall["voicemail_rate"] == 0.25
    assert overall["no_answer_rate"] == 0.0
    assert overall["avg_duration_seconds"] == 59.0
    assert overall["extraction_coverage"] == round(2 / 3, 4)

    recap = body["recap_agreement"]
    assert recap["checked"] == 2 and recap["agreed"] == 1 and recap["rate"] == 0.5
    assert recap["disagreed_call_ids"] == [ids["negotiate"]], "recap said $25, extractor said $20"
    assert "Just to confirm" in recap["definition"]

    assert body["split"]["real"]["overall"]["n_calls"] == 2
    assert (
        body["split"]["real"]["overall"]["n_done"] == 1 and body["split"]["real"]["overall"]["n_failed"] == 1
    )
    assert body["split"]["real"]["recap_agreement"] == {
        "checked": 1,
        "agreed": 1,
        "rate": 1.0,
        "disagreed_call_ids": [],
    }
    assert body["split"]["simulated"]["overall"]["n_calls"] == 2
    assert body["split"]["simulated"]["tracks"]["quote"]["n_with_value"] == 0
    assert body["eval"] is None
    assert "simulated" in body["note"] and body["cap"] == 500 and body["generated_at"]


def test_accuracy_reads_eval_report_when_present(client, tmp_path):
    c, store = client
    seed(store)
    report = {"n_cases": 12, "exact_match": 0.92, "generated_at": "2026-10-11T22:00:00+00:00"}
    (tmp_path / "eval-report.json").write_text(json.dumps(report))
    assert c.get(f"{P}/accuracy").json()["eval"] == report
    (tmp_path / "eval-report.json").write_text("{not json")
    assert c.get(f"{P}/accuracy").json()["eval"] is None


def test_accuracy_with_no_calls(client):
    c, store = client
    store.upsert_profile(USER, {})
    body = c.get(f"{P}/accuracy").json()
    assert body["overall"]["n_calls"] == 0 and body["overall"]["answer_rate"] is None
    assert body["recap_agreement"]["rate"] is None
    assert body["tracks"]["quote"]["success_rate"] is None


# ---------------------------------------------------------------------------------------------
# Pure helpers (server/accuracy.py)
# ---------------------------------------------------------------------------------------------


def test_mask_phone_forms():
    assert mask_phone("+14155550167") == "+1 (415) ***-**67"
    assert mask_phone("(415) 555-0167") == "(415) ***-**67"
    assert mask_phone("+442071234567") == "+44 ***-**67"
    assert mask_phone("555-0167") == "***-**67"
    assert mask_phone("12") == "***"
    assert mask_phone(None) is None and mask_phone("") is None and mask_phone("abc") is None


def test_recap_line_picks_last_agent_confirm():
    turns = _turns(
        ("agent", "Just to confirm, you said Tuesday?"),
        ("user", "Yes."),
        ("agent", "Perfect. Just to confirm, Tuesday October 20 at 2 PM. Thanks, bye."),
        ("user", "Bye."),
    )
    assert (
        recap_line({"transcript_turns": turns})
        == "Perfect. Just to confirm, Tuesday October 20 at 2 PM. Thanks, bye."
    )
    assert (
        recap_line({"transcript": _text(turns)})
        == "Perfect. Just to confirm, Tuesday October 20 at 2 PM. Thanks, bye."
    )
    assert (
        recap_line({"transcript": "agent: Summary: fourteen fifty flat.\nuser: ok"})
        == "Summary: fourteen fifty flat."
    )
    assert recap_line({"transcript": "user: Just to confirm, you are a robot?\nagent: Yes, bye."}) is None
    assert (
        recap_line({"transcript": "Hello there.\nJust to confirm, $20 off."}) == "Just to confirm, $20 off."
    )
    assert recap_line({"transcript": None, "transcript_turns": None}) is None
    assert recap_line({"transcript_turns": json.dumps(turns)}) is not None


def test_amount_and_day_variants():
    words = amount_variants(1450)
    assert {"1450", "1,450", "1450.00", "fourteen fifty", "one thousand four hundred fifty"} <= words
    assert "twelve hundred" in amount_variants(1200.0)
    assert {"89.99", "eighty nine ninety nine"} <= amount_variants(89.99)
    assert "twenty" in amount_variants(20)
    assert amount_variants(None) == set() and amount_variants("abc") == set()
    days = slot_day_variants("2026-10-20T14:00:00-07:00")
    assert {"october 20", "oct 20", "10/20", "2026-10-20", "the 20th", "october 20th"} <= days
    assert "march 3rd" in slot_day_variants("2027-03-03T09:00:00Z")
    assert slot_day_variants("not a date") == set() and slot_day_variants(None) == set()


def _done(track: str, outcome: dict, recap: str | None) -> dict:
    turns = _turns(("agent", "Hi, this is Alex."), ("user", "Hi."))
    if recap is not None:
        turns.append({"speaker": "agent", "text": recap})
    return {"state": "done", "track": track, "outcome": outcome, "transcript_turns": turns}


def test_recap_agrees_cases():
    quote = {"track": "quote", "amount_usd": 1450.0, "unit": "flat", "caveats": [], "note": None}
    assert recap_agrees(_done("quote", quote, "Just to confirm, fourteen fifty flat, bye.")) is True
    assert recap_agrees(_done("quote", quote, "Just to confirm, $1,450 flat.")) is True
    assert (
        recap_agrees(_done("quote", quote, "Just to confirm, one thousand four hundred and fifty.")) is True
    )
    assert recap_agrees(_done("quote", quote, "Just to confirm, $1,500 flat.")) is False
    assert recap_agrees(_done("quote", quote, None)) is None
    assert recap_agrees(_done("quote", {**quote, "amount_usd": None}, "Just to confirm, no quote.")) is None
    assert recap_agrees({**_done("quote", quote, "Just to confirm, $1,450."), "state": "failed"}) is None

    sched = {"track": "schedule", "slot_iso": "2026-10-20T14:00:00-07:00", "confirmed": True, "note": None}
    assert recap_agrees(_done("schedule", sched, "Just to confirm, Tuesday October 20 at 2:00 PM.")) is True
    assert recap_agrees(_done("schedule", sched, "Just to confirm, the 20th at two.")) is True
    assert recap_agrees(_done("schedule", sched, "Just to confirm, October 21 at 2 PM.")) is False

    nego = {
        "track": "negotiate",
        "action": "reduced",
        "saved_usd": 20.0,
        "effective_date": None,
        "note": None,
    }
    assert recap_agrees(_done("negotiate", nego, "Summary: twenty dollars a month off.")) is True
    assert recap_agrees(_done("negotiate", nego, "Just to confirm, $25 a month off.")) is False
    # A JSON-string outcome (raw Postgres text) is read the same way.
    assert recap_agrees(_done("negotiate", json.dumps(nego), "Just to confirm, $20 off.")) is True


def test_accuracy_report_pure_and_empty():
    empty = accuracy_report([])
    assert empty["overall"]["n_calls"] == 0 and empty["overall"]["answer_rate"] is None
    assert (
        empty["overall"]["avg_duration_seconds"] is None and empty["overall"]["extraction_coverage"] is None
    )
    assert empty["recap_agreement"] == {"checked": 0, "agreed": 0, "rate": None, "disagreed_call_ids": []}
    assert set(empty["tracks"]) == {"quote", "schedule", "negotiate"}
    assert accuracy_report([None, "junk", 3])["overall"]["n_calls"] == 0  # type: ignore[list-item]

    rows = [
        {"state": "failed", "termination_type": "no-answer", "track": "quote"},
        {"state": "failed", "termination_type": "busy", "track": "quote"},
        {"state": "done", "track": "schedule", "outcome": {"slot_iso": "2026-10-20", "confirmed": False}},
        {"state": "talking", "track": "quote"},
    ]
    report = accuracy_report(rows)
    assert report["overall"]["n_calls"] == 4 and report["overall"]["n_terminal"] == 3
    assert (
        report["overall"]["no_answer_rate"] == round(2 / 3, 4) and report["overall"]["voicemail_rate"] == 0.0
    )
    assert report["tracks"]["schedule"] == {"n_done": 1, "n_with_value": 0, "success_rate": 0.0}
    assert report["overall"]["extraction_coverage"] == 1.0, "a slot was read even though it was not confirmed"


def test_keewano_token_kind():
    assert keewano_token_kind(None) == "missing" and keewano_token_kind("  ") == "missing"
    assert keewano_token_kind("kwpat-abc") == "personal_token"
    assert keewano_token_kind("KWPAT_ABC") == "personal_token"
    assert keewano_token_kind("eyJhbGciOi.eyJwcm9q.sig") == "project_key"
    assert keewano_token_kind("just-a-string") == "unknown"
