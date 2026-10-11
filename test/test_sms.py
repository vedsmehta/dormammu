import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sms.messenger import dial_client, send_many, send_sms, wait_for_reply


class FakeDial:
    def __init__(self, inbound: list | None = None) -> None:
        self.sent = []
        self.inbound = inbound or []

    async def send_message(self, params):
        self.sent.append(params)
        return SimpleNamespace(id=f"msg_{len(self.sent)}", status="queued")

    async def list_messages(self, **filters):
        return self.inbound


async def test_send_sms_defaults_to_configured_number(monkeypatch):
    monkeypatch.setenv("DIAL_NUMBER", "+14155550100")
    dial = FakeDial()
    await send_sms(dial, "+14155550123", "hi")
    assert dial.sent[0].from_number == "+14155550100"
    assert dial.sent[0].to == "+14155550123"


async def test_send_many_sends_one_per_recipient(monkeypatch):
    monkeypatch.setenv("DIAL_NUMBER", "+14155550100")
    dial = FakeDial()
    results = await send_many(dial, ["+14155550001", "+14155550002", "+14155550003"], "quote?")
    assert len(results) == 3
    assert {p.to for p in dial.sent} == {"+14155550001", "+14155550002", "+14155550003"}


async def test_wait_for_reply_returns_first_inbound():
    reply = SimpleNamespace(body="yes, Friday at 3")
    found = await wait_for_reply(FakeDial([reply]), "+14155550123", datetime.now(UTC), timeout_seconds=1)
    assert found is reply


async def test_wait_for_reply_times_out():
    found = await wait_for_reply(
        FakeDial(), "+14155550123", datetime.now(UTC), timeout_seconds=0.2, poll_seconds=0.05
    )
    assert found is None


@pytest.mark.live
async def test_dial_number_is_on_account(require_env):
    require_env("DIAL_API_KEY", "DIAL_NUMBER")
    dial = dial_client()
    try:
        numbers = await dial.list_numbers()
    finally:
        await dial.close()
    configured = os.environ["DIAL_NUMBER"]
    assert any(configured in (n.number, n.id, n.nickname) for n in numbers), (
        f"{configured} not found; account has {[n.number for n in numbers]}"
    )


@pytest.mark.live
async def test_send_real_sms(require_env):
    require_env("DIAL_API_KEY", "DIAL_NUMBER", "TEST_SMS_TO")
    dial = dial_client()
    try:
        message = await send_sms(dial, os.environ["TEST_SMS_TO"], "Dormammu test message")
    finally:
        await dial.close()
    assert message.id
