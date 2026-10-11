"""Async httpx client for the Dial REST API (https://api.getdial.ai/api/v1).

Ground truth, probed 2026-10-11 20:30 PT with the real key (phone numbers masked):

GET /account (200, no envelope)
    {id, email, role, callerEmail, createdAt, maxCallDurationSeconds: null,
     limits: {maxCallDurationSeconds: 300, maxConcurrentCalls: 2},
     apiKeys: [{id, label, preview, createdAt, lastUsedAt}]}
    -> this account allows 2 concurrent calls and 300s per call. 180s is safe.

GET /numbers (200)              -> {"numbers": [{id, number: "+1***", nickname, country, capabilities:
                                   ["sms","call"], callingEnabled, setupStatus: "ready", ...camelCase}]}
GET /calls (200)                -> {"calls": [Call]}        (filters: numberId, direction, since, contact)
GET /calls/{id} (200)           -> {"call": Call}
POST /calls (201/200)           -> {"call": Call}
POST /calls/{id}/stop (200)     -> {"call": Call}

Errors seen live:
    404 {"error": "Call does-not-exist not found on this account.", "code": "not_found"}
    400 {"error": {"formErrors": [], "fieldErrors": {"to": [...], "outboundInstruction": [...],
                   "fromNumber": ["Provide exactly one of fromNumber or fromNumberId."]}}}
        (validation errors carry NO "code" and "error" is a dict, not a string)
    429 expected shape {"error": "...", "code": "call_limit_reached"} (documented, not reproduced)

The call list on this account was EMPTY at probe time (three filter variants, all `{"calls": []}`),
so the Call shape below is taken from the installed dial_sdk 0.40.x `types.Call.from_api`, which
reads the wire JSON. It is camelCase on the wire; the SDK converts to snake_case, we do NOT.

Call (wire, camelCase):
    id, phoneNumberId, from, to, direction ("outbound"|"inbound"), duration (int seconds),
    transcript (str | null until transcription finishes, async after hangup),
    transcriptTurns ([{speaker, text, startMs, endMs}] | null),
    instruction, queuedAt, startedRingingAt, callStartedAt, cancelRequestedAt, terminatedAt,
    terminationType (completed|busy|no-answer|failed|canceled|null), failureReason,
    transferTo, transferredAt, createdAt,
    status: {state: "Queued"|"Ringing"|"In-Progress"|"Terminated"|"Unknown",
             terminationType: str|null, cancelRequested: bool, cancelPending: bool, label: str}

transcriptTurns[].speaker is "agent" (Dial's AI), "user" (the human called), or
"transfer_target" (the human after a cold transfer). The callee is "user" / "transfer_target".

This module returns the raw wire dicts with the single-key envelope removed: get_call/place_call/
stop_call return the Call dict, list_numbers returns the list, get_account the account dict.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from settings import env, load_env

BASE_URL = "https://api.getdial.ai/api/v1"
TIMEOUT_SECONDS = 20.0
USER_AGENT = "dormammu/0.1.0"
DEFAULT_MAX_DURATION_SECONDS = 180


class DialError(Exception):
    """Non-2xx response (or transport failure, status_code 0) from the Dial API."""

    def __init__(self, status_code: int, code: str | None, message: str) -> None:
        super().__init__(f"Dial {status_code} {code or '-'}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message

    @property
    def is_limit(self) -> bool:
        return self.status_code == 429 or self.code == "call_limit_reached"

    @classmethod
    def from_response(cls, response: httpx.Response) -> DialError:
        code: str | None = None
        message = response.text[:500]
        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            code = body.get("code") if isinstance(body.get("code"), str) else None
            error = body.get("error", body.get("message"))
            if isinstance(error, str):
                message = error
            elif isinstance(error, dict):
                # zod-style validation payload: {"formErrors": [], "fieldErrors": {field: [msgs]}}
                field_errors = error.get("fieldErrors") or {}
                parts = [f"{k}: {'; '.join(map(str, v))}" for k, v in field_errors.items()]
                parts += [str(m) for m in error.get("formErrors") or []]
                message = " | ".join(parts) or str(error)
        return cls(response.status_code, code, message)


def make_client(api_key: str | None = None) -> httpx.AsyncClient:
    """One short-lived client per request. No module-level connection state, so tests can
    monkeypatch this factory or intercept transport with pytest-httpx."""
    load_env()
    key = api_key or env("DIAL_API_KEY")
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
        timeout=TIMEOUT_SECONDS,
    )


async def _request(
    method: str,
    path: str,
    *,
    json: dict | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
) -> Any:
    try:
        async with make_client() as client:
            response = await client.request(method, path, json=json, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise DialError(0, "transport", f"{type(exc).__name__}: {exc}") from exc
    if response.is_error:
        raise DialError.from_response(response)
    if not response.content:
        return {}
    return response.json()


def _unwrap(payload: Any, key: str) -> Any:
    if isinstance(payload, dict) and key in payload:
        return payload[key]
    return payload


async def place_call(
    to: str,
    instruction: str,
    *,
    from_number: str | None = None,
    voice_gender: str = "female",
    max_duration_seconds: int = DEFAULT_MAX_DURATION_SECONDS,
    transfer_to: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """POST /calls. Returns the Call dict (envelope removed). Raises DialError on any non-2xx."""
    load_env()
    body: dict[str, Any] = {
        "to": to,
        "outboundInstruction": instruction,
        "fromNumber": from_number or env("DIAL_NUMBER"),
        "voiceGender": voice_gender,
        "maxCallDurationSeconds": int(max_duration_seconds),
    }
    if transfer_to:
        body["transferTo"] = transfer_to
    headers = {"Idempotency-Key": idempotency_key or str(uuid.uuid4())}
    return _unwrap(await _request("POST", "/calls", json=body, headers=headers), "call")


async def get_call(call_id: str) -> dict:
    return _unwrap(await _request("GET", f"/calls/{call_id}"), "call")


async def list_calls(**filters: str) -> list[dict]:
    params = {k: v for k, v in filters.items() if v is not None}
    return _unwrap(await _request("GET", "/calls", params=params or None), "calls") or []


async def get_account() -> dict:
    return await _request("GET", "/account")


async def list_numbers() -> list[dict]:
    return _unwrap(await _request("GET", "/numbers"), "numbers") or []


async def stop_call(call_id: str) -> dict:
    return _unwrap(await _request("POST", f"/calls/{call_id}/stop"), "call")
