"""Fan out calls to the caller agent and collect their results.

Requires the worker to be running (`uv run python -m call.agent dev`).

    uv run python -m call.dispatch --to +14155550123 --to +14155550124 \
        --task "Get a quote to move a 2-bedroom apartment on Nov 1." --field price --field date
"""

import argparse
import asyncio
import os
import uuid

from livekit import api

from call.agent import AGENT_NAME
from call.models import CallRequest, CallResult, read_result
from settings import load_env

load_env()


async def wait_for_result(room: str, timeout_seconds: float, poll_seconds: float = 1.0) -> CallResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        if result := read_result(room):
            return result
        await asyncio.sleep(poll_seconds)
    raise TimeoutError(room)


async def run_call(lk: api.LiveKitAPI, request: CallRequest, timeout_seconds: float) -> CallResult:
    room = f"call-{uuid.uuid4().hex[:12]}"
    await lk.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room, metadata=request.to_json())
    )
    try:
        return await wait_for_result(room, timeout_seconds)
    except TimeoutError:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room))
        return CallResult(room=room, to=request.to, status="timeout")


async def run_calls(
    requests: list[CallRequest],
    max_concurrent: int | None = None,
    timeout_seconds: float = 900.0,
) -> list[CallResult]:
    limit = asyncio.Semaphore(max_concurrent or int(os.environ.get("MAX_CONCURRENT_CALLS", "10")))

    async def bounded(lk: api.LiveKitAPI, request: CallRequest) -> CallResult:
        async with limit:
            try:
                return await run_call(lk, request, timeout_seconds)
            except Exception as e:  # noqa: BLE001
                return CallResult(room="", to=request.to, status="failed", error=repr(e))

    async with api.LiveKitAPI() as lk:
        return await asyncio.gather(*(bounded(lk, r) for r in requests))


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Place parallel AI calls")
    parser.add_argument("--to", action="append", required=True, help="E.164 number; repeat for more calls")
    parser.add_argument("--task", required=True, help="what the agent should accomplish")
    parser.add_argument("--field", action="append", default=[], help="information to collect; repeatable")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds to wait per call")
    args = parser.parse_args()

    requests = [CallRequest(to=to, task=args.task, fields=args.field) for to in args.to]
    for result in await run_calls(requests, timeout_seconds=args.timeout):
        print(f"--- {result.to}: {result.status}")
        if result.error:
            print(f"error: {result.error}")
        if result.summary:
            print(result.summary)
        for key, value in result.details.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(_main())
