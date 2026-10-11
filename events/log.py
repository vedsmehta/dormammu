"""Event log: Postgres `events` is the system of record, Keewano is a fire-and-forget sink.

`log_event` is sync and never raises, so it is safe inside FastAPI handlers (sync or async) and
tests. The Keewano send is scheduled only when an event loop is running in the calling thread
(async handlers); sync handlers run in a threadpool without a loop and skip the sink silently.

The Keewano copy of a call-scoped event is enriched with the call's `track`, `vendor_key` and
`simulated` when the row's data lacks them. The lookup runs inside the background task, never on
the caller's path, and the Postgres row is stored exactly as the caller passed it. events/keewano.py
turns those fields into typed companion events, so a duration or an amount is attributed to the
right vendor even when one user has three calls in flight.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

from events import keewano

logger = logging.getLogger("dormammu.events")

_TRUTHY = {"1", "true", "yes", "on"}
_TASKS: set[asyncio.Task] = set()

CALL_CONTEXT_FIELDS = ("track", "vendor_key", "simulated")


def keewano_enabled() -> bool:
    enabled = os.environ.get("KEEWANO_ENABLED", "").strip().lower() in _TRUTHY
    return enabled and bool(os.environ.get("KEEWANO_TOKEN"))


def _on_done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if not task.cancelled():
        task.exception()  # retrieve so asyncio never logs "exception was never retrieved"


def _missing_call_context(event: dict) -> bool:
    data = event.get("data")
    if not event.get("call_id") or not isinstance(data, dict):
        return False
    return any(key not in data for key in CALL_CONTEXT_FIELDS)


def with_call_context(event: dict, call_row: Any) -> dict:
    """Copy of `event` whose data also carries the call's track, vendor_key and simulated flag where missing."""
    if not isinstance(call_row, dict):
        return event
    data = dict(event.get("data") or {})
    for key in CALL_CONTEXT_FIELDS:
        if key not in data and call_row.get(key) is not None:
            data[key] = call_row[key]
    return {**event, "data": data}


async def _send(store: Any, user_id: str, event: dict) -> bool:
    if _missing_call_context(event):
        try:
            row = await asyncio.to_thread(store.get_call, event["call_id"])
            event = with_call_context(event, row)
        except Exception as exc:  # noqa: BLE001
            logger.debug("call context lookup skipped for %s: %s", event.get("type"), exc)
    return await keewano.send_events(user_id, [event])


def _schedule_keewano(store: Any, user_id: str, event: dict) -> None:
    try:
        if not keewano_enabled():
            return
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001  (RuntimeError when no loop; anything else is also a skip)
        return
    try:
        task = loop.create_task(_send(store, user_id, event))
        _TASKS.add(task)
        task.add_done_callback(_on_done)
    except Exception as exc:  # noqa: BLE001
        logger.debug("keewano schedule skipped: %s", exc)


def log_event(
    store: Any,
    user_id: str,
    type: str,
    data: dict | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
) -> dict:
    """Insert the event row (always first), then fire-and-forget to Keewano. Never raises."""
    payload = dict(data) if isinstance(data, dict) else {}
    event: dict
    try:
        event = store.insert_event(user_id, type, payload, run_id=run_id, call_id=call_id)
        if not isinstance(event, dict):
            event = {"user_id": user_id, "type": type, "data": payload, "run_id": run_id, "call_id": call_id}
    except Exception as exc:  # noqa: BLE001
        logger.warning("insert_event failed for %s: %s", type, exc)
        event = {"user_id": user_id, "type": type, "data": payload, "run_id": run_id, "call_id": call_id}
    with contextlib.suppress(Exception):
        _schedule_keewano(store, user_id, event)
    return event
