"""Send SMS from Dial numbers and wait for replies.

uv run python -m sms.messenger --to +14155550123 --body "Any openings Friday?" --wait
"""

import argparse
import asyncio
from datetime import UTC, datetime

from dial_sdk import DialClient, DialConfig, Message, SendMessageParams

from settings import env, load_env

load_env()


def dial_client() -> DialClient:
    return DialClient(DialConfig(api_key=env("DIAL_API_KEY"), user_agent="dormammu/0.1.0"))


def dial_number() -> str:
    return env("DIAL_NUMBER")


async def send_sms(dial: DialClient, to: str, body: str, from_number: str | None = None) -> Message:
    return await dial.send_message(
        SendMessageParams(to=to, body=body, from_number=from_number or dial_number())
    )


async def send_many(dial: DialClient, recipients: list[str], body: str) -> list[Message | Exception]:
    return await asyncio.gather(*(send_sms(dial, to, body) for to in recipients), return_exceptions=True)


async def wait_for_reply(
    dial: DialClient,
    contact: str,
    since: datetime,
    timeout_seconds: float = 600.0,
    poll_seconds: float = 3.0,
) -> Message | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        replies = await dial.list_messages(direction="inbound", contact=contact, since=since.isoformat())
        if replies:
            return replies[0]
        await asyncio.sleep(poll_seconds)
    return None


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Send an SMS from your Dial number")
    parser.add_argument("--to", required=True, help="E.164 number")
    parser.add_argument("--body", required=True)
    parser.add_argument("--wait", action="store_true", help="wait for the first reply")
    args = parser.parse_args()

    dial = dial_client()
    try:
        sent_at = datetime.now(UTC)
        message = await send_sms(dial, args.to, args.body)
        print(f"sent {message.id} ({message.status})")
        if args.wait:
            reply = await wait_for_reply(dial, args.to, sent_at)
            print(f"reply: {reply.body}" if reply else "no reply")
    finally:
        await dial.close()


if __name__ == "__main__":
    asyncio.run(_main())
