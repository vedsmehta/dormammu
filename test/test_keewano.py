import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from events import keewano

USER = "22222222-2222-2222-2222-222222222222"


def _event(type="run_requested", data=None, **extra):
    row = {
        "id": 1,
        "user_id": USER,
        "run_id": "run-1",
        "call_id": None,
        "type": type,
        "data": data if data is not None else {"request": "cheaper internet"},
        "created_at": "2026-10-11T20:00:00+00:00",
    }
    row.update(extra)
    return row


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    keewano.reset_state()
    monkeypatch.setenv("KEEWANO_TOKEN", "test-token")
    yield
    keewano.reset_state()


def test_event_type_ids_are_custom_and_stable():
    assert keewano.EVENT_TYPE_IDS["run_requested"] == 2001
    assert keewano.EVENT_TYPE_IDS["ledger_written"] == 2008
    assert len(set(keewano.EVENT_TYPE_IDS.values())) == len(keewano.EVENT_TYPE_IDS)
    assert all(type_id >= 2000 for type_id in keewano.EVENT_TYPE_IDS.values())
    assert keewano.CUSTOM_EVENTS_HASH != 0


async def test_registers_once_then_sends(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})

    assert await keewano.send_events(USER, [_event()]) is True

    register = _body(httpx_mock.get_requests(url=keewano.CUSTOM_URL)[0])
    assert register == {"customEventsHash": keewano.CUSTOM_EVENTS_HASH, "eventTypes": keewano.CUSTOM_EVENTS}

    request = httpx_mock.get_requests(url=keewano.INGRESS_URL)[0]
    assert request.headers["k-token"] == "test-token"
    body = _body(request)
    assert body["userId"] == USER
    assert body["sdkVersion"] == "0.0.0"
    assert body["batch"] == 1
    assert body["dataSessionId"] == keewano.DATA_SESSION_ID
    assert body["customEventsHash"] == keewano.CUSTOM_EVENTS_HASH
    (sent,) = body["events"]
    assert sent["eventTypeId"] == 2001
    assert sent["dataType"] == "string"
    assert sent["timestamp"] == int(datetime(2026, 10, 11, 20, tzinfo=UTC).timestamp())
    assert json.loads(sent["data"]) == {"type": "run_requested", "run_id": "run-1", "request": "cheaper internet"}


async def test_truncates_every_string_to_256(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=200, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})
    long = "x" * 1000
    data = {"transcript": long, "nested": {"note": long, "items": [long, 7]}, "n": 3}

    assert await keewano.send_events(USER, [_event(type="call_terminated", data=data)]) is True

    payload = json.loads(_body(httpx_mock.get_requests(url=keewano.INGRESS_URL)[0])["events"][0]["data"])
    assert len(payload["transcript"]) == 256
    assert len(payload["nested"]["note"]) == 256
    assert len(payload["nested"]["items"][0]) == 256
    assert payload["nested"]["items"][1] == 7
    assert payload["n"] == 3


async def test_batch_increments_and_registration_happens_once(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={}, is_reusable=True)

    assert await keewano.send_events(USER, [_event()]) is True
    assert await keewano.send_events(USER, [_event(type="call_dispatched")]) is True
    assert await keewano.send_events(USER, [_event(type="run_aggregated")]) is True

    assert len(httpx_mock.get_requests(url=keewano.CUSTOM_URL)) == 1
    batches = [_body(r)["batch"] for r in httpx_mock.get_requests(url=keewano.INGRESS_URL)]
    assert batches == [1, 2, 3]


async def test_http_error_returns_false_and_warns_once(httpx_mock, caplog):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(
        url=keewano.INGRESS_URL, method="POST", status_code=403,
        json={"statusCode": 403, "message": "Invalid Api Key"}, is_reusable=True,
    )
    with caplog.at_level(logging.WARNING, logger="dormammu.keewano"):
        assert await keewano.send_events(USER, [_event()]) is False
        assert await keewano.send_events(USER, [_event()]) is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "403" in warnings[0].getMessage()


async def test_rejected_key_names_project_api_key_and_sets_last_error(httpx_mock, caplog):
    # The real failure seen Oct 11 2026: a personal access token gets 403 on /custom and /in.
    httpx_mock.add_response(
        url=keewano.CUSTOM_URL, method="POST", status_code=403,
        json={"statusCode": 403, "message": "Invalid Api Key"}, is_reusable=True,
    )
    with caplog.at_level(logging.WARNING, logger="dormammu.keewano"):
        assert await keewano.send_events(USER, [_event()]) is False
        assert await keewano.send_events(USER, [_event()]) is False
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "403" in warnings[0]
    assert "personal access token" in warnings[0] and "Project API Key" in warnings[0]
    # Same key would get the same answer, so no ingress POST is attempted.
    assert httpx_mock.get_requests(url=keewano.INGRESS_URL) == []
    assert keewano.last_status == 403
    assert warnings[0] == f"keewano: {keewano.last_error}"
    snapshot = keewano.status()
    assert snapshot["last_error"] == keewano.last_error and snapshot["registered"] is False
    assert "test-token" not in json.dumps(snapshot)


