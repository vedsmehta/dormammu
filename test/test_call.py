import asyncio
import json

import httpx
import pytest

from call import dial_client, dispatch
from call.dial_client import DialError
from call.models import CallRequest, CallResult, read_result, write_result

API = "https://api.getdial.ai/api/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("KEEWANO_ENABLED", "false")
    monkeypatch.setenv("DIAL_API_KEY", "sk_test_not_real")
    monkeypatch.setenv("DIAL_NUMBER", "+14155550100")
    monkeypatch.setattr(dispatch, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(dispatch, "TRANSCRIPT_WAIT_SECONDS", 0.05)


# ----- frozen models ---------------------------------------------------------------------------------


def test_request_round_trips_through_json():
    request = CallRequest(to="+14155550123", task="Get a quote", fields=["price"])
    assert CallRequest.from_json(request.to_json()) == request


def test_result_written_and_read_back():
    write_result(CallResult(room="call-abc", to="+1", status="completed", details={"price": "$90"}))
    assert read_result("call-abc").details == {"price": "$90"}
    assert read_result("call-missing") is None


def test_cli_instructions_open_with_disclosure_and_carry_task():
    text = dispatch.build_instructions(CallRequest(to="+1", task="Book a haircut", fields=["time", "price"]))
    assert text.splitlines()[0].startswith('Your first words on this call, exactly: "Hi, this is ')
    assert "AI assistant" in text.splitlines()[1]
    assert "Book a haircut" in text
    assert "time, price" in text
    assert "Just to confirm" in text


# ----- dial_client over a mocked transport ---------------------------------------------------------------


async def test_place_call_sends_contract_body_and_headers(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/calls",
        status_code=201,
        json={"call": {"id": "dc_1", "status": {"state": "Queued"}}},
    )
    call = await dial_client.place_call(
        "+14155550123", "say hi", idempotency_key="row-1", transfer_to="+14155550199"
    )
    assert call == {"id": "dc_1", "status": {"state": "Queued"}}
    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer sk_test_not_real"
    assert request.headers["Idempotency-Key"] == "row-1"
    assert json.loads(request.content) == {
        "to": "+14155550123",
        "outboundInstruction": "say hi",
        "fromNumber": "+14155550100",
        "voiceGender": "female",
        "maxCallDurationSeconds": 180,
        "transferTo": "+14155550199",
    }


async def test_place_call_generates_idempotency_key_and_honours_overrides(httpx_mock):
    httpx_mock.add_response(method="POST", url=f"{API}/calls", json={"call": {"id": "dc_2"}})
    await dial_client.place_call(
        "+1", "x", from_number="+14155550111", voice_gender="male", max_duration_seconds=120
    )
    request = httpx_mock.get_request()
    assert len(request.headers["Idempotency-Key"]) >= 32
    body = json.loads(request.content)
    assert body["fromNumber"] == "+14155550111" and body["voiceGender"] == "male"
    assert body["maxCallDurationSeconds"] == 120 and "transferTo" not in body


async def test_limit_error_is_recognised(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/calls",
        status_code=429,
        json={"error": "too many", "code": "call_limit_reached"},
    )
    with pytest.raises(DialError) as info:
        await dial_client.place_call("+1", "x")
    assert info.value.is_limit and info.value.status_code == 429 and info.value.code == "call_limit_reached"
    assert DialError(200, "call_limit_reached", "m").is_limit
    assert DialError(429, None, "m").is_limit
    assert not DialError(400, None, "m").is_limit


async def test_validation_error_shape_is_flattened(httpx_mock):
    httpx_mock.add_response(
        method="POST",
        url=f"{API}/calls",
        status_code=400,
        json={
            "error": {
                "formErrors": [],
                "fieldErrors": {"to": ["Too small: expected string to have >=7 characters"]},
            }
        },
    )
    with pytest.raises(DialError) as info:
        await dial_client.place_call("+1", "x")
    assert info.value.status_code == 400 and info.value.code is None
    assert "to: Too small" in info.value.message


async def test_not_found_and_envelopes(httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=f"{API}/calls/nope",
        status_code=404,
        json={"error": "Call nope not found", "code": "not_found"},
    )
    httpx_mock.add_response(
        method="GET", url=f"{API}/calls/dc_1", json={"call": {"id": "dc_1", "duration": 3}}
    )
    httpx_mock.add_response(method="GET", url=f"{API}/numbers", json={"numbers": [{"id": "n1"}]})
    httpx_mock.add_response(method="GET", url=f"{API}/account", json={"limits": {"maxConcurrentCalls": 2}})
    httpx_mock.add_response(method="POST", url=f"{API}/calls/dc_1/stop", json={"call": {"id": "dc_1"}})
    with pytest.raises(DialError) as info:
        await dial_client.get_call("nope")
    assert info.value.code == "not_found" and not info.value.is_limit
    assert await dial_client.get_call("dc_1") == {"id": "dc_1", "duration": 3}
    assert await dial_client.list_numbers() == [{"id": "n1"}]
    assert (await dial_client.get_account())["limits"]["maxConcurrentCalls"] == 2
    assert await dial_client.stop_call("dc_1") == {"id": "dc_1"}


async def test_transport_errors_become_dial_errors(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectTimeout("slow"), method="GET", url=f"{API}/account")
    with pytest.raises(DialError) as info:
        await dial_client.get_account()
    assert info.value.status_code == 0 and info.value.code == "transport"


# ----- run_calls on a monkeypatched dial client ----------------------------------------------------------


class FakeDial:
    def __init__(self, polls_until_done=2, termination="completed", transcript=True, duration=40, fail=None):
        self.polls_until_done = polls_until_done
        self.termination = termination
        self.transcript = transcript
        self.duration = duration
        self.fail = fail
        self.polls = {}
        self.stopped = []
        self.active = self.peak = 0

    async def place_call(self, to, instruction, **kwargs):
        if self.fail:
            raise self.fail
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.03)
        call_id = f"dc_{to[-4:]}"
        self.polls[call_id] = 0
        return {"id": call_id, "to": to, "status": {"state": "Queued", "terminationType": None}}

    async def get_call(self, call_id):
        self.polls[call_id] += 1
        if self.polls[call_id] < self.polls_until_done:
            return {"id": call_id, "status": {"state": "In-Progress", "terminationType": None}}
        self.active = max(0, self.active - 1)
        turns = [
            {"speaker": "agent", "text": "Hi, this is an AI assistant.", "startMs": 0, "endMs": 2000},
            {"speaker": "user", "text": "Ninety dollars, Friday at 3.", "startMs": 2500, "endMs": 5000},
            {
                "speaker": "agent",
                "text": "Summary: $90, Friday at 3. Goodbye.",
                "startMs": 5500,
                "endMs": 8000,
            },
        ]
        return {
            "id": call_id,
            "duration": self.duration,
            "transcript": "agent: hi\nuser: ninety\nagent: Summary: $90, Friday at 3. Goodbye."
            if self.transcript
            else None,
            "transcriptTurns": turns if self.transcript else None,
            "terminationType": self.termination,
            "status": {"state": "Terminated", "terminationType": self.termination},
        }

    async def stop_call(self, call_id):
        self.stopped.append(call_id)
        return {"id": call_id}


