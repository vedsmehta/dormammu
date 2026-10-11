"""The same questions as analysis.insights, phrased for Keewano, plus a plain-Python MCP client.

Ground truth (probed Oct 11 2026 through the session's keewano MCP server and from plain httpx):

- Endpoint: POST https://api.keewano.com/mcp, header `k-token: <Personal Access Token>`. The PAT lives in
  env KEEWANO_PAT (fallback KEEWANO_TOKEN for older .env files; since Oct 11 KEEWANO_TOKEN is the Project
  API key that ingest wants, and the MCP server rejects that one). JSON-RPC 2.0 over streamable HTTP;
  responses come back as plain `application/json`, no Mcp-Session-Id is issued, and a bare `tools/call`
  without `initialize` is accepted. Tool results carry the parsed answer under `result.structuredContent`.
- One project is visible: SpeedHacks26, id f21f5584-84af-4c5a-88bb-b4cbace12f54 (tz UTC). At probe time it
  held zero events and only the built-in event catalog (ids < 100). Our custom types (run_requested ..
  ledger_written, ids 2001-2008) appear in the catalog only after the first accepted registration.
- Keewano SQL: one table `events`, one column per event type (double-quoted names), step-function
  semantics, `user_id` and `event_datetime` are the only non-event columns we need. A string-typed event's
  value is a label: `=`, `IN`, `LIKE`, `GROUP BY` and `count(DISTINCT)` work, nothing can parse it. Our
  events today ride as one JSON string per event (events.keewano.encode_event), so Keewano can count and
  sequence them but cannot read a quote amount out of them. That is why each query below says whether it
  runs on today's encoding or needs the scalar event types listed in PROPOSED_EVENT_TYPES.
- Lua (run_script_query): `ev.id`/`ev.data` are numbers; names resolve with kdb.resolve_event_id at upload
  and an unknown name is an upload error, so the Lua query also waits for registration.

`keewano_insights` never raises and returns None when Keewano cannot be reached or answers nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from events.keewano import EVENT_TYPE_IDS

logger = logging.getLogger("dormammu.analysis.keewano")

MCP_URL = os.environ.get("KEEWANO_MCP_URL") or "https://api.keewano.com/mcp"
DEFAULT_PROJECT_ID = "f21f5584-84af-4c5a-88bb-b4cbace12f54"  # SpeedHacks26 (list_projects, Oct 11 2026)
TIMEOUT_SECONDS = 10.0
DEFAULT_WINDOW_DAYS = 180
PLAN_LIMIT_MARKER = "plan limit"

# Statuses, honest about today's catalog:
RUNNABLE_TODAY = "runnable_today"  # built-in columns only; works against the empty project right now
AFTER_INGEST = "runnable_once_ingested"  # needs our custom types registered (first accepted send_events)
NEEDS_SCALARS = "needs_scalar_events"  # needs PROPOSED_EVENT_TYPES in addition (phase 2 schema)

# Additive registration proposal for events/keewano.py (ids continue after ledger_written = 2008). Each is
# one scalar per call so Keewano SQL can aggregate it; emit them right after call_terminated so the
# step-function value is pinned to that call when the next event lands.
PROPOSED_EVENT_TYPES = [
    {"eventTypeId": 2009, "name": "vendor_key", "dataType": "string"},
    {"eventTypeId": 2010, "name": "track", "dataType": "string"},
    {"eventTypeId": 2011, "name": "termination_type", "dataType": "string"},
    {"eventTypeId": 2012, "name": "call_duration_seconds", "dataType": "uint"},
    {"eventTypeId": 2013, "name": "quote_amount_usd", "dataType": "float"},
    {"eventTypeId": 2014, "name": "saved_usd", "dataType": "float"},
    {"eventTypeId": 2015, "name": "confirmed", "dataType": "bool"},
    {"eventTypeId": 2016, "name": "prior_context_used", "dataType": "bool"},
]

_USER_WINDOW = "user_id = '{user_id}' AND event_datetime BETWEEN '{since}' AND '{until}'"

LUA_RUNS_AND_GAPS = """
local RUN = kdb.resolve_event_id("run_requested")
local DISPATCH = kdb.resolve_event_id("call_dispatched")
partial = { users = 0, runs = 0, calls = 0, max_fanout = 0, gaps_over_7d = 0, max_gap_days = 0,
            first_ts = 0, last_ts = 0 }
