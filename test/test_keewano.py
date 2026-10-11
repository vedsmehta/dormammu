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


async def test_transport_exception_never_raises(httpx_mock, caplog):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=keewano.CUSTOM_URL)
    with caplog.at_level(logging.WARNING, logger="dormammu.keewano"):
        assert await keewano.send_events(USER, [_event()]) is False
    assert any("ConnectError" in r.getMessage() for r in caplog.records)


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
