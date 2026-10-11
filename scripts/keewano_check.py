"""Keewano ingest check: send ONE labeled test event and print exactly what Keewano answered.

    uv run python scripts/keewano_check.py

Reads KEEWANO_TOKEN from .env (settings.load_env). Prints the token fingerprint (first 6 characters,
the rest masked), what was sent, the HTTP status and body of the last Keewano response, and
events.keewano's last_error. Exit 0 when Keewano accepted the batch, 1 when it refused, 2 when no
token is set.

The batch carries two Keewano events for user 00000000-0000-0000-0000-000000000001 (override with
KEEWANO_CHECK_USER): one typed companion, call_track = 1 (id 2009, "quote"), and the check event
itself. The check event's type "keewano_check" is not one of our registered custom ids, so it rides
on the built-in fallback id 20 ("Button Click") with {"type": "keewano_check", ...} as its data.
In Keewano SQL afterwards:
    SELECT count(call_track) AS companions, count("Button Click") AS checks FROM events
    WHERE user_id = '00000000-0000-0000-0000-000000000001'
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from events import keewano
from settings import load_env

DEFAULT_USER = "00000000-0000-0000-0000-000000000001"
CUSTOM_ID_RANGE = f"{min(keewano.EVENT_TYPE_IDS.values())}-{max(keewano.COMPANION_TYPE_IDS.values())}"
NAMES_BY_ID = {
    **{type_id: name for name, type_id in keewano.EVENT_TYPE_IDS.items()},
    **{type_id: name for name, type_id in keewano.COMPANION_TYPE_IDS.items()},
    keewano.FALLBACK_EVENT_TYPE_ID: "Button Click (built-in fallback)",
}


def fingerprint(token: str) -> str:
    if not token:
        return "(unset)"
    return f"{token[:6]}{'*' * min(12, max(0, len(token) - 6))} ({len(token)} chars)"


def build_event(user_id: str) -> dict:
    now = datetime.now(UTC)
    return {
        "id": 0,
        "user_id": user_id,
        "run_id": None,
        "call_id": None,
        "type": "keewano_check",
        "data": {
            "source": "scripts/keewano_check.py",
            "track": "quote",  # yields the call_track = 1 companion
            "nonce": uuid.uuid4().hex[:12],
            "sent_at": now.isoformat(),
        },
        "created_at": now.isoformat(),
    }


def describe(encoded: list[dict]) -> str:
    parts = []
    for item in encoded:
        name = NAMES_BY_ID.get(item["eventTypeId"], "?")
        value = item["data"] if item["dataType"] != "string" else "<json blob>"
        parts.append(f"{item['eventTypeId']} {name} = {value}")
    return "; ".join(parts)


def registration_label(snapshot: dict) -> str:
    if snapshot["registered"]:
        return f"ok (custom ids {CUSTOM_ID_RANGE} accepted)"
    if snapshot["use_builtin"]:
        return f"refused, using built-in id {keewano.FALLBACK_EVENT_TYPE_ID} and no typed companions"
    if keewano.last_status in (401, 403):
        return f"rejected (HTTP {keewano.last_status}, key not accepted)"
    return "not registered"


async def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    load_env()
    token = os.environ.get("KEEWANO_TOKEN", "")
    enabled = os.environ.get("KEEWANO_ENABLED") or "(unset)"
    user_id = os.environ.get("KEEWANO_CHECK_USER") or DEFAULT_USER
    print(f"KEEWANO_ENABLED = {enabled}")
    print(f"KEEWANO_TOKEN   = {fingerprint(token)}")
    if not token:
        print("FAIL: KEEWANO_TOKEN is not set in .env")
        return 2
    if enabled.strip().lower() not in {"1", "true", "yes", "on"}:
        print("note: KEEWANO_ENABLED is not true, so the server skips the sink; this check sends anyway")

    keewano.reset_state()
    event = build_event(user_id)
    print(f"sending         = {describe(keewano.encode_batch([event]))}")
    ok = await keewano.send_events(user_id, [event])
    snapshot = keewano.status()

    print(f"registration    = {registration_label(snapshot)}")
    print(f"last response   = HTTP {keewano.last_status} {keewano.last_body or ''}".rstrip())
    print(f"last_error      = {keewano.last_error}")
    if ok:
        sent = 1 if snapshot["use_builtin"] else 2
        print(f"OK: Keewano accepted {sent} event(s) for user {user_id} (batch {snapshot['batch']}).")
        return 0
    print("FAIL: Keewano refused the batch. See last_error above and docs/KEEWANO.md for the fix.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