output = { users = 0, runs = 0, calls = 0, max_fanout = 0, gaps_over_7d = 0, max_gap_days = 0,
           first_ts = 0, last_ts = 0 }
function process_user(user, events)
  partial.users = partial.users + 1
  local n = events.count
  local i = events:find_next(0, RUN, n)
  local prev_ts = -1
  while i < n do
    local ts = events[i].timestamp
    partial.runs = partial.runs + 1
    if prev_ts >= 0 then
      local gap_days = (ts - prev_ts) / 86400
      if gap_days > 7 then partial.gaps_over_7d = partial.gaps_over_7d + 1 end
      if gap_days > partial.max_gap_days then partial.max_gap_days = gap_days end
    end
    prev_ts = ts
    if partial.first_ts == 0 or ts < partial.first_ts then partial.first_ts = ts end
    if ts > partial.last_ts then partial.last_ts = ts end
    local stop = events:find_next(i + 1, RUN, n)
    local fanout = 0
    local j = events:find_next(i + 1, DISPATCH, stop)
    while j < stop do
      fanout = fanout + 1
      j = events:find_next(j + 1, DISPATCH, stop)
    end
    partial.calls = partial.calls + fanout
    if fanout > partial.max_fanout then partial.max_fanout = fanout end
    i = stop
  end
end
function reduce(partials)
  for _, p in ipairs(partials) do
    output.users = output.users + p.users
    output.runs = output.runs + p.runs
    output.calls = output.calls + p.calls
    output.gaps_over_7d = output.gaps_over_7d + p.gaps_over_7d
    if p.max_fanout > output.max_fanout then output.max_fanout = p.max_fanout end
    if p.max_gap_days > output.max_gap_days then output.max_gap_days = p.max_gap_days end
    if p.first_ts > 0 and (output.first_ts == 0 or p.first_ts < output.first_ts) then output.first_ts = p.first_ts end
    if p.last_ts > output.last_ts then output.last_ts = p.last_ts end
  end
