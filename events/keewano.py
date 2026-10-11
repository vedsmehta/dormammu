"""Keewano secondary sink. Postgres `events` is the system of record; this is fire-and-forget.

API ground truth (probed Oct 11 2026, 13:28-13:32 and again ~22:00, plus docs.keewano.com speedhacks-quickstart):

- Ingress:      POST https://api.keewano.com/event/ingress/v1/json/in       header `k-token: <Project API Key>`
- Registration: POST https://api.keewano.com/event/ingress/v1/json/custom   same header
  body {"customEventsHash": int, "eventTypes": [{"eventTypeId": 2001, "name": "...", "dataType": "string"}]}
  200 = already registered, 202 = accepted, 409 = a different list is registered under that hash.
  The hash is `zlib.crc32(json.dumps(eventTypes, sort_keys=True).encode()) or 1`. Append-only: never
  change a registered id's type.
- Ingress body: {dataSessionId (uuid, one per process), batch (int, starts at 1, monotonic per session),
  customEventsHash, userId (our profiles.id), sdkVersion, events: [{eventTypeId, dataType, timestamp
  (unix seconds), data}]}. For dataType "string" the `data` field is a plain string, so we JSON-encode
  the event payload into it; for "uint" it is a number. Keewano caps strings at 256 chars; every string
  value is truncated first.
- Auth: 401 "Missing API Key" when the header is absent, 403 "Invalid Api Key" when it is wrong.
  The KEEWANO_TOKEN currently in .env is a Personal Access Token (Settings > Personal > Security). It
  works for the MCP server but both /custom and /in return 403 with it. Ingress needs a Project API Key
  (Settings > Project > API Keys). Swap KEEWANO_TOKEN for that key and this module works unchanged.
  On 401/403 this module logs one warning naming that cause, skips the ingress POST (same key, same
  answer) and leaves the reason in `last_error`; `scripts/keewano_check.py` prints it.
- The project's event catalog (via MCP list_event_types) has only built-in types (ids < 100). The
  built-in string-typed id 20 "Button Click" is the fallback when registration is rejected.

Event ids. 2001-2008 are the lifecycle events, one JSON blob each (the full record). 2009-2016 are
typed uint companions emitted right before the blob they describe, same batch, same timestamp, only
when the source row carries the field. Keewano SQL groups and aggregates the companions; it cannot
read inside the blob. See docs/KEEWANO.md.
"""

from __future__ import annotations

import json
import logging
import math
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
UINT_MAX = 2**32 - 1
TIMEOUT_SECONDS = 10.0

# Built-in string event used when custom registration is refused (BUILD-PLAN §6, Lane C abort rule).
FALLBACK_EVENT_TYPE_ID = 20  # "Button Click", dataType string

# What a 401/403 from Keewano almost certainly means for this repo (verified Oct 11 2026).
AUTH_HINT = (
    "KEEWANO_TOKEN looks like a personal access token; ingest needs a Project API Key "
    "(Keewano console: Settings > Project > API Keys)"
)

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

# Typed companions (dataType uint), appended after the lifecycle ids. Same rule: append only.
COMPANION_TYPES = [
    "call_track",  # 2009: TRACK_CODES
    "call_vendor_key",  # 2010: vendor_key_hash(vendor_key); the key string stays in the blob
    "call_simulated",  # 2011: 0 live, 1 simulated
    "call_termination",  # 2012: TERMINATION_CODES
    "call_duration_seconds",  # 2013
    "outcome_amount_usd_cents",  # 2014: quote amount_usd, or negotiate saved_usd, in cents
    "run_wall_clock_seconds",  # 2015
    "run_multiplier_x100",  # 2016: parallel speedup times 100
]
COMPANION_TYPE_IDS: dict[str, int] = {
    name: 2001 + len(EVENT_TYPES) + i for i, name in enumerate(COMPANION_TYPES)
}
TRACK_CODES = {"quote": 1, "schedule": 2, "negotiate": 3}
TERMINATION_CODES = {"completed": 1, "voicemail": 2, "no-answer": 3, "busy": 4, "failed": 5, "canceled": 6}

