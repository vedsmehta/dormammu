"""FastAPI service. Every route lives under /api/py (docs/CONTRACT.md, BUILD-PLAN §4.5-§4.7, §4.10, §12).

Local: `uv run uvicorn server.app:app --port 8000 --reload`. Vercel: api/index.py re-exports `app`.
Cross-lane modules (call.*, router.*, aggregator.*, events.*) are imported by module so tests can patch
their attributes; aggregator.ledger is optional and imported lazily.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import importlib
import inspect
import json
import os
import platform
import re
import subprocess
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aggregator import aggregate as aggregate_mod
from aggregator import extract as extract_mod
from call import dial_client, dispatch, ingest, simulate
from events.log import log_event
from router import plan as planner
from server import accuracy as accuracy_mod
from server import store as store_mod
from server.accuracy import accuracy_report, keewano_token_kind, mask_phone
from server.auth import bearer_token, current_user_id, ensure_profile
from server.schemas import (
    JsonRpcRequest,
    LedgerCreate,
    LedgerFire,
    ProfileUpdate,
    ProposalApprove,
    ResearchIn,
    RunCreate,
    SignupIn,
    VendorsIn,
)
from server.store import TERMINAL_STATES, Store
from settings import ROOT, load_env

load_env()

PREFIX = "/api/py"
STALE_PLANNED_SECONDS = 90.0
EVIDENCE_DIAL_TIMEOUT_SECONDS = 8.0
EVIDENCE_RECENT_EVENTS = 30
EVIDENCE_DATA_CHARS = 300
ACCURACY_CALL_CAP = 500
EVAL_REPORT_PATH: Path = ROOT / "public" / "eval-report.json"  # written by the analysis lane

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
# Auth: password accounts, created server-side (no email delivery anywhere in the flow)
# ---------------------------------------------------------------------------------------------


def supabase_admin() -> Any | None:
    """Service-role Supabase client for GoTrue admin calls, or None when Supabase is not configured.

    Module-level so tests can monkeypatch it. Never cached: the secret key may be rotated at runtime.
    """
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        return None
    from supabase import create_client

    return create_client(url, key)


def _is_duplicate_user_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    status = getattr(exc, "status", None)
    return (
        "already" in text
        or "duplicate" in text
        or code in {"email_exists", "user_already_exists", "phone_exists"}
        or status == 422
        and "exist" in text
    )


@app.post(f"{PREFIX}/auth/signup")
def auth_signup(body: SignupIn, store: StoreDep) -> dict:
    """Create a password account via the GoTrue admin API with email_confirm set, so sign-in works
    immediately with signInWithPassword and nothing depends on email delivery. Unauthenticated."""
    admin = supabase_admin()
    if admin is None:
        raise HTTPException(status_code=400, detail="Supabase is not configured; use DEV_USER_ID locally")
    attributes: dict[str, Any] = {
        "email": body.email,
        "password": body.password,
        "email_confirm": True,
        "user_metadata": {"display_name": body.display_name} if body.display_name else {},
    }
    try:
        response = admin.auth.admin.create_user(attributes)
    except Exception as exc:
        if _is_duplicate_user_error(exc):
            raise HTTPException(status_code=409, detail="account exists, sign in instead") from exc
        message = str(exc) or exc.__class__.__name__
        raise HTTPException(status_code=502, detail=f"account creation failed: {message[:300]}") from exc
    user = getattr(response, "user", None)
    user_id = getattr(user, "id", None)
    if not user_id:
        raise HTTPException(status_code=502, detail="account creation returned no user id")
    user_id = str(user_id)
    # The auth user exists even if this fails: the DB trigger (Supabase) or the first authed request
    # creates the profile, so a profile hiccup must not turn a successful signup into an error.
    with contextlib.suppress(Exception):
        ensure_profile(store, user_id)
        if body.display_name:
            store.upsert_profile(user_id, {"display_name": body.display_name})
    return {"id": user_id, "email": body.email}


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
    # exclude_none: an omitted source or website leaves the stored value alone (store default: manual).
    return store.upsert_vendors(user_id, [v.model_dump(exclude_none=True) for v in body.vendors])


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


@app.post(f"{PREFIX}/ledger")
def post_ledger(body: LedgerCreate, user_id: UserId, store: StoreDep) -> dict:
    """A manual dated entry from the calendar (a booking the user made themselves). No source call."""
    ensure_profile(store, user_id)
    row = {
        "user_id": user_id,
        "vendor_id": None,
        "kind": body.kind,
        "label": body.label.strip(),
        "amount_usd": body.amount_usd,
        "effective_on": body.effective_on,
        "expires_on": body.expires_on,
        "source_call": None,
    }
    rows = store.insert_ledger([row])
    log_event(store, user_id, "ledger_written", {"manual": True, "kind": body.kind, "label": row["label"]})
    return rows[0] if rows else row


@app.get(f"{PREFIX}/events")
def get_events(user_id: UserId, store: StoreDep, limit: int = 50) -> list[dict]:
    return store.list_events(user_id, max(1, min(limit, 500)))


# ---------------------------------------------------------------------------------------------
# Past calls, evidence and accuracy (read-only; phones are masked before they leave the server)
# ---------------------------------------------------------------------------------------------


def _public_call(row: dict) -> dict:
    out = dict(row)
    out["target_phone"] = mask_phone(row.get("target_phone"))
    return out


@app.get(f"{PREFIX}/calls")
def get_calls(user_id: UserId, store: StoreDep, limit: int = 200, offset: int = 0) -> list[dict]:
    """The user's calls across every run, newest first, each with the run's `request`. Phones masked."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return [_public_call(r) for r in store.list_calls_for_user(user_id, limit, offset)]