end
"""

KEEWANO_QUERIES: list[dict[str, Any]] = [
    {
        "name": "project_activity",
        "question": "Is anything landing in Keewano at all: distinct users and events in the window?",
        "tool": "run_sql_query",
        "status": RUNNABLE_TODAY,
        "needs": {"event_types": [], "fields": ["user_id", "event_datetime"]},
        "sql": (
            "SELECT count(DISTINCT user_id) AS users, count(*) AS events, min(event_datetime) AS first_event, "
            "max(event_datetime) AS last_event FROM events WHERE event_datetime BETWEEN '{since}' AND '{until}'"
        ),
        "insights_equivalent": "coverage",
    },
    {
        "name": "activity_by_day",
        "question": "How many runs, calls, terminations and outcomes did this user generate per day?",
        "tool": "run_sql_query",
        "status": AFTER_INGEST,
        "needs": {
            "event_types": ["run_requested", "call_dispatched", "call_terminated", "outcome_extracted"],
            "fields": ["user_id", "event_datetime"],
        },
        "sql": (
            "SELECT date_trunc('day', event_datetime) AS day, count(\"run_requested\") AS runs, "
            'count("call_dispatched") AS calls, count("call_terminated") AS terminated, '
            'count("outcome_extracted") AS outcomes FROM events WHERE ' + _USER_WINDOW + " GROUP BY day ORDER BY day"
        ),
        "insights_equivalent": "coverage + track_stats.n_calls",
    },
    {
        "name": "call_funnel",
        "question": "Of the calls dispatched, how many connected, terminated and produced an outcome?",
        "tool": "run_sql_query",
        "status": AFTER_INGEST,
        "needs": {
            "event_types": ["call_dispatched", "call_simulated", "call_connected", "call_terminated", "outcome_extracted"],
            "fields": ["user_id", "event_datetime"],
        },
        "sql": (
            'SELECT count("call_dispatched") AS dispatched, count("call_simulated") AS simulated, '
            'count("call_connected") AS connected, count("call_terminated") AS terminated, '
            'count("outcome_extracted") AS outcomes FROM events WHERE ' + _USER_WINDOW
        ),
        "insights_equivalent": "coverage.n_done / n_failed, failure_modes.failure_rate",
    },
    {
        "name": "runs_and_gaps",
        "question": "How many runs, how wide was each fan-out, and how long were the gaps between runs?",
        "tool": "run_script_query",
        "status": AFTER_INGEST,
        "needs": {"event_types": ["run_requested", "call_dispatched"], "fields": ["timestamp"]},
        "lua": LUA_RUNS_AND_GAPS,
        "filters": [{"operation": 13, "value": ["{user_id}"]}],
        "insights_equivalent": "coverage.n_runs, track_stats.avg_calls_per_run, date range",
    },
    {
        "name": "failure_modes",
        "question": "How do calls end: completed, voicemail, no answer, busy?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {"event_types": ["termination_type"], "fields": ["user_id", "event_datetime"]},
        "sql": (
            'SELECT "termination_type" AS kind, count("termination_type") AS n FROM events WHERE '
            + _USER_WINDOW
            + ' AND "termination_type" IS NOT NULL GROUP BY kind ORDER BY n DESC'
        ),
        "insights_equivalent": "failure_modes.counts",
    },
    {
        "name": "answer_rate_by_hour",
        "question": "At which hours do vendors pick up?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {"event_types": ["termination_type"], "fields": ["user_id", "event_datetime"]},
        "sql": (
            'SELECT EXTRACT(hour FROM event_datetime) AS hour, count("termination_type") AS n_calls, '
            "count(*) FILTER (WHERE \"termination_type\" = 'completed') AS n_answered FROM events WHERE "
            + _USER_WINDOW
            + ' AND "termination_type" IS NOT NULL GROUP BY hour ORDER BY hour'
        ),
        "insights_equivalent": "time_of_day.per_hour (UTC here; insights uses the user's zone)",
    },
    {
        "name": "quotes_by_vendor",
        "question": "Per vendor: how many quotes, the best and the average?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {
            "event_types": ["vendor_key", "quote_amount_usd"],
            "fields": ["user_id", "event_datetime"],
            "ordering": "vendor_key must be emitted before quote_amount_usd for the same call",
        },
        "sql": (
            'SELECT "vendor_key" AS vendor, count("quote_amount_usd") AS n_quotes, min("quote_amount_usd") AS best_usd, '
            'avg("quote_amount_usd") AS avg_usd FROM events WHERE '
            + _USER_WINDOW
            + ' AND "quote_amount_usd" IS NOT NULL GROUP BY vendor ORDER BY best_usd'
        ),
        "insights_equivalent": "vendor_scorecards.*.best_quote_usd / avg_quote_usd",
    },
    {
        "name": "first_quotes_by_vendor",
        "question": "What did each vendor quote the first time (pair with quotes_by_vendor for the context effect)?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {"event_types": ["vendor_key", "quote_amount_usd"], "fields": ["user_id", "event_datetime"]},
        "sql": (
            'SELECT "vendor_key" AS vendor, avg("quote_amount_usd") AS first_quote_usd FROM events WHERE '
            + _USER_WINDOW
            + ' AND occurrence("quote_amount_usd") = 1 GROUP BY vendor'
        ),
        "insights_equivalent": "context_effect.quote.avg_first_usd",
    },
    {
        "name": "savings_by_vendor",
        "question": "Per retention line: how often it conceded and how much per month?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {"event_types": ["vendor_key", "saved_usd"], "fields": ["user_id", "event_datetime"]},
        "sql": (
            'SELECT "vendor_key" AS vendor, count("saved_usd") AS n_concessions, sum("saved_usd") AS total_usd_per_month, '
            'avg("saved_usd") AS avg_usd_per_month FROM events WHERE '
            + _USER_WINDOW
            + ' AND "saved_usd" > 0 GROUP BY vendor ORDER BY total_usd_per_month DESC'
        ),
        "insights_equivalent": "vendor_scorecards.*.total_saved_usd_per_month",
    },
    {
        "name": "prior_context_share",
        "question": "How many terminated calls carried prior context?",
        "tool": "run_sql_query",
        "status": NEEDS_SCALARS,
        "needs": {"event_types": ["prior_context_used", "termination_type"], "fields": ["user_id", "event_datetime"]},
        "sql": (
            'SELECT count("termination_type") AS n_calls, count(*) FILTER (WHERE "prior_context_used" = true) '
            "AS n_with_context FROM events WHERE " + _USER_WINDOW
        ),
        "insights_equivalent": "context_effect.share_later_with_prior_context",
    },
]

QUERIES_BY_NAME = {q["name"]: q for q in KEEWANO_QUERIES}


def _window(since: datetime | None, until: datetime | None) -> tuple[str, str]:
    until = until or datetime.now(UTC)
    since = since or (until - timedelta(days=DEFAULT_WINDOW_DAYS))
    return since.date().isoformat(), (until + timedelta(days=1)).date().isoformat()


def render_arguments(
    query: dict, project_id: str, user_id: str, since: datetime | None = None, until: datetime | None = None
) -> dict:
    """Tool arguments for `tools/call`, with the user id and window substituted (plain replace, no format())."""
    since_text, until_text = _window(since, until)

    def fill(text: str) -> str:
        return text.replace("{user_id}", str(user_id)).replace("{since}", since_text).replace("{until}", until_text)

    if query["tool"] == "run_sql_query":
        return {"projectId": project_id, "sqlQuery": fill(query["sql"]), "maxRows": 500}
    if query["tool"] == "run_script_query":
        filters = [
            {"operation": f["operation"], "value": [fill(v) if isinstance(v, str) else v for v in f["value"]]}
            if isinstance(f.get("value"), list)
            else f
            for f in query.get("filters", [])
        ]
        return {"projectId": project_id, "luaScript": query["lua"], "filters": filters}
    raise ValueError(f"unsupported tool {query['tool']}")


def custom_event_ids() -> dict[str, int]:
    """Our registered event names and ids, for report tools that take numeric ids."""
    return dict(EVENT_TYPE_IDS)


def mcp_token() -> str:
    """The token for Keewano's MCP endpoint: KEEWANO_PAT, else KEEWANO_TOKEN, else empty."""
    return os.environ.get("KEEWANO_PAT") or os.environ.get("KEEWANO_TOKEN") or ""