CUSTOM_EVENTS = [
    *({"eventTypeId": type_id, "name": name, "dataType": "string"} for name, type_id in EVENT_TYPE_IDS.items()),
    *({"eventTypeId": type_id, "name": name, "dataType": "uint"} for name, type_id in COMPANION_TYPE_IDS.items()),
]
CUSTOM_EVENTS_HASH = zlib.crc32(json.dumps(CUSTOM_EVENTS, sort_keys=True).encode()) or 1

DATA_SESSION_ID = str(uuid.uuid4())

_batch = 0
_registered = False
_use_builtin = False
_warned: set[str] = set()

# Read-only status for health checks and scripts/keewano_check.py. `last_error` is None after a
# successful send; `last_status` and `last_body` describe the most recent Keewano HTTP response.
last_error: str | None = None
last_status: int | None = None
last_body: str | None = None


def reset_state() -> None:
    """Test hook: forget registration, batch counter, fallback mode, warned errors and last status."""
    global _batch, _registered, _use_builtin, last_error, last_status, last_body
    _batch = 0
    _registered = False
    _use_builtin = False
    _warned.clear()
    last_error = None
    last_status = None
    last_body = None


def status() -> dict[str, Any]:
    """Snapshot for a status check. Never includes the token itself."""
    return {
        "token_present": bool(_token()),
        "registered": _registered,
        "use_builtin": _use_builtin,
        "batch": _batch,
        "data_session_id": DATA_SESSION_ID,
        "last_status": last_status,
        "last_error": last_error,
    }


def _token() -> str | None:
    return os.environ.get("KEEWANO_TOKEN") or None


def _warn_once(message: str) -> None:
    if message in _warned:
        return
    _warned.add(message)
    logger.warning("keewano: %s", message)


def _fail(message: str) -> None:
    """Record the failure for status readers and warn about it once."""
    global last_error
    last_error = message
    _warn_once(message)


def _record(response: httpx.Response) -> None:
    global last_status, last_body
    last_status = response.status_code
    last_body = response.text[:200]


def _describe(where: str, response: httpx.Response) -> str:
    text = f"{where} {response.status_code}: {response.text[:200]}"
    if response.status_code in (401, 403):
        text = f"{text}; {AUTH_HINT}"
    return text


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
    """Map one of our events rows to its Keewano blob event. Unknown types ride on the fallback id."""
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


# -- typed companions -------------------------------------------------------------------------


def vendor_key_hash(vendor_key: str) -> int:
    """Stable 32-bit id for a vendor_key (crc32, never 0). Keewano cannot map it back; this function can."""
    return zlib.crc32(str(vendor_key).encode("utf-8")) or 1


