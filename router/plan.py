"""STUB. Lane D replaces this file. Signature is the contract (docs/CONTRACT.md)."""

from __future__ import annotations

import hashlib
import os
from typing import Any


def request_hash(request: str) -> str:
    return hashlib.sha256(request.strip().lower().encode()).hexdigest()


def fallback_plans(vendors: list[dict], request: str) -> list[dict]:
    cap = int(os.environ.get("MAX_CALLS_PER_RUN", "5"))
    return [
        {"vendor_key": v["key"], "track": v["track"], "objective": request, "voice_gender": "female"}
        for v in vendors[:cap]
    ]


def prior_context(store: Any, user_id: str, vendor_key: str, track: str) -> str | None:
    return None


async def plan(store: Any, request: str, user_id: str) -> list[dict]:
    return fallback_plans(store.list_vendors(user_id), request)
