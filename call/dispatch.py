"""Fan out calls on Dial. Two entry points:

dispatch_run(store, run, calls, profile)   THE FAN-OUT for the service (BUILD-PLAN §4.5). One
    coroutine per calls row under asyncio.gather(return_exceptions=True). A refused call (Dial
    429 call_limit_reached, bad number, network) becomes a labeled simulated row, never a red
    failure, and never takes down the other rows. Never raises.

run_calls(requests, max_concurrent, timeout_seconds)   the CLI path on Dial, keeping the frozen
    CallRequest / CallResult contract and file-based write_result:

    uv run python -m call.dispatch --to +14155550123 --to +14155550124 \
        --task "Get a quote to move a 2-bedroom apartment on Nov 1." --field price --field date
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from call import dial_client
from call.dial_client import DialError
from call.ingest import VOICEMAIL_MIN_SECONDS, has_callee_turn
from call.models import CallRequest, CallResult, write_result
from call.state import STATE_RANK
from events.log import log_event
from router.instructions import build
from settings import load_env

MAX_CALL_DURATION_SECONDS = 180
POLL_SECONDS = 2.0
TRANSCRIPT_WAIT_SECONDS = 60.0
DEFAULT_MAX_CONCURRENT = 2  # the verified cap on this Dial account (CONTRACT.md)

_SUMMARY_LINE = re.compile(r"summary\s*:\s*(.+)", re.IGNORECASE | re.DOTALL)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------------------------------
# Service fan-out
# --------------------------------------------------------------------------------------------------


def _plan_from_row(row: dict) -> dict:
    return {
        "vendor_key": row.get("vendor_key"),
        "track": row.get("track"),
        "objective": row.get("objective") or "",
        "voice_gender": row.get("voice_gender") or "female",
    }


def _vendor_from_row(row: dict) -> dict:
    return {
        "id": row.get("vendor_id"),
        "owner_id": row.get("user_id"),
        "key": row.get("vendor_key"),
        "name": row.get("target_name") or "",
        "track": row.get("track"),
        "phone": row.get("target_phone") or "",
        "is_demo": bool(row.get("is_demo", False)),
    }


def _reason(exc: BaseException) -> str:
    if isinstance(exc, DialError):
        return f"dial {exc.status_code} {exc.code or '-'}: {exc.message}"[:500]
    return f"{type(exc).__name__}: {exc}"[:500]


async def _dispatch_one(store: Any, run: dict, row: dict, profile: dict | None) -> None:
    call_id = row["id"]
    run_id = run.get("id") or row.get("run_id")
    user_id = run.get("user_id") or row.get("user_id") or ""
    simulated_reason: str | None = None
    is_limit = False
    dial_call_id: str | None = None
    try:
        instruction = build(_plan_from_row(row), _vendor_from_row(row), profile, row.get("prior_context"))
        if not (row.get("vendor_id") or row.get("vendor_key")):
            raise ValueError("row is not backed by a vendors row; only vendor rows are dialable")
        if not row.get("target_phone"):
            raise ValueError("row has no target_phone")
        if os.environ.get("DORMAMMU_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}:
            raise RuntimeError("DORMAMMU_DRY_RUN is set; no real call placed")
        call = await dial_client.place_call(
            row["target_phone"],
            instruction,
            voice_gender=_plan_from_row(row)["voice_gender"],
            max_duration_seconds=MAX_CALL_DURATION_SECONDS,
            idempotency_key=call_id,
        )
        dial_call_id = (call or {}).get("id") if isinstance(call, dict) else None
        if not dial_call_id:
            raise ValueError("Dial returned no call id")
        store.update_call(call_id, {"dial_call_id": dial_call_id, "error": None, "started_at": _now_iso()})
        store.update_call_state(call_id, "dialing", STATE_RANK["dialing"])
    except Exception as exc:  # noqa: BLE001
        simulated_reason = _reason(exc)
        is_limit = isinstance(exc, DialError) and exc.is_limit
        with contextlib.suppress(Exception):
            store.update_call(call_id, {"simulated": True, "error": None, "started_at": _now_iso()})
            store.update_call_state(call_id, "dialing", STATE_RANK["dialing"])
        log_event(
            store,
            user_id,
            "call_simulated",
            {
                "reason": simulated_reason,
                "limit": is_limit,
                "track": row.get("track"),
                "vendor_key": row.get("vendor_key"),
            },
            run_id=run_id,
            call_id=call_id,
        )
    log_event(
        store,
        user_id,
        "call_dispatched",
        {
            "track": row.get("track"),
            "vendor_key": row.get("vendor_key"),
            "simulated": simulated_reason is not None,
            "dial_call_id": dial_call_id,
        },
        run_id=run_id,
        call_id=call_id,
    )


async def dispatch_run(store: Any, run: dict, calls: list[dict], profile: dict | None) -> list[dict]:
    """Place every call in `calls` concurrently. Returns the refreshed rows in input order."""
    rows = [r for r in (calls or []) if isinstance(r, dict) and r.get("id")]
    if rows:
        await asyncio.gather(
            *(_dispatch_one(store, run or {}, row, profile) for row in rows), return_exceptions=True
        )
    refreshed = []
    for row in rows:
        try:
            refreshed.append(store.get_call(row["id"]) or row)
        except Exception:  # noqa: BLE001
            refreshed.append(row)
    return refreshed


# --------------------------------------------------------------------------------------------------
# CLI path: CallRequest -> Dial -> CallResult
# --------------------------------------------------------------------------------------------------


def build_instructions(request: CallRequest) -> str:
    """CLI instruction through the same builder the service uses, so the AI disclosure is always first."""
    objective = request.task.strip()
    if request.fields:
        objective += " Collect and confirm out loud: " + ", ".join(request.fields) + "."
    plan = {"vendor_key": None, "track": "quote", "objective": objective, "voice_gender": "female"}
    vendor = {"key": None, "name": "the business", "track": "quote"}
    profile = {"display_name": request.caller_name} if request.caller_name else None
    return build(plan, vendor, profile)


def _turns_to_transcript(dial_call: dict) -> list[dict[str, str]]:
    turns = dial_call.get("transcriptTurns")
    if isinstance(turns, list) and turns:
        return [{"role": str(t.get("speaker") or ""), "text": str(t.get("text") or "")} for t in turns]
    text = dial_call.get("transcript")
    if not isinstance(text, str) or not text.strip():
        return []
    out = []
    for line in text.splitlines():
        if ":" in line:
            role, _, said = line.partition(":")
            out.append({"role": role.strip().lower(), "text": said.strip()})
        elif line.strip():
            out.append({"role": "", "text": line.strip()})
    return out


def _summary_of(transcript: list[dict[str, str]]) -> str:
    for turn in reversed(transcript):
        match = _SUMMARY_LINE.search(turn.get("text") or "")
        if match:
            return match.group(1).strip()
    return ""


def result_from_dial(request: CallRequest, dial_call: dict) -> CallResult:
    status = dial_call.get("status") or {}
    termination = status.get("terminationType") or dial_call.get("terminationType")
    duration = float(dial_call.get("duration") or 0)
    transcript = _turns_to_transcript(dial_call)
    result = CallResult(room=str(dial_call.get("id") or ""), to=request.to, transcript=transcript)
    result.duration_seconds = duration
    if termination == "completed":
        callee = has_callee_turn(dial_call.get("transcriptTurns"), dial_call.get("transcript"))
        if callee is False or duration < VOICEMAIL_MIN_SECONDS:
            result.status, result.success, result.error = "failed", False, "voicemail"
        else:
            result.status, result.success = "completed", True
            result.summary = _summary_of(transcript)
    elif termination in ("busy", "no-answer"):
        result.status, result.success, result.error = "no_answer", False, termination
    else:
        result.status, result.success = "failed", False
        result.error = dial_call.get("failureReason") or termination or "terminated without a reason"
    return result


def _is_terminated(dial_call: dict) -> bool:
    return str((dial_call.get("status") or {}).get("state") or "").lower() == "terminated"


async def run_call(request: CallRequest, timeout_seconds: float) -> CallResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    placed = await dial_client.place_call(
        request.to,
        build_instructions(request),
        max_duration_seconds=MAX_CALL_DURATION_SECONDS,
        idempotency_key=str(uuid.uuid4()),
    )
    call_id = placed["id"]
    current = placed
    while not _is_terminated(current):
        if loop.time() >= deadline:
            with contextlib.suppress(Exception):
                await dial_client.stop_call(call_id)
            return CallResult(room=call_id, to=request.to, status="timeout", success=False, error="timeout")
        await asyncio.sleep(POLL_SECONDS)
        current = await dial_client.get_call(call_id)
    # Transcription finishes asynchronously after hangup; give it a bounded wait.
    termination = (current.get("status") or {}).get("terminationType") or current.get("terminationType")
    transcript_deadline = min(deadline, loop.time() + TRANSCRIPT_WAIT_SECONDS)
    while (
        termination == "completed" and current.get("transcript") is None and loop.time() < transcript_deadline
    ):
        await asyncio.sleep(POLL_SECONDS)
        current = await dial_client.get_call(call_id)
    return result_from_dial(request, current)


async def run_calls(
    requests: list[CallRequest],
    max_concurrent: int | None = None,
    timeout_seconds: float = 900.0,
) -> list[CallResult]:
    cap = max_concurrent or int(os.environ.get("MAX_CONCURRENT_CALLS") or DEFAULT_MAX_CONCURRENT)
    limit = asyncio.Semaphore(max(1, cap))

    async def bounded(request: CallRequest) -> CallResult:
        async with limit:
            try:
                result = await run_call(request, timeout_seconds)
            except Exception as e:  # noqa: BLE001
                result = CallResult(room="", to=request.to, status="failed", success=False, error=repr(e))
        if result.room:
            try:
                write_result(result)
            except OSError:
                pass
        return result

    return list(await asyncio.gather(*(bounded(r) for r in requests)))


async def _main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Place parallel AI calls on Dial")
    parser.add_argument("--to", action="append", required=True, help="E.164 number; repeat for more calls")
    parser.add_argument("--task", required=True, help="what the agent should accomplish")
    parser.add_argument("--field", action="append", default=[], help="information to collect; repeatable")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait per call")
    parser.add_argument("--max-concurrent", type=int, default=None, help="parallel calls (account cap is 2)")
    args = parser.parse_args()

    requests = [CallRequest(to=to, task=args.task, fields=args.field) for to in args.to]
    for result in await run_calls(requests, max_concurrent=args.max_concurrent, timeout_seconds=args.timeout):
        print(f"--- {result.to[:2]}***{result.to[-2:]}: {result.status} ({result.duration_seconds:.0f}s)")
        if result.error:
            print(f"error: {result.error}")
        if result.summary:
            print(result.summary)
        for turn in result.transcript:
            print(f"  {turn['role']}: {turn['text']}")


if __name__ == "__main__":
    asyncio.run(_main())
