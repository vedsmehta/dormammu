"""Keewano secondary sink. Postgres `events` is the system of record; this is fire-and-forget.

API ground truth (probed Oct 11 2026, 13:28-13:32, plus docs.keewano.com speedhacks-quickstart):

- Ingress:      POST https://api.keewano.com/event/ingress/v1/json/in       header `k-token: <Project API Key>`
- Registration: POST https://api.keewano.com/event/ingress/v1/json/custom   same header
  body {"customEventsHash": int, "eventTypes": [{"eventTypeId": 2001, "name": "...", "dataType": "string"}]}
  200 = already registered, 202 = accepted, 409 = a different list is registered under that hash.
  The hash is `zlib.crc32(json.dumps(eventTypes, sort_keys=True).encode()) or 1`. Append-only: never
  change a registered id's type.
- Ingress body: {dataSessionId (uuid, one per process), batch (int, starts at 1, monotonic per session),
  customEventsHash, userId (our profiles.id), sdkVersion, events: [{eventTypeId, dataType, timestamp
  (unix seconds), data}]}. For dataType "string" the `data` field is a plain string, so we JSON-encode
  the event payload into it. Keewano caps strings at 256 chars; every string value is truncated first.
- Auth: 401 "Missing API Key" when the header is absent, 403 "Invalid Api Key" when it is wrong.
  The KEEWANO_TOKEN currently in .env is a Personal Access Token (Settings > Personal > Security). It
  works for the MCP server but ingress returns 403 with it. Ingress needs a Project API Key
  (Settings > Project > API Keys). Swap KEEWANO_TOKEN for that key and this module works unchanged.
- The project's event catalog (via MCP list_event_types) has only built-in types (ids < 100). The
  built-in string-typed id 20 "Button Click" is the fallback when registration is rejected.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
import zlib
from datetime import datetime
from typing import Any

import httpx

logger = logging.getLogger("dormammu.keewano")

API_BASE = "https://api.keewano.com/event/ingress/v1/json"
INGRESS_URL = f"{API_BASE}/in"
CUSTOM_URL = f"{API_BASE}/custom"
SDK_VERSION = "0.0.0"
MAX_STRING = 256
TIMEOUT_SECONDS = 10.0

# Built-in string event used when custom registration is refused (BUILD-PLAN §6, Lane C abort rule).
FALLBACK_EVENT_TYPE_ID = 20  # "Button Click", dataType string

# Order is load-bearing: ids are assigned by position and must never change once registered.
EVENT_TYPES = [
    "run_requested",
    "call_dispatched",
    "call_simulated",
    "call_connected",
    "call_terminated",
    "outcome_extracted",
    "run_aggregated",
    "ledger_written",
]
EVENT_TYPE_IDS: dict[str, int] = {name: 2001 + i for i, name in enumerate(EVENT_TYPES)}
CUSTOM_EVENTS = [
    {"eventTypeId": type_id, "name": name, "dataType": "string"} for name, type_id in EVENT_TYPE_IDS.items()
]
CUSTOM_EVENTS_HASH = zlib.crc32(json.dumps(CUSTOM_EVENTS, sort_keys=True).encode()) or 1

DATA_SESSION_ID = str(uuid.uuid4())

_batch = 0
_registered = False
_use_builtin = False
_warned: set[str] = set()


def reset_state() -> None:
    """Test hook: forget registration, batch counter, fallback mode and warned errors."""
    global _batch, _registered, _use_builtin
    _batch = 0
    _registered = False
    _use_builtin = False
    _warned.clear()


def _token() -> str | None:
    return os.environ.get("KEEWANO_TOKEN") or None


def _warn_once(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    logger.warning("keewano: %s", message)


def _next_batch() -> int:
    global _batch
    _batch += 1
    return _batch


def truncate_strings(value: Any) -> Any:
    """Return a copy of `value` with every string (at any depth) cut to MAX_STRING chars."""
    if isinstance(value, str):
        return value[:MAX_STRING]
    if isinstance(value, dict):
        return {str(k)[:MAX_STRING]: truncate_strings(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [truncate_strings(v) for v in value]
    return value


def _timestamp(event: dict) -> int:
    raw = event.get("created_at")
    if isinstance(raw, datetime):
        return int(raw.timestamp())
    if isinstance(raw, str) and raw:
        try:
            return int(datetime.fromisoformat(raw).timestamp())
        except ValueError:
            pass
    return int(time.time())


def encode_event(event: dict) -> dict | None:
    """Map one of our events rows to a Keewano event object. Unknown types ride on the fallback id."""
    if not isinstance(event, dict):
        return None
    event_type = str(event.get("type") or "unknown")
    data = event.get("data")
    payload: dict[str, Any] = {"type": event_type}
    for key in ("run_id", "call_id"):
        if event.get(key):
            payload[key] = str(event[key])
    if isinstance(data, dict):
        payload.update(data)
    elif data is not None:
        payload["value"] = data
    payload = truncate_strings(payload)
    type_id = EVENT_TYPE_IDS.get(event_type)
    if type_id is None or _use_builtin:
        type_id = FALLBACK_EVENT_TYPE_ID
    return {
        "eventTypeId": type_id,
        "dataType": "string",
        "timestamp": _timestamp(event),
        "data": json.dumps(payload, separators=(",", ":"), default=str),
    }


async def _ensure_registered(client: httpx.AsyncClient, headers: dict[str, str]) -> None:
    """Register our custom event list once per process. Safe to repeat; failures flip the fallback."""
    global _registered, _use_builtin
    if _registered or _use_builtin:
        return
    body = {"customEventsHash": CUSTOM_EVENTS_HASH, "eventTypes": CUSTOM_EVENTS}
    response = await client.post(CUSTOM_URL, json=body, headers=headers)
    if response.status_code in (200, 202):
        _registered = True
        return
    if response.status_code in (400, 404, 409, 422):
        _use_builtin = True
        _warn_once(
            f"custom registration refused ({response.status_code}: {response.text[:200]}); "
            f"falling back to built-in event id {FALLBACK_EVENT_TYPE_ID}"
        )
        return
    _warn_once(f"custom registration {response.status_code}: {response.text[:200]}")


async def send_events(user_id: str, events: list[dict]) -> bool:
    """POST `events` (our events rows) to Keewano for `user_id`. Never raises; False on any failure."""
    try:
        token = _token()
        if not token or not user_id or not events:
            return False
        encoded = [e for e in (encode_event(ev) for ev in events) if e]
        if not encoded:
            return False
        headers = {"k-token": token, "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            await _ensure_registered(client, headers)
            if _use_builtin:
                for item in encoded:
                    item["eventTypeId"] = FALLBACK_EVENT_TYPE_ID
            body = {
                "dataSessionId": DATA_SESSION_ID,
                "batch": _next_batch(),
                "customEventsHash": 0 if _use_builtin else CUSTOM_EVENTS_HASH,
                "userId": str(user_id),
                "sdkVersion": SDK_VERSION,
                "events": encoded,
            }
            response = await client.post(INGRESS_URL, json=body, headers=headers)
        if 200 <= response.status_code < 300:
            return True
        _warn_once(f"ingress {response.status_code}: {response.text[:200]}")
        return False
    except Exception as exc:  # noqa: BLE001
        _warn_once(f"{type(exc).__name__}: {exc}")
        return False
