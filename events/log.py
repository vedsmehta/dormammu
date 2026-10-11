"""STUB. Lane C replaces this file. Signature is the contract (docs/CONTRACT.md)."""

from __future__ import annotations

from typing import Any


def log_event(
    store: Any,
    user_id: str,
    type: str,
    data: dict | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
) -> dict:
    try:
        return store.insert_event(user_id, type, data or {}, run_id=run_id, call_id=call_id)
    except Exception:  # noqa: BLE001
        return {"type": type, "data": data or {}}