def patch_dial(monkeypatch, fake):
    monkeypatch.setattr(dial_client, "place_call", fake.place_call)
    monkeypatch.setattr(dial_client, "get_call", fake.get_call)
    monkeypatch.setattr(dial_client, "stop_call", fake.stop_call)


async def test_run_calls_respects_concurrency_limit(monkeypatch):
    fake = FakeDial(polls_until_done=3)
    patch_dial(monkeypatch, fake)
    requests = [CallRequest(to=f"+1415555000{i}", task="t") for i in range(6)]

    results = await dispatch.run_calls(requests, max_concurrent=2)

    assert fake.peak == 2
    assert [r.to for r in results] == [r.to for r in requests]
    assert all(r.status == "completed" and r.success for r in results)
    assert results[0].summary == "$90, Friday at 3. Goodbye."
    assert results[0].transcript[1] == {"role": "user", "text": "Ninety dollars, Friday at 3."}
    assert results[0].duration_seconds == 40
    assert read_result(results[0].room).status == "completed"


async def test_run_calls_reports_failures(monkeypatch):
    patch_dial(monkeypatch, FakeDial(fail=RuntimeError("trunk missing")))
    [result] = await dispatch.run_calls([CallRequest(to="+14155550000", task="t")])
    assert result.status == "failed" and result.success is False
    assert "trunk missing" in result.error
    assert result.room == ""


@pytest.mark.parametrize(
    ("termination", "expected", "error"),
    [
        ("no-answer", "no_answer", "no-answer"),
        ("busy", "no_answer", "busy"),
        ("failed", "failed", "failed"),
        (None, "failed", None),
    ],
)
async def test_run_calls_maps_terminations(monkeypatch, termination, expected, error):
    patch_dial(monkeypatch, FakeDial(termination=termination, transcript=False, duration=0))
    [result] = await dispatch.run_calls([CallRequest(to="+14155550000", task="t")])
    assert result.status == expected and result.success is False
    if error:
        assert result.error == error


async def test_run_calls_voicemail_guard(monkeypatch):
    patch_dial(monkeypatch, FakeDial(duration=8))
    [result] = await dispatch.run_calls([CallRequest(to="+14155550000", task="t")])
    assert result.status == "failed" and result.error == "voicemail" and result.summary == ""


async def test_run_calls_times_out_and_stops_the_call(monkeypatch):
    fake = FakeDial(polls_until_done=10_000)
    patch_dial(monkeypatch, fake)
    [result] = await dispatch.run_calls([CallRequest(to="+14155550000", task="t")], timeout_seconds=0.1)
    assert result.status == "timeout" and fake.stopped == ["dc_0000"]
