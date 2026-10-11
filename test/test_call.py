import asyncio
import os
import subprocess
import sys
import threading
import time

import pytest
from livekit import api

from call import dispatch
from call.agent import build_instructions
from call.models import CallRequest, CallResult, read_result, write_result
from settings import ROOT


def test_request_round_trips_through_metadata():
    request = CallRequest(to="+14155550123", task="Get a quote", fields=["price"])
    assert CallRequest.from_json(request.to_json()) == request


def test_instructions_include_task_and_fields():
    text = build_instructions(CallRequest(to="+1", task="Book a haircut", fields=["time", "price"]))
    assert "Book a haircut" in text
    assert "time, price" in text


def test_result_written_and_read_back():
    write_result(CallResult(room="call-abc", to="+1", status="completed", details={"price": "$90"}))
    assert read_result("call-abc").details == {"price": "$90"}


async def test_wait_for_result_times_out():
    with pytest.raises(TimeoutError):
        await dispatch.wait_for_result("call-missing", timeout_seconds=0.2, poll_seconds=0.05)


async def test_run_calls_respects_concurrency_limit(monkeypatch):
    active = peak = 0

    async def fake_run_call(lk, request, timeout_seconds):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return CallResult(room="r", to=request.to, status="completed")

    class FakeLiveKitAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(dispatch, "run_call", fake_run_call)
    monkeypatch.setattr(dispatch.api, "LiveKitAPI", FakeLiveKitAPI)

    requests = [CallRequest(to=f"+1415555000{i}", task="t") for i in range(6)]
    results = await dispatch.run_calls(requests, max_concurrent=2)

    assert peak == 2
    assert [r.to for r in results] == [r.to for r in requests]


async def test_run_calls_reports_failures(monkeypatch):
    async def broken_run_call(lk, request, timeout_seconds):
        raise RuntimeError("trunk missing")

    class FakeLiveKitAPI:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(dispatch, "run_call", broken_run_call)
    monkeypatch.setattr(dispatch.api, "LiveKitAPI", FakeLiveKitAPI)

    [result] = await dispatch.run_calls([CallRequest(to="+1", task="t")])
    assert result.status == "failed"
    assert "trunk missing" in result.error


LIVEKIT_ENV = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


@pytest.mark.live
async def test_livekit_credentials(require_env):
    require_env(*LIVEKIT_ENV)
    async with api.LiveKitAPI() as lk:
        await lk.room.list_rooms(api.ListRoomsRequest())


@pytest.mark.live
async def test_outbound_trunk_exists(require_env):
    require_env(*LIVEKIT_ENV, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID")
    async with api.LiveKitAPI() as lk:
        trunks = await lk.sip.list_outbound_trunk(api.ListSIPOutboundTrunkRequest())
    ids = [t.sip_trunk_id for t in trunks.items]
    assert os.environ["LIVEKIT_SIP_OUTBOUND_TRUNK_ID"] in ids, f"trunk not found; have {ids}"


@pytest.fixture
def worker(require_env):
    require_env(*LIVEKIT_ENV, "LIVEKIT_SIP_OUTBOUND_TRUNK_ID", "TEST_CALL_TO")
    proc = subprocess.Popen(
        [sys.executable, "-m", "call.agent", "start"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line and proc.poll() is not None:
            pytest.fail("worker exited before registering; run `uv run python -m call.agent dev` to see why")
        if "registered worker" in line:
            break
    else:
        proc.terminate()
        pytest.fail("worker did not register within 60s")
    threading.Thread(target=proc.stdout.read, daemon=True).start()
    yield proc
    proc.terminate()
    proc.wait(timeout=15)


@pytest.mark.live
async def test_real_call(worker):
    request = CallRequest(
        to=os.environ["TEST_CALL_TO"],
        task="This is a test call. Ask the person what their favorite color is, then thank them.",
        fields=["favorite_color"],
    )
    [result] = await dispatch.run_calls([request], timeout_seconds=300)
    print(result)
    assert result.status in {"answered", "completed"}, result.error
    assert result.transcript