async def test_success_clears_last_error(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})
    keewano.last_error = "stale failure from an earlier send"
    assert await keewano.send_events(USER, [_event()]) is True
    assert keewano.last_error is None
    assert keewano.last_status == 202
    assert keewano.status()["batch"] == 1


async def test_transport_exception_never_raises(httpx_mock, caplog):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=keewano.CUSTOM_URL)
    with caplog.at_level(logging.WARNING, logger="dormammu.keewano"):
        assert await keewano.send_events(USER, [_event()]) is False
    assert any("ConnectError" in r.getMessage() for r in caplog.records)
    assert keewano.last_error is not None and "ConnectError" in keewano.last_error


async def test_no_token_returns_false_without_network(monkeypatch, httpx_mock):
    monkeypatch.delenv("KEEWANO_TOKEN")
    assert await keewano.send_events(USER, [_event()]) is False
    assert httpx_mock.get_requests() == []


async def test_empty_or_garbage_events_return_false(httpx_mock):
    assert await keewano.send_events(USER, []) is False
    assert await keewano.send_events(USER, ["not a dict", None]) is False
    assert httpx_mock.get_requests() == []


async def test_registration_conflict_falls_back_to_builtin_id(httpx_mock, caplog):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=409, json={"message": "hash"})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={}, is_reusable=True)

    with caplog.at_level(logging.WARNING, logger="dormammu.keewano"):
        assert await keewano.send_events(USER, [_event()]) is True
        assert await keewano.send_events(USER, [_event(type="call_connected")]) is True

    assert len(httpx_mock.get_requests(url=keewano.CUSTOM_URL)) == 1
    for request in httpx_mock.get_requests(url=keewano.INGRESS_URL):
        body = _body(request)
        assert body["customEventsHash"] == 0
        assert body["events"][0]["eventTypeId"] == keewano.FALLBACK_EVENT_TYPE_ID
        assert json.loads(body["events"][0]["data"])["type"] in {"run_requested", "call_connected"}
    assert sum("falling back" in r.getMessage() for r in caplog.records) == 1


async def test_unknown_event_type_uses_builtin_id(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})
    assert await keewano.send_events(USER, [_event(type="something_new")]) is True
    sent = _body(httpx_mock.get_requests(url=keewano.INGRESS_URL)[0])["events"][0]
    assert sent["eventTypeId"] == keewano.FALLBACK_EVENT_TYPE_ID
    assert json.loads(sent["data"])["type"] == "something_new"


def test_truncate_strings_is_pure():
    original = {"a": "y" * 300, "b": ["z" * 300]}
    out = keewano.truncate_strings(original)
    assert len(out["a"]) == 256 and len(out["b"][0]) == 256
    assert len(original["a"]) == 300


# -- typed companions --------------------------------------------------------------------------

TS = int(datetime(2026, 10, 11, 20, tzinfo=UTC).timestamp())


def _ids(events: list[dict]) -> list[int]:
    return [e["eventTypeId"] for e in events]


def test_companion_ids_are_appended_after_lifecycle_ids():
    assert keewano.COMPANION_TYPE_IDS["call_track"] == 2009
    assert keewano.COMPANION_TYPE_IDS["run_multiplier_x100"] == 2016
    ids = list(keewano.EVENT_TYPE_IDS.values()) + list(keewano.COMPANION_TYPE_IDS.values())
    assert ids == list(range(2001, 2017))
    by_id = {e["eventTypeId"]: e for e in keewano.CUSTOM_EVENTS}
    assert all(by_id[i]["dataType"] == "string" for i in range(2001, 2009))
    assert all(by_id[i]["dataType"] == "uint" for i in range(2009, 2017))
    assert len(by_id) == 16


def test_vendor_key_hash_is_stable_nonzero_uint():
    a = keewano.vendor_key_hash("comcast")
    assert a == keewano.vendor_key_hash("comcast")
    assert 1 <= a <= 2**32 - 1
    assert a != keewano.vendor_key_hash("xfinity")


def test_call_terminated_companions_precede_blob_with_same_timestamp():
    data = {
        "dial_call_id": "d1", "track": "quote", "vendor_key": "comcast", "simulated": False,
        "state": "failed", "termination_type": "voicemail", "duration_seconds": 12.4, "voicemail": True,
    }
    out = keewano.encode_batch([_event(type="call_terminated", data=data, call_id="c1")])
    assert [(e["eventTypeId"], e["data"]) for e in out[:-1]] == [
        (2009, 1), (2010, keewano.vendor_key_hash("comcast")), (2011, 0), (2012, 2), (2013, 12),
    ]
    assert all(e["dataType"] == "uint" and e["timestamp"] == TS for e in out[:-1])
    blob = out[-1]
    assert blob["eventTypeId"] == 2005 and blob["timestamp"] == TS and blob["dataType"] == "string"
    assert json.loads(blob["data"])["vendor_key"] == "comcast"


