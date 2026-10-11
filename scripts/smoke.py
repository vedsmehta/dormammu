"""Smoke test: place ONE real Dial call to TEST_CALL_TO and print what happens.

    PYTHONPATH=. uv run python scripts/smoke.py

Needs DIAL_API_KEY, DIAL_NUMBER and TEST_CALL_TO in .env. Prints every status transition with a
timestamp, waits for the transcript after Terminated (transcription is async, up to 90s), prints
it. Phone numbers are masked in the output. Does not touch the database.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from call import dial_client
from call.dial_client import DialError
from router.instructions import SUMMARY_CLOSER
from settings import env, load_env

MAX_DURATION_SECONDS = 120
POLL_SECONDS = 2.0
CALL_WAIT_SECONDS = 300.0
TRANSCRIPT_WAIT_SECONDS = 90.0

INSTRUCTION = (
    "You are an AI assistant placing a short test call on behalf of a client. Your very first words, before "
    'anything else, must be: "Hi, this is an AI assistant calling on behalf of a client. This is a quick test '
    'call, it will take under a minute." Never claim to be human; if asked, confirm you are an AI.\n\n'
    "Your task: ask the person what their favorite color is, then ask them to tell you one sentence "
    "about how their day is going. Thank them.\n\n"
    "You are speaking out loud on a phone line. Use short, natural sentences, no small talk beyond the "
    "two questions. Never invent facts. If you reach voicemail, hang up without leaving a message.\n\n"
    f"{SUMMARY_CLOSER}"
)


def mask(value: object) -> str:
    return re.sub(r"\+?\d{7,}", "+1***", str(value))


def stamp() -> str:
    return datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]


def describe(call: dict) -> str:
    status = call.get("status") or {}
    return f"{status.get('state')} / {status.get('terminationType') or call.get('terminationType') or '-'}"


async def main() -> int:
    load_env()
    to = env("TEST_CALL_TO")
    print(f"[{stamp()}] account")
    account = await dial_client.get_account()
    print(f"[{stamp()}]   limits={json.dumps(account.get('limits'))}")

    print(
        f"[{stamp()}] placing call to {mask(to)} from {mask(env('DIAL_NUMBER'))}, max {MAX_DURATION_SECONDS}s"
    )
    t0 = time.monotonic()
    try:
        call = await dial_client.place_call(to, INSTRUCTION, max_duration_seconds=MAX_DURATION_SECONDS)
    except DialError as exc:
        print(f"[{stamp()}] place_call FAILED: {exc.status_code} {exc.code} {mask(exc.message)}")
        return 1
    call_id = call["id"]
    print(f"[{stamp()}] placed id={call_id} status={describe(call)}")
    print(f"[{stamp()}] raw keys: {sorted(call.keys())}")

    last = describe(call)
    while time.monotonic() - t0 < CALL_WAIT_SECONDS:
        status = call.get("status") or {}
        if str(status.get("state") or "").lower() == "terminated":
            break
        await asyncio.sleep(POLL_SECONDS)
        call = await dial_client.get_call(call_id)
        now = describe(call)
        if now != last:
            print(f"[{stamp()}] +{time.monotonic() - t0:5.1f}s  {last} -> {now}")
            last = now
    else:
        print(f"[{stamp()}] call did not terminate within {CALL_WAIT_SECONDS:.0f}s, stopping it")
        try:
            await dial_client.stop_call(call_id)
        except DialError as exc:
            print(f"[{stamp()}] stop_call: {exc}")

    print(
        f"[{stamp()}] terminated: duration={call.get('duration')} terminationType={call.get('terminationType')}"
    )
    print(f"[{stamp()}]   terminatedAt={call.get('terminatedAt')} callStartedAt={call.get('callStartedAt')}")

    t1 = time.monotonic()
    while call.get("transcript") is None and time.monotonic() - t1 < TRANSCRIPT_WAIT_SECONDS:
        await asyncio.sleep(POLL_SECONDS)
        call = await dial_client.get_call(call_id)
    waited = time.monotonic() - t1
    if call.get("transcript") is None:
        print(f"[{stamp()}] no transcript after {waited:.0f}s")
    else:
        print(f"[{stamp()}] transcript appeared after {waited:.0f}s")
        turns = call.get("transcriptTurns")
        if isinstance(turns, list):
            print(
                f"[{stamp()}] transcriptTurns ({len(turns)}), speakers={sorted({t.get('speaker') for t in turns})}"
            )
            for t in turns:
                print(
                    f"    [{t.get('startMs')}-{t.get('endMs')}ms] {t.get('speaker')}: {mask(t.get('text'))}"
                )
        else:
            print(f"[{stamp()}] transcriptTurns is {turns!r}")
        print("---- transcript ----")
        print(mask(call["transcript"]))
        print("--------------------")
    redacted = {k: v for k, v in call.items() if k not in ("transcript", "transcriptTurns", "instruction")}
    print(f"[{stamp()}] call record (masked): {mask(json.dumps(redacted, indent=1))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