def _uint(value: Any, scale: float = 1.0) -> int | None:
    """Round a non-negative number into Keewano's uint range. None for anything else: never invent."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value) or value < 0:
        return None
    return min(round(value * scale), UINT_MAX)


def _code(table: dict[str, int], value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    return table.get(value.strip().lower().replace("_", "-"))


def companion_values(event: dict) -> list[tuple[str, int]]:
    """(companion name, uint value) pairs for one events row, in emission order.

    Only fields the row's data actually carries produce a value. Unknown strings and non-numeric
    values produce nothing.
    """
    data = event.get("data") if isinstance(event, dict) else None
    if not isinstance(data, dict):
        return []
    out: list[tuple[str, int]] = []
    track = _code(TRACK_CODES, data.get("track"))
    if track is not None:
        out.append(("call_track", track))
    vendor = data.get("vendor_key")
    if isinstance(vendor, str) and vendor:
        out.append(("call_vendor_key", vendor_key_hash(vendor)))
    simulated = data.get("simulated")
    if isinstance(simulated, bool) or (isinstance(simulated, int) and simulated in (0, 1)):
        out.append(("call_simulated", int(bool(simulated))))
    termination = _code(TERMINATION_CODES, data.get("termination_type"))
    if termination is not None:
        out.append(("call_termination", termination))
    duration = _uint(data.get("duration_seconds"))
    if duration is not None:
        out.append(("call_duration_seconds", duration))
    outcome = data.get("outcome")
    if isinstance(outcome, dict):
        amount = _uint(outcome.get("amount_usd"), 100)
        if amount is None:
            amount = _uint(outcome.get("saved_usd"), 100)
        if amount is not None:
            out.append(("outcome_amount_usd_cents", amount))
    wall_clock = _uint(data.get("wall_clock_seconds"))
    if wall_clock is not None:
        out.append(("run_wall_clock_seconds", wall_clock))
    multiplier = _uint(data.get("multiplier"), 100)
    if multiplier is not None:
        out.append(("run_multiplier_x100", multiplier))
    return out


def encode_companions(event: dict, timestamp: int) -> list[dict]:
    return [
        {"eventTypeId": COMPANION_TYPE_IDS[name], "dataType": "uint", "timestamp": timestamp, "data": value}
        for name, value in companion_values(event)
    ]


def encode_batch(events: list[dict]) -> list[dict]:
    """Keewano event objects for our rows: each row's typed companions, then its blob.

    In built-in fallback mode the companions are dropped (no built-in uint id means anything to us)
    and every blob rides on FALLBACK_EVENT_TYPE_ID; the blob still carries every field.
    """
    out: list[dict] = []
    for event in events:
        blob = encode_event(event)
        if not blob:
            continue
        if _use_builtin:
            blob["eventTypeId"] = FALLBACK_EVENT_TYPE_ID
        else:
            out.extend(encode_companions(event, blob["timestamp"]))
        out.append(blob)
    return out


# -- network ------------------------------------------------------------------------------------


async def _ensure_registered(client: httpx.AsyncClient, headers: dict[str, str]) -> bool:
    """Register our custom event list once per process. Returns False only when the key is rejected.

    Refusals of the list itself (400/404/409/422) flip the built-in fallback and still return True.
    """
    global _registered, _use_builtin
    if _registered or _use_builtin:
        return True
    body = {"customEventsHash": CUSTOM_EVENTS_HASH, "eventTypes": CUSTOM_EVENTS}
    response = await client.post(CUSTOM_URL, json=body, headers=headers)
    _record(response)
    if response.status_code in (200, 202):
        _registered = True
        return True
    if response.status_code in (401, 403):
        _fail(_describe("custom registration", response))
        return False
    if response.status_code in (400, 404, 409, 422):
        _use_builtin = True
        _warn_once(
            f"custom registration refused ({response.status_code}: {response.text[:200]}); "
            f"falling back to built-in event id {FALLBACK_EVENT_TYPE_ID}"
        )
        return True
    _warn_once(f"custom registration {response.status_code}: {response.text[:200]}")
    return True


async def send_events(user_id: str, events: list[dict]) -> bool:
    """POST `events` (our events rows) to Keewano for `user_id`. Never raises; False on any failure."""
    global last_error
    try:
        token = _token()
        if not token or not user_id or not events:
            return False
        rows = [ev for ev in events if isinstance(ev, dict)]
        if not rows:
            return False
        headers = {"k-token": token, "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            if not await _ensure_registered(client, headers):
                return False
            body = {
                "dataSessionId": DATA_SESSION_ID,
                "batch": _next_batch(),
                "customEventsHash": 0 if _use_builtin else CUSTOM_EVENTS_HASH,
                "userId": str(user_id),
                "sdkVersion": SDK_VERSION,
                "events": encode_batch(rows),
            }
            response = await client.post(INGRESS_URL, json=body, headers=headers)
        _record(response)
        if 200 <= response.status_code < 300:
            last_error = None
            return True
        _fail(_describe("ingress", response))
        return False
    except Exception as exc:  # noqa: BLE001
        _fail(f"{type(exc).__name__}: {exc}")
        return False