def test_outcome_amount_cents_from_quote_or_negotiate_only():
    def amounts(outcome, track):
        row = _event(type="outcome_extracted", data={"track": track, "outcome": outcome, "simulated": True}, call_id="c1")
        return [e["data"] for e in keewano.encode_batch([row]) if e["eventTypeId"] == 2014]

    assert amounts({"track": "quote", "amount_usd": 1890.5, "unit": "total"}, "quote") == [189050]
    assert amounts({"track": "negotiate", "action": "reduced", "saved_usd": 12.34}, "negotiate") == [1234]
    assert amounts({"track": "schedule", "slot_iso": "2026-10-16T14:30", "confirmed": True}, "schedule") == []
    assert amounts({"track": "quote", "amount_usd": None, "note": "empty transcript"}, "quote") == []
    assert amounts({"track": "quote", "amount_usd": -5}, "quote") == []


def test_simulated_outcome_emits_track_simulated_and_amount():
    data = {"simulated": True, "track": "negotiate", "outcome": {"track": "negotiate", "saved_usd": 20}}
    out = keewano.encode_batch([_event(type="outcome_extracted", data=data, call_id="c1")])
    assert [(e["eventTypeId"], e["data"]) for e in out[:-1]] == [(2009, 3), (2011, 1), (2014, 2000)]
    assert out[-1]["eventTypeId"] == 2006


def test_run_aggregated_companions_only_when_fields_present():
    full = {"n_calls": 3, "wall_clock_seconds": 151.2, "talk_seconds": 300, "multiplier": 2.87}
    out = keewano.encode_batch([_event(type="run_aggregated", data=full)])
    assert [(e["eventTypeId"], e["data"]) for e in out] == [(2015, 151), (2016, 287), (2007, out[-1]["data"])]
    failed = keewano.encode_batch([_event(type="run_aggregated", data={"error": "boom"})])
    assert _ids(failed) == [2007]
    nulls = keewano.encode_batch([_event(type="run_aggregated", data={"wall_clock_seconds": None, "multiplier": None})])
    assert _ids(nulls) == [2007]


def test_no_companions_when_fields_missing_or_unknown():
    assert _ids(keewano.encode_batch([_event()])) == [2001]
    assert _ids(keewano.encode_batch([_event(type="call_connected", data={}, call_id="c1")])) == [2004]
    odd = {"track": "fax", "termination_type": "weird", "duration_seconds": "12", "vendor_key": "", "simulated": "yes"}
    assert _ids(keewano.encode_batch([_event(type="call_terminated", data=odd, call_id="c1")])) == [2005]
    assert keewano.companion_values({"type": "x", "data": "not a dict"}) == []


def test_termination_codes_cover_dial_and_voicemail_guard():
    for name, code in {"completed": 1, "voicemail": 2, "no-answer": 3, "busy": 4, "failed": 5, "canceled": 6}.items():
        out = keewano.encode_batch([_event(type="call_terminated", data={"termination_type": name}, call_id="c1")])
        assert [(e["eventTypeId"], e["data"]) for e in out[:-1]] == [(2012, code)]
    out = keewano.encode_batch([_event(type="call_terminated", data={"termination_type": "No_Answer"}, call_id="c1")])
    assert out[0]["data"] == 3


async def test_companions_are_sent_in_the_same_batch(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=202, json={})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})
    data = {"track": "schedule", "vendor_key": "dr-chen", "simulated": False, "dial_call_id": "d2"}
    assert await keewano.send_events(USER, [_event(type="call_dispatched", data=data, call_id="c2")]) is True
    register = _body(httpx_mock.get_requests(url=keewano.CUSTOM_URL)[0])
    assert len(register["eventTypes"]) == 16
    body = _body(httpx_mock.get_requests(url=keewano.INGRESS_URL)[0])
    assert body["batch"] == 1 and body["customEventsHash"] == keewano.CUSTOM_EVENTS_HASH
    assert _ids(body["events"]) == [2009, 2010, 2011, 2002]
    assert body["events"][0]["data"] == 2
    assert len({e["timestamp"] for e in body["events"]}) == 1


async def test_builtin_fallback_drops_companions(httpx_mock):
    httpx_mock.add_response(url=keewano.CUSTOM_URL, method="POST", status_code=409, json={"message": "hash"})
    httpx_mock.add_response(url=keewano.INGRESS_URL, method="POST", status_code=202, json={})
    data = {"track": "quote", "vendor_key": "comcast", "termination_type": "completed", "duration_seconds": 90}
    assert await keewano.send_events(USER, [_event(type="call_terminated", data=data, call_id="c1")]) is True
    body = _body(httpx_mock.get_requests(url=keewano.INGRESS_URL)[0])
    assert _ids(body["events"]) == [keewano.FALLBACK_EVENT_TYPE_ID]
    assert body["customEventsHash"] == 0
    assert json.loads(body["events"][0]["data"])["termination_type"] == "completed"
