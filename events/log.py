"""Event log: Postgres `events` is the system of record, Keewano is a fire-and-forget sink.

`log_event` is sync and never raises, so it is safe inside FastAPI handlers (sync or async) and
tests. The Keewano send is scheduled only when an event loop is running in the calling thread
(async handlers); sync handlers run in a threadpool without a loop and skip the sink silently.
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


def keewano_enabled() -> bool:
    enabled = os.environ.get("KEEWANO_ENABLED", "").strip().lower() in _TRUTHY
    return enabled and bool(os.environ.get("KEEWANO_TOKEN"))


def _on_done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if not task.cancelled():
        task.exception()  # retrieve so asyncio never logs "exception was never retrieved"


def _schedule_keewano(user_id: str, event: dict) -> None:
    try:
        if not keewano_enabled():
            return
        loop = asyncio.get_running_loop()
    except Exception:  # noqa: BLE001  (RuntimeError when no loop; anything else is also a skip)
        return
    try:
        task = loop.create_task(keewano.send_events(user_id, [event]))
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
        _schedule_keewano(user_id, event)
    return event