def _headers(token: str) -> dict[str, str]:
    return {"k-token": token, "content-type": "application/json", "accept": "application/json, text/event-stream"}


def _parse_rpc(response: httpx.Response) -> dict:
    """{"ok": bool, "result": ..., "error": str|None} from a JSON-RPC reply (plain JSON or one SSE data line)."""
    text = response.text or ""
    body: Any = None
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    body = httpx.Response(200, content=line[5:].strip()).json()
                except ValueError:
                    body = None
    else:
        try:
            body = response.json()
        except ValueError:
            body = None
    if response.status_code >= 400:
        return {"ok": False, "result": None, "error": f"http {response.status_code}: {text[:200]}"}
    if not isinstance(body, dict):
        return {"ok": False, "result": None, "error": f"unreadable reply: {text[:200]}"}
    if body.get("error"):
        return {"ok": False, "result": None, "error": str(body["error"])[:300]}
    result = body.get("result")
    if not isinstance(result, dict):
        return {"ok": False, "result": None, "error": "no result"}
    if result.get("isError"):
        texts = [c.get("text") for c in result.get("content") or [] if isinstance(c, dict) and c.get("text")]
        return {"ok": False, "result": None, "error": (" ".join(texts) or "tool error")[:300]}
    structured = result.get("structuredContent")
    if structured is None:
        texts = [c.get("text") for c in result.get("content") or [] if isinstance(c, dict) and c.get("text")]
        structured = {"text": "\n".join(texts)}
    return {"ok": True, "result": structured, "error": None}


