"""FastAPI service. Every route lives under /api/py (docs/CONTRACT.md, BUILD-PLAN §4.5-§4.7, §4.10, §12).

Local: `uv run uvicorn server.app:app --port 8000 --reload`. Vercel: api/index.py re-exports `app`.
Cross-lane modules (call.*, router.*, aggregator.*, events.*) are imported by module so tests can patch
their attributes; aggregator.ledger is optional and imported lazily.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aggregator import aggregate as aggregate_mod
from aggregator import extract as extract_mod
from call import dial_client, dispatch, ingest, simulate
from events.log import log_event
from router import plan as planner
from server import store as store_mod
from server.auth import bearer_token, current_user_id, ensure_profile
from server.schemas import JsonRpcRequest, LedgerFire, ProfileUpdate, RunCreate, VendorsIn
from server.store import TERMINAL_STATES, Store
from settings import load_env

load_env()

PREFIX = "/api/py"
STALE_PLANNED_SECONDS = 90.0

_store: Store | None = None


def set_store(store: Store | None) -> None:
    """Inject a store (tests, or a custom backend). None resets to get_store() on next use."""
    global _store
    _store = store


def get_store_dep() -> Store:
    global _store
    if _store is None:
        _store = store_mod.get_store()
    return _store


UserId = Annotated[str, Depends(current_user_id)]
StoreDep = Annotated[Store, Depends(get_store_dep)]


def utcnow() -> datetime:
    return datetime.now(UTC)


def _is_terminal(row: dict) -> bool:
    return row.get("state") in TERMINAL_STATES


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _record_error(store: Store, call_id: str, exc: BaseException) -> None:
    message = f"{exc.__class__.__name__}: {exc}"[:500]
    with contextlib.suppress(Exception):
        store.update_call(call_id, {"error": message})


def _join_turns(turns: Any) -> str:
    if isinstance(turns, str):
        return turns
    if isinstance(turns, list):
        return "\n".join(
            f"{t.get('speaker', '?')}: {t.get('text', '')}" for t in turns if isinstance(t, dict)
        )
    return str(turns)


def _safe_aggregate(run: dict) -> dict | None:
    try:
        return aggregate_mod.aggregate(run)
    except Exception:  # noqa: BLE001
        return None


def _assemble(store: Store, run_id: str, user_id: str | None, now: datetime) -> dict:
    run = store.get_run(run_id, user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    run["aggregate"] = _safe_aggregate(run)
    run["server_now"] = now.isoformat()
    return run


app = FastAPI(title="Dormammu", docs_url=f"{PREFIX}/docs", openapi_url=f"{PREFIX}/openapi.json")


@app.middleware("http")
async def no_store_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------------------------
# Health, account, profile, vendors
# ---------------------------------------------------------------------------------------------


@app.get(f"{PREFIX}/health")
def health(store: StoreDep) -> dict:
    return {"ok": True, "store": getattr(store, "name", "sqlite")}


@app.get(f"{PREFIX}/account")
async def account(user_id: UserId) -> dict:
    try:
        return await dial_client.get_account()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"dial account lookup failed: {exc}") from exc


@app.get(f"{PREFIX}/profile")
def get_profile(user_id: UserId, store: StoreDep) -> dict:
    return ensure_profile(store, user_id)


@app.post(f"{PREFIX}/profile")
def post_profile(body: ProfileUpdate, user_id: UserId, store: StoreDep) -> dict:
    ensure_profile(store, user_id)
    return store.upsert_profile(user_id, body.model_dump(exclude_none=True))


@app.get(f"{PREFIX}/vendors")
def get_vendors(user_id: UserId, store: StoreDep) -> list[dict]:
    return store.list_vendors(user_id)


@app.post(f"{PREFIX}/vendors")
def post_vendors(body: VendorsIn, user_id: UserId, store: StoreDep) -> list[dict]:
    ensure_profile(store, user_id)
    return store.upsert_vendors(user_id, [v.model_dump() for v in body.vendors])


# ---------------------------------------------------------------------------------------------
# Runs: dispatch (§4.5), tick (§4.6 + §4.7), read
# ---------------------------------------------------------------------------------------------


def _safe_prior_context(store: Store, user_id: str, vendor_key: str, track: str) -> str | None:
    try:
        return planner.prior_context(store, user_id, vendor_key, track)
    except Exception:  # noqa: BLE001
        return None


async def _dispatch(store: Store, user_id: str, run: dict, calls: list[dict]) -> None:
    """Await the fan-out. dispatch_run never raises by contract; guard anyway so the run still returns."""
    profile = store.get_profile(user_id)
    try:
        await dispatch.dispatch_run(store, run, calls, profile)
    except Exception as exc:  # noqa: BLE001
        for row in calls:
            _record_error(store, row["id"], exc)
    store.set_run_status(run["id"], "running")


@app.get(f"{PREFIX}/runs")
def get_runs(user_id: UserId, store: StoreDep) -> list[dict]:
    return store.list_runs(user_id)


@app.post(f"{PREFIX}/runs")
async def post_run(body: RunCreate, user_id: UserId, store: StoreDep) -> dict:
    request_text = body.request.strip()
    if not request_text:
        raise HTTPException(status_code=400, detail="request is empty")
    ensure_profile(store, user_id)
    vendors = store.list_vendors(user_id)
    if not vendors:
        raise HTTPException(
            status_code=400, detail="no vendors on file; add vendors first (POST /api/py/vendors)"
        )
    by_key = {v["key"]: v for v in vendors}

    plans = await planner.plan(store, request_text, user_id)
    resolved: list[tuple[dict, dict]] = []
    seen: set[str] = set()
    for plan in plans or []:
        key = plan.get("vendor_key")
        if key in by_key and key not in seen:
            seen.add(key)
            resolved.append((plan, by_key[key]))
    if not resolved:
        raise HTTPException(
            status_code=400,
            detail=f"the router produced no plan that matches your vendors (known keys: {sorted(by_key)})",
        )
    cap = int(os.environ.get("MAX_CALLS_PER_RUN", "5"))
    resolved = resolved[:cap]

    run = store.create_run(user_id, request_text)
    rows = []
    for plan, vendor in resolved:
        track = plan.get("track") or vendor["track"]
        rows.append(
            {
                "run_id": run["id"],
                "vendor_id": vendor["id"],
                "vendor_key": vendor["key"],
                "track": track,
                "target_name": vendor["name"],
                "target_phone": vendor["phone"],
                "objective": plan.get("objective") or request_text,
                "prior_context": _safe_prior_context(store, user_id, vendor["key"], track),
                "state": "planned",
                "state_rank": 0,
                "simulated": False,
            }
        )
    calls = store.insert_calls(rows)
    for row, (plan, _vendor) in zip(calls, resolved):
        row["voice_gender"] = plan.get("voice_gender") or "female"
    log_event(
        store,
        user_id,
        "run_requested",
        {"request": request_text, "n_calls": len(calls), "vendor_keys": [r["vendor_key"] for r in calls]},
        run_id=run["id"],
    )
    await _dispatch(store, user_id, run, calls)
    return _assemble(store, run["id"], user_id, utcnow())


@app.get(f"{PREFIX}/runs/{{run_id}}")
def get_run(run_id: str, user_id: UserId, store: StoreDep) -> dict:
    return _assemble(store, run_id, user_id, utcnow())


async def _fetch_dial(row: dict) -> dict:
    return await dial_client.get_call(row["dial_call_id"])


async def _extract_one(
    store: Store, user_id: str, row: dict, transcript: Any, payload: dict | None, now: datetime
) -> None:
    if not store.claim_extraction(row["id"]):
        return
    track = row.get("track") or "quote"
    try:
        outcome = await extract_mod.extract(track, transcript)
    except Exception as exc:  # noqa: BLE001
        outcome = extract_mod.empty_outcome(track, f"extraction failed: {exc.__class__.__name__}")
    if not isinstance(outcome, dict):
        outcome = extract_mod.empty_outcome(track, "extractor returned no outcome")
    fields: dict[str, Any] = {"outcome": outcome}
    if not row.get("transcript"):
        fields["transcript"] = _join_turns(transcript)
    turns = (payload or {}).get("transcriptTurns")
    if turns and not row.get("transcript_turns"):
        fields["transcript_turns"] = turns
    store.update_call(row["id"], fields)
    store.update_call_state(row["id"], "done", 6, {"ended_at": row.get("ended_at") or now.isoformat()})
    log_event(
        store,
        user_id,
        "outcome_extracted",
        {"track": track, "outcome": outcome, "simulated": bool(row.get("simulated"))},
        run_id=row["run_id"],
        call_id=row["id"],
    )


def _complete_run(store: Store, user_id: str, run_id: str, now: datetime) -> None:
    store.set_run_status(run_id, "complete", completed_at=now.isoformat())
    full = store.get_run(run_id) or {"id": run_id, "calls": []}
    agg = _safe_aggregate(full) or {}
    summary_keys = (
        "n_calls",
        "n_completed",
        "n_failed",
        "n_simulated",
        "n_live",
        "wall_clock_seconds",
        "talk_seconds",
        "sequential_estimate_seconds",
        "multiplier",
    )
    log_event(store, user_id, "run_aggregated", {k: agg.get(k) for k in summary_keys}, run_id=run_id)

    try:
        from aggregator.ledger import ledger_rows
    except ImportError:
        return
    rows: list[dict] = []
    for call in full.get("calls", []):
        if call.get("state") != "done" or not call.get("outcome"):
            continue
        try:
            produced = ledger_rows(call) or []
        except Exception:  # noqa: BLE001
            produced = []
        for item in produced:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["user_id"] = user_id
            row["vendor_id"] = call.get("vendor_id")
            row.setdefault("source_call", call["id"])
            if call.get("simulated") and row.get("label") and "(simulated)" not in row["label"]:
                row["label"] = f"{row['label']} (simulated)"
            rows.append(row)
    if not rows:
        return
    try:
        inserted = store.insert_ledger(rows)
    except Exception as exc:  # noqa: BLE001
        log_event(store, user_id, "ledger_written", {"n": 0, "error": str(exc)[:300]}, run_id=run_id)
        return
    log_event(
        store,
        user_id,
        "ledger_written",
        {
            "n": len(inserted),
            "kinds": [r.get("kind") for r in inserted],
            "ledger_ids": [r.get("id") for r in inserted],
        },
        run_id=run_id,
    )


async def _tick(store: Store, user_id: str, run: dict, now: datetime) -> None:
    run_id = run["id"]
    calls = store.list_calls(run_id)

    live = [c for c in calls if not _is_terminal(c) and not c.get("simulated") and c.get("dial_call_id")]
    payloads = await asyncio.gather(*(_fetch_dial(c) for c in live), return_exceptions=True)
    fetched: dict[str, dict] = {}
    for row, payload in zip(live, payloads):
        if isinstance(payload, BaseException):
            _record_error(store, row["id"], payload)
            continue
        if not isinstance(payload, dict):
            continue
        fetched[row["id"]] = payload
        try:
            ingest.ingest_call_update(store, row, payload)
        except Exception as exc:  # noqa: BLE001
            _record_error(store, row["id"], exc)

    for row in calls:
        if _is_terminal(row):
            continue
        if row.get("simulated"):
            try:
                simulate.advance_simulated(store, row, now)
            except Exception as exc:  # noqa: BLE001
                _record_error(store, row["id"], exc)
        elif row.get("state") == "planned" and not row.get("dial_call_id"):
            started = _parse_ts(row.get("started_at"))
            if started and (now - started).total_seconds() > STALE_PLANNED_SECONDS:
                store.update_call_state(
                    row["id"], "failed", 6, {"error": "never dispatched", "ended_at": now.isoformat()}
                )

    for row in store.list_calls(run_id):
        if row.get("state") != "extracting" or row.get("simulated"):
            continue
        payload = fetched.get(row["id"])
        transcript = (payload or {}).get("transcript") or row.get("transcript")
        if not transcript:
            continue
        try:
            await _extract_one(store, user_id, row, transcript, payload, now)
        except Exception as exc:  # noqa: BLE001
            _record_error(store, row["id"], exc)

    calls = store.list_calls(run_id)
    if calls and all(_is_terminal(c) for c in calls):
        fresh = store.get_run(run_id)
        if fresh and fresh.get("status") != "complete":
            try:
                _complete_run(store, user_id, run_id, now)
            except Exception as exc:  # noqa: BLE001
                log_event(store, user_id, "run_aggregated", {"error": str(exc)[:300]}, run_id=run_id)


@app.post(f"{PREFIX}/runs/{{run_id}}/tick")
async def tick_run(run_id: str, user_id: UserId, store: StoreDep) -> dict:
    run = store.get_run(run_id, user_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    now = utcnow()
    await _tick(store, user_id, run, now)
    return _assemble(store, run_id, user_id, now)


# ---------------------------------------------------------------------------------------------
# Ledger and events
# ---------------------------------------------------------------------------------------------


@app.get(f"{PREFIX}/ledger")
def get_ledger(user_id: UserId, store: StoreDep) -> list[dict]:
    return store.list_ledger(user_id)


@app.get(f"{PREFIX}/events")
def get_events(user_id: UserId, store: StoreDep, limit: int = 50) -> list[dict]:
    return store.list_events(user_id, max(1, min(limit, 500)))


def _money(amount: Any) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "the rate"
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


@app.post(f"{PREFIX}/ledger/fire")
async def fire_ledger(body: LedgerFire, user_id: UserId, store: StoreDep) -> dict:
    ensure_profile(store, user_id)
    due = store.due_ledger(user_id, body.as_of)
    if not due:
        raise HTTPException(status_code=400, detail=f"no ledger rows are due as of {body.as_of[:10]}")
    vendors = {v["id"]: v for v in store.list_vendors(user_id)}
    by_vendor: dict[str, list[dict]] = {}
    for row in due:
        vid = row.get("vendor_id")
        if vid in vendors:
            by_vendor.setdefault(vid, []).append(row)
    if not by_vendor:
        raise HTTPException(status_code=400, detail="due ledger rows have no matching vendor on file")

    labels = ", ".join(r["label"] for r in due if r.get("label"))
    run = store.create_run(user_id, f"Retention: {labels}")
    rows = []
    for vid, items in by_vendor.items():
        vendor = vendors[vid]
        first = items[0]
        expires_on = str(first.get("expires_on") or body.as_of)[:10]
        rows.append(
            {
                "run_id": run["id"],
                "vendor_id": vid,
                "vendor_key": vendor["key"],
                "track": "negotiate",
                "target_name": vendor["name"],
                "target_phone": vendor["phone"],
                "objective": f"Our promotional rate ends {expires_on}; keep the rate or get a better one",
                "prior_context": f"Last year you quoted {_money(first.get('amount_usd'))} for {first.get('label')}",
                "state": "planned",
                "state_rank": 0,
                "simulated": False,
            }
        )
    calls = store.insert_calls(rows)
    for row in calls:
        row["voice_gender"] = "female"
    log_event(
        store,
        user_id,
        "run_requested",
        {
            "request": run["request"],
            "kind": "retention",
            "as_of": body.as_of,
            "n_calls": len(calls),
            "ledger_ids": [r.get("id") for r in due],
        },
        run_id=run["id"],
    )
    await _dispatch(store, user_id, run, calls)
    return _assemble(store, run["id"], user_id, utcnow())


# ---------------------------------------------------------------------------------------------
# Context MCP (§4.10): stateless JSON-RPC 2.0 over POST
# ---------------------------------------------------------------------------------------------

MCP_PROTOCOL_VERSION = "2025-03-26"
MCP_TOOLS: list[dict] = [
    {
        "name": "check_calendar_availability",
        "description": (
            "Free time slots the user can accept an appointment in. Pass a date or a date range such as "
            "'2026-10-15' or '2026-10-15 to 2026-10-17'. Returns ISO slots with start and end."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "date_range": {"type": "string", "description": "A date or date range, ISO dates."}
            },
            "required": ["date_range"],
        },
    },
    {
        "name": "get_user_preference",
        "description": (
            "Read one of the user's stored preferences by key, for example budget_ceiling_usd or "
            "preferred_times. Returns null when the key is not set."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string", "description": "Preference key."}},
            "required": ["key"],
        },
    },
]


def _rpc_error(rid: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}, status_code=status
    )


def _parse_date_range(text: str) -> tuple[date | None, date | None]:
    found = re.findall(r"\d{4}-\d{2}-\d{2}", text or "")
    if not found:
        return None, None
    try:
        lo = date.fromisoformat(found[0])
        hi = date.fromisoformat(found[-1]) if len(found) > 1 else lo
    except ValueError:
        return None, None
    return (lo, hi) if lo <= hi else (hi, lo)


def _free_slots(prefs: dict, date_range: str) -> dict:
    lo, hi = _parse_date_range(date_range)
    slots: list[dict] = []
    for slot in prefs.get("availability") or []:
        if not isinstance(slot, dict):
            continue
        start = _parse_ts(slot.get("start"))
        if start is None or lo is None:
            slots.append(slot)
            continue
        if lo <= start.date() <= hi:  # type: ignore[operator]
            slots.append(slot)
    return {
        "date_range": date_range,
        "free_slots": slots,
        "preferred_times": prefs.get("preferred_times") or [],
    }


@app.post(f"{PREFIX}/mcp")
async def mcp(request: Request, store: StoreDep):
    secret = os.environ.get("MCP_SHARED_SECRET") or ""
    token = bearer_token(request)
    if not secret or not token or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="bearer must equal MCP_SHARED_SECRET")
    try:
        raw = await request.json()
    except Exception:  # noqa: BLE001
        return _rpc_error(None, -32700, "parse error", status=400)
    if not isinstance(raw, dict):
        return _rpc_error(None, -32600, "batch requests are not supported", status=400)
    try:
        rpc = JsonRpcRequest.model_validate(raw)
    except Exception:  # noqa: BLE001
        return _rpc_error(raw.get("id"), -32600, "invalid request", status=400)
    params = rpc.params or {}

    if rpc.method.startswith("notifications/"):
        return Response(status_code=202)
    if rpc.method == "initialize":
        result: dict[str, Any] = {
            "protocolVersion": params.get("protocolVersion") or MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "dormammu-context", "version": "0.1.0"},
        }
    elif rpc.method == "ping":
        result = {}
    elif rpc.method == "tools/list":
        result = {"tools": MCP_TOOLS}
    elif rpc.method == "tools/call":
        user_id = request.headers.get("x-dormammu-user") or os.environ.get("MCP_USER_ID")
        if not user_id:
            return _rpc_error(rpc.id, -32602, "no user: set X-Dormammu-User or MCP_USER_ID", status=400)
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        profile = store.get_profile(user_id) or {}
        prefs = profile.get("prefs") or {}
        if name == "check_calendar_availability":
            payload: Any = _free_slots(prefs, str(args.get("date_range") or ""))
        elif name == "get_user_preference":
            key = str(args.get("key") or "")
            payload = {"key": key, "value": prefs.get(key)}
        else:
            return _rpc_error(rpc.id, -32602, f"unknown tool: {name}")
        result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
    else:
        return _rpc_error(rpc.id, -32601, f"method not found: {rpc.method}")
    return {"jsonrpc": "2.0", "id": rpc.id, "result": result}