def _exc_text(exc: BaseException) -> str:
    return f"{exc.__class__.__name__}: {exc}"[:300]


async def _dial_account_evidence() -> dict:
    """Account limits plus the masked from-number. Errors land in "error"; the secret never leaves."""
    out: dict[str, Any] = {
        "configured": bool(os.environ.get("DIAL_API_KEY")),
        "limits": None,
        "max_call_duration_seconds": None,
        "from_number": mask_phone(os.environ.get("DIAL_NUMBER")),
        "numbers": [],
        "api_keys": None,
        "error": None,
    }
    if not out["configured"]:
        out["error"] = "DIAL_API_KEY is not set"
        return out
    try:
        account, numbers = await asyncio.wait_for(
            asyncio.gather(dial_client.get_account(), dial_client.list_numbers(), return_exceptions=True),
            timeout=EVIDENCE_DIAL_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001  (timeout or cancellation)
        out["error"] = _exc_text(exc)
        return out
    errors: list[str] = []
    if isinstance(account, BaseException):
        errors.append(f"account: {_exc_text(account)}")
    elif isinstance(account, dict):
        out["limits"] = account.get("limits")
        out["max_call_duration_seconds"] = account.get("maxCallDurationSeconds")
        keys = account.get("apiKeys")
        out["api_keys"] = len(keys) if isinstance(keys, list) else None
    if isinstance(numbers, BaseException):
        errors.append(f"numbers: {_exc_text(numbers)}")
    elif isinstance(numbers, list):
        out["numbers"] = [
            {
                "number": mask_phone(n.get("number")),
                "nickname": n.get("nickname"),
                "country": n.get("country"),
                "setup_status": n.get("setupStatus"),
                "calling_enabled": n.get("callingEnabled"),
            }
            for n in numbers
            if isinstance(n, dict)
        ]
        if out["from_number"] is None and out["numbers"]:
            out["from_number"] = out["numbers"][0]["number"]
    out["error"] = "; ".join(errors) or None
    return out


def _scrub_secrets(value: Any) -> Any:
    """Drop string values under keys that look like credentials. Belt and braces for third-party dicts."""
    if not isinstance(value, dict):
        return value
    return {
        k: v
        for k, v in value.items()
        if not (isinstance(v, str) and any(word in str(k).lower() for word in ("token", "secret", "key")))
    }


def _keewano_evidence() -> dict:
    """Sink status without the token. events.keewano is another lane's module: every name is optional."""
    token = os.environ.get("KEEWANO_TOKEN")
    out: dict[str, Any] = {
        "enabled": False,
        "token_kind": keewano_token_kind(token),
        "status": None,
        "last_error": None,
        "last_status": None,
        "event_types": None,
        "companion_types": None,
        "fallback_event_type_id": None,
        "ingress_url": None,
        "auth_hint": None,
    }
    try:
        from events.log import keewano_enabled

        out["enabled"] = bool(keewano_enabled())
    except Exception:  # noqa: BLE001
        truthy = os.environ.get("KEEWANO_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
        out["enabled"] = truthy and bool(token)
    try:
        from events import keewano as kw
    except Exception:  # noqa: BLE001
        return out
    status_fn = getattr(kw, "status", None)
    if callable(status_fn):
        try:
            out["status"] = _scrub_secrets(status_fn())
        except Exception as exc:  # noqa: BLE001
            out["status"] = {"error": _exc_text(exc)}
    out["last_error"] = getattr(kw, "last_error", None)
    out["last_status"] = getattr(kw, "last_status", None)
    for key, name in (
        ("event_types", "EVENT_TYPE_IDS"),
        ("companion_types", "COMPANION_TYPE_IDS"),
        ("fallback_event_type_id", "FALLBACK_EVENT_TYPE_ID"),
        ("ingress_url", "INGRESS_URL"),
    ):
        value = getattr(kw, name, None)
        out[key] = dict(value) if isinstance(value, dict) else value
    if out["token_kind"] != "project_key":
        out["auth_hint"] = getattr(kw, "AUTH_HINT", None) or (
            "Keewano ingest needs a Project API Key (a JWT); a kwpat- personal token only works for the MCP"
        )
    return out


def _recent_events(store: Store, user_id: str) -> list[dict]:
    out: list[dict] = []
    for e in store.list_events(user_id, EVIDENCE_RECENT_EVENTS):
        text = (
            json.dumps(e.get("data"), separators=(",", ":"), default=str) if e.get("data") is not None else ""
        )
        out.append(
            {
                "id": e.get("id"),
                "type": e.get("type"),
                "created_at": e.get("created_at"),
                "run_id": e.get("run_id"),
                "call_id": e.get("call_id"),
                "data": text[:EVIDENCE_DATA_CHARS],
                "truncated": len(text) > EVIDENCE_DATA_CHARS,
            }
        )
    return out


def _git_short_sha() -> str | None:
    """Short sha from git (2s budget), else Vercel's env, else None. Never raises."""
    with contextlib.suppress(Exception):
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        sha = result.stdout.strip()
        if result.returncode == 0 and sha:
            return sha
    vercel = os.environ.get("VERCEL_GIT_COMMIT_SHA")
    return vercel[:7] if vercel else None


def _policy_evidence() -> dict:
    """The product rules the demo is held to (BUILD-PLAN §8), read from the modules that enforce them."""
    out: dict[str, Any] = {
        "ai_disclosure": None,
        "summary_closer": None,
        "only_vendor_rows_dialable": True,
        "multiplier_label": "est.",
        "dial_overhead_seconds": getattr(aggregate_mod, "DIAL_OVERHEAD_SECONDS", None),
        "max_calls_per_run": int(os.environ.get("MAX_CALLS_PER_RUN", "5") or 5),
        "system_of_record": "postgres events",
        "analysis_sink": "keewano",
    }
    with contextlib.suppress(Exception):
        from router import instructions

        out["ai_disclosure"] = getattr(instructions, "AI_DISCLOSURE", None)
        out["summary_closer"] = getattr(instructions, "SUMMARY_CLOSER", None)
    return out


@app.get(f"{PREFIX}/evidence")
async def get_evidence(user_id: UserId, store: StoreDep) -> dict:
    """Everything the evidence screen shows: Dial account and real calls, Keewano sink state, the events
    ledger, call state counts, build info. Nothing here is fabricated and no secret or full phone leaves."""
    dial_account = await _dial_account_evidence()
    real_calls = [_public_call(r) for r in store.real_dial_calls(user_id, 20)]
    buckets = store.count_calls_by_simulated(user_id)
    return {
        "dial": {
            "account": dial_account,
            "real_calls": real_calls,
            "n_real_calls": buckets.get("real_dial", 0),
            "n_simulated_calls": buckets.get("simulated", 0),
            "n_undialed_calls": buckets.get("undialed", 0),
        },
        "keewano": _keewano_evidence(),
        "events": {"by_type": store.count_events_by_type(user_id), "recent": _recent_events(store, user_id)},
        "calls": {"by_state": store.count_calls_by_state(user_id), "by_simulated": buckets},
        "policy": _policy_evidence(),
        "build": {
            "commit": _git_short_sha(),
            "deployed_at": None,
            "python": platform.python_version(),
            "store": getattr(store, "name", "sqlite"),
        },
        "generated_at": utcnow().isoformat(),
    }


def _read_eval_report() -> Any | None:
    try:
        with open(EVAL_REPORT_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001  (missing, unreadable or not JSON: the analysis lane owns it)
        return None


@app.get(f"{PREFIX}/accuracy")
def get_accuracy(user_id: UserId, store: StoreDep) -> dict:
    """Success, funnel and recap-agreement rates over the user's most recent calls (cap ACCURACY_CALL_CAP).

    Top level is every call; `split.real` and `split.simulated` repeat the same report per bucket so the
    screen can show real-dial accuracy next to fixture accuracy instead of blending them.
    """
    calls = store.list_calls_for_user(user_id, ACCURACY_CALL_CAP, 0)
    report = accuracy_report(calls)
    report["split"] = {
        "real": accuracy_report([c for c in calls if not c.get("simulated")]),
        "simulated": accuracy_report([c for c in calls if c.get("simulated")]),
    }
    report["eval"] = _read_eval_report()
    prefixes = " or ".join(f'"{p.capitalize()}"' for p in accuracy_mod.RECAP_PREFIXES)
    report["recap_agreement"]["definition"] = (
        "extracted amount, saved_usd or appointment day appears in the agent's closing recap turn "
        f"(the last agent turn opening with {prefixes})"
    )
    report["note"] = (
        "Seeded and simulated calls are included and flagged: every fixture row has simulated=true, and "
        "split.real / split.simulated report each bucket on its own. Recap agreement is a consistency check "
        "between the extractor and the agent's spoken summary, not ground truth."
    )
    report["cap"] = ACCURACY_CALL_CAP
    report["generated_at"] = utcnow().isoformat()
    return report


def _money(amount: Any) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return "the rate"
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


@app.get(f"{PREFIX}/insights")
def get_insights(user_id: UserId, store: StoreDep) -> dict:
    """The analysis report over this user's history (analysis.insights.compute_insights).

    Vendor scorecards, track stats, failure modes, the prior-context effect and ranked recommendations,
    with a coverage block that says how much of the data is seeded or simulated.
    """
    try:
        from analysis.insights import compute_insights
    except ImportError as exc:  # pragma: no cover - the analysis package ships with the repo
        raise HTTPException(status_code=501, detail=f"analysis layer unavailable: {exc}") from exc
    try:
        return compute_insights(store, user_id, cache_ttl=60)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"insights failed: {exc}") from exc


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
# Research-first flow: POST /research -> proposal -> POST /proposals/{id}/approve -> run
#
# The engine (research.engine.research, another lane's module) finds providers, plans, discounts and phones
# and checks regulations; nothing is dialed until the user approves items. Only vendor rows are dialable, so
# approval first turns each item into a vendors row (reusing a roster row when the phone matches) and then
# builds a run the way POST /runs does. Research vendors are external numbers: they are dialed only when
# ALLOW_EXTERNAL_CALLS is set, otherwise their rows run as labeled simulated rows carrying NOT_DIALED_NOTE.
# ---------------------------------------------------------------------------------------------

EXTERNAL_CALLS_ENV = "ALLOW_EXTERNAL_CALLS"
NOT_DIALED_NOTE = "approved, not dialed: external calling disabled"
OBJECTIVE_MAX_CHARS = 600
VENDOR_KEY_MAX_CHARS = 40
APPROVABLE_STATUSES = ("proposed",)
TRACKS = ("quote", "schedule", "negotiate")
# Track by research category, used only when an item carries no valid track of its own.
CATEGORY_TRACKS = {
    "movers": "quote",
    "moving": "quote",
    "mover": "quote",
    "storage": "quote",
    "internet": "negotiate",
    "isp": "negotiate",
    "broadband": "negotiate",
    "utilities": "negotiate",
    "utility": "negotiate",
    "phone": "negotiate",
    "insurance": "negotiate",
    "dentist": "schedule",
    "dental": "schedule",
    "doctor": "schedule",
    "clinic": "schedule",
    "health": "schedule",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def external_calls_enabled() -> bool:
    """ALLOW_EXTERNAL_CALLS is truthy (1, true, yes). Read at call time so a restart is never needed."""
    return os.environ.get(EXTERNAL_CALLS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "null", "undefined") else text


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        with contextlib.suppress(Exception):
            out = dump()
            if isinstance(out, dict):
                return out
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def normalize_proposal(raw: Any, request_text: str) -> dict:
    """The stored proposal body. Always a dict with `items` (each with an `id`), `errors`, `regulations`,
    `meta` and `request`; everything else the engine returned is kept as is."""
    body = dict(_as_dict(raw))
    items: list[dict] = []
    for index, item in enumerate(_as_list(body.get("items"))):
        item = dict(_as_dict(item))
        if not item:
            continue
        item["id"] = _str(item.get("id") or item.get("item_id")) or f"item-{index + 1}"
        items.append(item)
    body["items"] = items
    body["errors"] = [str(e)[:300] for e in _as_list(body.get("errors")) if e]
    body["regulations"] = _as_list(body.get("regulations"))
    body["meta"] = dict(_as_dict(body.get("meta")))
    body.setdefault("request", request_text)
    return body


def item_vendor(item: dict) -> dict:
    """{name, phone, website, roster_vendor_id} from the item's `vendor` block, falling back to flat keys.
    phone is None when the engine kept none (it only keeps a phone that appeared in a fetched source or on
    the roster); roster_vendor_id is the vendors.id the engine copied from the roster, or None."""
    vendor = _as_dict(item.get("vendor"))
    return {
        "name": _str(vendor.get("name") or item.get("vendor_name") or item.get("name")),
        "phone": _str(vendor.get("phone") or item.get("phone")) or None,
        "website": _str(vendor.get("website") or item.get("website") or item.get("url")) or None,
        "roster_vendor_id": _str(vendor.get("roster_vendor_id") or item.get("roster_vendor_id")) or None,
    }


def item_track(item: dict, vendor_row: dict | None = None) -> str:
    track = _str(item.get("track")).lower()
    if track in TRACKS:
        return track
    if vendor_row and vendor_row.get("track") in TRACKS:
        return vendor_row["track"]
    return CATEGORY_TRACKS.get(_str(item.get("category")).lower(), "quote")


def research_vendor_key(category: Any, name: Any) -> str:
    """'r_' + slug of the category and vendor name, capped at VENDOR_KEY_MAX_CHARS."""
    text = " ".join(p for p in (_str(category), _str(name)) if p).lower()
    slug = _SLUG_RE.sub("-", text).strip("-") or "vendor"
    return ("r_" + slug)[:VENDOR_KEY_MAX_CHARS].rstrip("-")


def _phone_digits(value: Any) -> str:
    return re.sub(r"\D", "", _str(value))


def same_phone(a: Any, b: Any) -> bool:
    """Digits-only comparison; a 10-digit national number matches its +1 form."""
    da, db = _phone_digits(a), _phone_digits(b)
    if not da or not db:
        return False
    return da == db or (len(da) >= 10 and len(db) >= 10 and da[-10:] == db[-10:])


def item_objective(item: dict, fallback: str) -> str:
    """strategy.opening + why + "Ask for: ..." kept under OBJECTIVE_MAX_CHARS; `why` is trimmed first."""
    strategy = _as_dict(item.get("strategy"))
    opening = _str(strategy.get("opening"))
    why = _str(item.get("why"))
    asks = [_str(a) for a in _as_list(strategy.get("asks")) if _str(a)]
    ask_line = "Ask for: " + ", ".join(asks) + "." if asks else ""

    def compose(why_text: str) -> str:
        return " ".join(p for p in (opening, why_text, ask_line) if p).strip()

    text = compose(why)
    if len(text) >= OBJECTIVE_MAX_CHARS and why:
        room = OBJECTIVE_MAX_CHARS - 1 - len(compose("")) - 1
        why = why[: room - 3].rstrip() + "..." if room > 20 else ""
        text = compose(why)
    if len(text) >= OBJECTIVE_MAX_CHARS:
        text = text[: OBJECTIVE_MAX_CHARS - 4].rstrip() + "..."
    return text or _str(fallback)[: OBJECTIVE_MAX_CHARS - 1] or "Call about the client's request."


def research_counts(body: dict, seconds: float) -> dict:
    items = body.get("items") or []
    categories = {_str(it.get("category")).lower() for it in items if _str(it.get("category"))}
    meta = body.get("meta") or {}
    web_search = body.get("web_search")
    if web_search is None:
        web_search = meta.get("web_search", False)
    return {
        "items": len(items),
        "phones_verified": sum(1 for it in items if item_vendor(it)["phone"]),
        "categories": len(categories),
        "regulations": len(body.get("regulations") or []),
        "web_search": bool(web_search),
        "seconds": round(seconds, 2),
        "errors": len(body.get("errors") or []),
    }


@app.post(f"{PREFIX}/research")
async def post_research(body: ResearchIn, user_id: UserId, store: StoreDep) -> dict:
    """Research first. The engine reads the profile, the request and the roster and returns a proposal;
    it is stored (status proposed, or failed when it has no items and reports errors) and returned. Nothing
    is dialed here. 503 when the engine module cannot be imported."""
    request_text = body.request.strip()
    if not request_text:
        raise HTTPException(status_code=400, detail="request is empty")
    profile = ensure_profile(store, user_id)
    roster = store.list_vendors(user_id)
    try:
        engine = importlib.import_module("research.engine")
        research_fn = engine.research
    except Exception as exc:  # absent, mid-write or broken module: say so and stay up
        raise HTTPException(
            status_code=503,
            detail=f"research engine unavailable: {exc.__class__.__name__}: {exc}"[:300],
        ) from exc
    started = time.monotonic()
    try:
        result = research_fn(profile, request_text, roster)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001
        result = {"items": [], "errors": [f"{exc.__class__.__name__}: {exc}"[:300]]}
    seconds = time.monotonic() - started
    proposal = normalize_proposal(result, request_text)
    status = "failed" if not proposal["items"] and proposal["errors"] else "proposed"
    row = store.create_proposal(user_id, request_text, proposal, status=status)
    counts = research_counts(proposal, seconds)
    log_event(
        store,
        user_id,
        "research_completed",
        {"proposal_id": row["id"], "status": status, "request": request_text, **counts},
    )
    return row


@app.get(f"{PREFIX}/proposals")
def get_proposals(user_id: UserId, store: StoreDep, limit: int = 20) -> list[dict]:
    """Newest first, without the body: id, request, status, n_items, approved_item_ids, run_id, timestamps."""
    return store.list_proposals(user_id, max(1, min(limit, 100)))


def _proposal_or_404(store: Store, proposal_id: str, user_id: str) -> dict:
    row = store.get_proposal(proposal_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return row


@app.get(f"{PREFIX}/proposals/{{proposal_id}}")
def get_proposal(proposal_id: str, user_id: UserId, store: StoreDep) -> dict:
    return _proposal_or_404(store, proposal_id, user_id)


def _vendor_for_item(store: Store, user_id: str, item: dict, vendor_info: dict, roster: list[dict]) -> dict:
    """The vendors row an approved item dials: the roster row the engine named (vendor.roster_vendor_id) or
    the one with the same phone, else an upserted research row (key r_<category>-<name>, is_demo false,
    source research, website kept)."""
    for vendor in roster:
        if vendor_info["roster_vendor_id"] and str(vendor.get("id")) == vendor_info["roster_vendor_id"]:
            return vendor
    for vendor in roster:
        if same_phone(vendor.get("phone"), vendor_info["phone"]):
            return vendor
    key = research_vendor_key(item.get("category"), vendor_info["name"])
    fields: dict[str, Any] = {
        "key": key,
        "name": vendor_info["name"] or key,
        "track": item_track(item),
        "phone": vendor_info["phone"],
        "is_demo": False,
        "source": "research",
    }
    if vendor_info["website"]:
        fields["website"] = vendor_info["website"]
    (row,) = store.upsert_vendors(user_id, [fields])
    roster.append(row)
    return row


@app.post(f"{PREFIX}/proposals/{{proposal_id}}/approve")
async def approve_proposal(
    proposal_id: str, body: ProposalApprove, user_id: UserId, store: StoreDep
) -> dict:
    """Turn the approved items into a run. Items without a phone are skipped ("no verified phone"); the
    rest become vendors rows and one calls row each. Roster (is_demo) vendors go through dispatch_run; research
    vendors are external numbers and are dialed only when ALLOW_EXTERNAL_CALLS is set, otherwise their rows are
    pre-marked simulated with NOT_DIALED_NOTE and the tick's simulate path drives them."""
    proposal = _proposal_or_404(store, proposal_id, user_id)
    if proposal.get("status") not in APPROVABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"proposal is {proposal.get('status')}, not proposed")
    body_items = (proposal.get("proposal") or {}).get("items") if isinstance(proposal.get("proposal"), dict) else []
    items = {it["id"]: it for it in _as_list(body_items) if isinstance(it, dict) and it.get("id")}
    request_text = _str(proposal.get("request")) or "approved proposal"
    ensure_profile(store, user_id)
    roster = store.list_vendors(user_id)
    enabled = external_calls_enabled()
    cap = int(os.environ.get("MAX_CALLS_PER_RUN", "5") or 5)

    skipped: list[dict] = []
    accepted: list[tuple[str, dict, dict, str]] = []  # (item_id, item, vendor row, track)
    seen_items: set[str] = set()
    vendor_item: dict[str, str] = {}
    for raw_id in body.item_ids:
        item_id = raw_id.strip()
        if not item_id or item_id in seen_items:
            continue
        seen_items.add(item_id)
        item = items.get(item_id)
        if item is None:
            skipped.append({"item_id": item_id, "reason": "not in this proposal"})
            continue
        vendor_info = item_vendor(item)
        if not vendor_info["phone"]:
            skipped.append({"item_id": item_id, "reason": "no verified phone"})
            continue
        if len(accepted) >= cap:
            skipped.append({"item_id": item_id, "reason": f"over the MAX_CALLS_PER_RUN cap of {cap}"})
            continue
        vendor = _vendor_for_item(store, user_id, item, vendor_info, roster)
        if vendor["id"] in vendor_item:
            skipped.append({"item_id": item_id, "reason": f"same vendor as {vendor_item[vendor['id']]}"})
            continue
        vendor_item[vendor["id"]] = item_id
        accepted.append((item_id, item, vendor, item_track(item, vendor)))
    if not accepted:
        reasons = "; ".join(f"{s['item_id']}: {s['reason']}" for s in skipped) or "no item ids given"
        raise HTTPException(status_code=400, detail=f"nothing to dispatch ({reasons})")

    run = store.create_run(user_id, request_text)
    rows = []
    for item_id, item, vendor, track in accepted:
        rows.append(
            {
                "run_id": run["id"],
                "vendor_id": vendor["id"],
                "vendor_key": vendor["key"],
                "track": track,
                "target_name": vendor["name"],
                "target_phone": vendor["phone"],
                "objective": item_objective(item, request_text),
                "prior_context": _safe_prior_context(store, user_id, vendor["key"], track),
                "state": "planned",
                "state_rank": 0,
                "simulated": False,
            }
        )
    calls = store.insert_calls(rows)
    genders = ("female", "male")
    to_dial: list[dict] = []
    held: list[dict] = []
    for index, (row, (_item_id, _item, vendor, _track)) in enumerate(zip(calls, accepted)):
        row["voice_gender"] = genders[index % 2]
        row["is_demo"] = bool(vendor.get("is_demo"))
        (to_dial if enabled or vendor.get("is_demo") else held).append(row)
    log_event(
        store,
        user_id,
        "run_requested",
        {
            "request": request_text,
            "kind": "proposal",
            "proposal_id": proposal_id,
            "n_calls": len(calls),
            "vendor_keys": [r["vendor_key"] for r in calls],
            "item_ids": [a[0] for a in accepted],
        },
        run_id=run["id"],
    )

    now_iso = utcnow().isoformat()
    for row in held:
        store.update_call(row["id"], {"simulated": True, "error": NOT_DIALED_NOTE, "started_at": now_iso})
        store.update_call_state(row["id"], "dialing", 1)
        context = {"track": row["track"], "vendor_key": row["vendor_key"]}
        log_event(
            store,
            user_id,
            "call_simulated",
            {"reason": NOT_DIALED_NOTE, "limit": False, "external": True, **context},
            run_id=run["id"],
            call_id=row["id"],
        )
        log_event(
            store,
            user_id,
            "call_dispatched",
            {"simulated": True, "dial_call_id": None, **context},
            run_id=run["id"],
            call_id=row["id"],
        )
    if to_dial:
        await _dispatch(store, user_id, run, to_dial)
    else:
        store.set_run_status(run["id"], "running")

    approved_ids = [a[0] for a in accepted]
    log_event(
        store,
        user_id,
        "proposal_approved",
        {
            "proposal_id": proposal_id,
            "approved": len(accepted),
            "skipped": skipped,
            "dialed": len(to_dial),
            "simulated": len(held),
            "external_calls_enabled": enabled,
            "item_ids": approved_ids,
        },
        run_id=run["id"],
    )
    updated = store.update_proposal(
        proposal_id, {"status": "dispatched", "run_id": run["id"], "approved_item_ids": approved_ids}
    )
    return {
        "run": _assemble(store, run["id"], user_id, utcnow()),
        "skipped": skipped,
        "external_calls_enabled": enabled,
        "proposal": updated,
    }


@app.post(f"{PREFIX}/proposals/{{proposal_id}}/reject")
def reject_proposal(proposal_id: str, user_id: UserId, store: StoreDep) -> dict:
    proposal = _proposal_or_404(store, proposal_id, user_id)
    if proposal.get("status") == "dispatched":
        raise HTTPException(status_code=409, detail="proposal already dispatched")
    row = store.update_proposal(proposal_id, {"status": "rejected"})
    if row is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    log_event(store, user_id, "proposal_rejected", {"proposal_id": proposal_id, "request": row.get("request")})
    return row


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