async def mcp_tool_call(client: httpx.AsyncClient, token: str, name: str, arguments: dict, *, rpc_id: int = 1) -> dict:
    payload = {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    response = await client.post(MCP_URL, json=payload, headers=_headers(token))
    return _parse_rpc(response)


async def _discover_project(client: httpx.AsyncClient, token: str) -> str | None:
    reply = await mcp_tool_call(client, token, "list_projects", {})
    projects = (reply.get("result") or {}).get("projects") if reply.get("ok") else None
    if isinstance(projects, list) and projects and isinstance(projects[0], dict):
        return str(projects[0].get("id") or "") or None
    return None


async def keewano_insights(
    user_id: str,
    *,
    project_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    timeout: float = TIMEOUT_SECONDS,
    include: tuple[str, ...] = (RUNNABLE_TODAY, AFTER_INGEST),
) -> dict | None:
    """Run the Keewano-side queries for one user over MCP. None on any transport failure; never raises.

    Queries whose status is not in `include` are reported as skipped with the reason. Per-query errors
    (for example "unknown column" before our types are registered) are recorded, not raised.
    """
    token = mcp_token()
    if not token or not user_id:
        logger.info("keewano_insights skipped: %s", "no KEEWANO_PAT/KEEWANO_TOKEN" if not token else "no user_id")
        return None
    started = datetime.now(UTC)
    results: dict[str, dict] = {}
    note: str | None = None
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout) as client:
                project = project_id or os.environ.get("KEEWANO_PROJECT_ID") or await _discover_project(client, token)
                if not project:
                    logger.warning("keewano_insights: no project visible to this token")
                    return None
                for i, query in enumerate(KEEWANO_QUERIES, start=2):
                    if query["status"] not in include:
                        results[query["name"]] = {"ok": False, "skipped": query["status"], "question": query["question"]}
                        continue
                    reply = await mcp_tool_call(
                        client, token, query["tool"], render_arguments(query, project, user_id, since, until), rpc_id=i
                    )
                    results[query["name"]] = {**reply, "question": query["question"], "status": query["status"]}
                    if not reply["ok"] and PLAN_LIMIT_MARKER in (reply.get("error") or "").lower():
                        note = "Keewano plan limit reached; remaining queries not attempted"
                        break
    except TimeoutError:
        note = f"stopped after {timeout:.0f}s; partial results"
        if not results:
            logger.warning("keewano_insights: timed out before any answer")
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("keewano_insights failed: %s: %s", type(exc).__name__, exc)
        return None
    n_ok = sum(1 for r in results.values() if r.get("ok"))
    if n_ok == 0 and not note:
        note = "no query answered; the project has no Dormammu events until ingest accepts a project API key"
    return {
        "project_id": project,
        "user_id": user_id,
        "fetched_at": started.isoformat(),
        "elapsed_seconds": round((datetime.now(UTC) - started).total_seconds(), 2),
        "n_queries": len(results),
        "n_ok": n_ok,
        "note": note,
        "queries": results,
    }
