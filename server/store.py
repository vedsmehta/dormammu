"""Store interface plus the two backends (docs/CONTRACT.md).

SupabaseStore talks PostgREST through supabase-py with the server-only secret key. SqliteStore is a
stdlib sqlite3 mirror of supabase/migrations/0001_init.sql for tests and no-Supabase dev. Both return
plain dicts whose keys are the column names; json columns come back parsed, booleans as bool,
timestamps as ISO-8601 strings with a timezone.

One deliberate deviation from the contract text: claim_extraction uses state_rank as the claim token
(rank 5, between extracting=4 and done=6) instead of `state <> 'extracting'`. ingest maps
Terminated/completed to state 'extracting' before the tick ever reaches the claim, so the literal
guard would refuse every claim. `WHERE outcome IS NULL AND state_rank < 5` keeps the exactly-once
property and stays monotonic: a late rank-4 write is refused, done (6) still lands.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from settings import ROOT, load_env

load_env()

TERMINAL_STATES = ("done", "failed")
CLAIM_RANK = 5

PROFILE_COLUMNS = ("id", "display_name", "phone", "prefs", "cohort", "created_at")
VENDOR_COLUMNS = ("id", "owner_id", "key", "name", "track", "phone", "is_demo")
RUN_COLUMNS = ("id", "user_id", "request", "status", "created_at", "completed_at")
CALL_COLUMNS = (
    "id",
    "run_id",
    "vendor_id",
    "vendor_key",
    "track",
    "target_name",
    "target_phone",
    "objective",
    "prior_context",
    "dial_call_id",
    "state",
    "state_rank",
    "termination_type",
    "transcript",
    "transcript_turns",
    "outcome",
    "simulated",
    "error",
    "duration_seconds",
    "started_at",
    "ended_at",
)
EVENT_COLUMNS = ("id", "user_id", "run_id", "call_id", "type", "data", "created_at")
LEDGER_COLUMNS = (
    "id",
    "user_id",
    "vendor_id",
    "kind",
    "label",
    "amount_usd",
    "effective_on",
    "expires_on",
    "source_call",
    "created_at",
)

JSON_COLUMNS: dict[str, set[str]] = {
    "profiles": {"prefs"},
    "calls": {"transcript_turns", "outcome"},
    "events": {"data"},
    "plan_cache": {"plans"},
}
BOOL_COLUMNS: dict[str, set[str]] = {"vendors": {"is_demo"}, "calls": {"simulated"}}
DATE_COLUMNS: dict[str, set[str]] = {"ledger": {"effective_on", "expires_on"}}


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _pick(columns: tuple[str, ...], fields: dict) -> dict:
    return {k: v for k, v in fields.items() if k in columns}


def _date_only(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 10:
        return value[:10]
    return value


class Store(Protocol):
    def get_profile(self, user_id: str) -> dict | None: ...
    def upsert_profile(self, user_id: str, fields: dict) -> dict: ...
    def list_vendors(self, user_id: str) -> list[dict]: ...
    def upsert_vendors(self, user_id: str, vendors: list[dict]) -> list[dict]: ...
    def create_run(self, user_id: str, request: str) -> dict: ...
    def get_run(self, run_id: str, user_id: str | None = None) -> dict | None: ...
    def list_runs(self, user_id: str, limit: int = 20) -> list[dict]: ...
    def set_run_status(self, run_id: str, status: str, completed_at: str | None = None) -> None: ...
    def insert_calls(self, rows: list[dict]) -> list[dict]: ...
    def list_calls(self, run_id: str, nonterminal_only: bool = False) -> list[dict]: ...
    def get_call(self, call_id: str) -> dict | None: ...
    def update_call(self, call_id: str, fields: dict) -> dict | None: ...
    def update_call_state(self, call_id: str, state: str, rank: int, fields: dict | None = None) -> bool: ...
    def claim_extraction(self, call_id: str) -> bool: ...
    def insert_event(
        self, user_id: str, type: str, data: dict, run_id: str | None = None, call_id: str | None = None
    ) -> dict: ...
    def list_events(self, user_id: str, limit: int = 50) -> list[dict]: ...
    def last_outcomes_for_vendor(self, user_id: str, vendor_key: str, limit: int = 3) -> list[dict]: ...
    def get_plan_cache(self, request_hash: str) -> list[dict] | None: ...
    def put_plan_cache(self, request_hash: str, plans: list[dict]) -> None: ...
    def insert_ledger(self, rows: list[dict]) -> list[dict]: ...
    def list_ledger(self, user_id: str) -> list[dict]: ...
    def due_ledger(self, user_id: str, as_of: str) -> list[dict]: ...


# ---------------------------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------------------------

SQLITE_DDL = """
create table if not exists profiles (
  id           text primary key,
  display_name text,
  phone        text,
  prefs        text not null default '{}',
  cohort       text not null default 'student',
  created_at   text not null
);
create table if not exists vendors (
  id        text primary key,
  owner_id  text not null references profiles(id) on delete cascade,
  key       text not null,
  name      text not null,
  track     text not null check (track in ('quote','schedule','negotiate')),
  phone     text not null,
  is_demo   integer not null default 0,
  unique (owner_id, key)
);
create table if not exists runs (
  id           text primary key,
  user_id      text not null references profiles(id) on delete cascade,
  request      text not null,
  status       text not null default 'dispatching'
                 check (status in ('dispatching','running','complete','failed')),
  created_at   text not null,
  completed_at text
);
create table if not exists calls (
  id               text primary key,
  run_id           text not null references runs(id) on delete cascade,
  vendor_id        text references vendors(id) on delete set null,
  vendor_key       text,
  track            text not null,
  target_name      text not null,
  target_phone     text not null,
  objective        text not null,
  prior_context    text,
  dial_call_id     text unique,
  state            text not null default 'planned',
  state_rank       integer not null default 0,
  termination_type text,
  transcript       text,
  transcript_turns text,
  outcome          text,
  simulated        integer not null default 0,
  error            text,
  duration_seconds real,
  started_at       text not null,
  ended_at         text
);
create index if not exists calls_run_id_idx on calls (run_id);
create index if not exists calls_dial_call_id_idx on calls (dial_call_id);
create index if not exists calls_vendor_key_idx on calls (vendor_key);
create table if not exists events (
  id         integer primary key autoincrement,
  user_id    text not null references profiles(id) on delete cascade,
  run_id     text references runs(id) on delete cascade,
  call_id    text references calls(id) on delete cascade,
  type       text not null,
  data       text not null default '{}',
  created_at text not null
);
create index if not exists events_user_created_idx on events (user_id, created_at desc);
create index if not exists events_call_id_idx on events (call_id);
create table if not exists plan_cache (
  request_hash text primary key,
  plans        text not null,
  created_at   text not null
);
create table if not exists ledger (
  id           text primary key,
  user_id      text not null references profiles(id) on delete cascade,
  vendor_id    text references vendors(id) on delete set null,
  kind         text not null check (kind in ('promo_expiry','renewal','appointment','quote')),
  label        text not null,
  amount_usd   real,
  effective_on text,
  expires_on   text,
  source_call  text references calls(id) on delete set null,
  created_at   text not null
);
create index if not exists ledger_user_expires_idx on ledger (user_id, expires_on);
"""


def default_sqlite_path() -> Path:
    return Path(os.environ.get("DORMAMMU_DB_PATH") or (ROOT / ".calls" / "dormammu.db"))


class SqliteStore:
    """stdlib sqlite3 mirror of the Postgres schema. Foreign keys are declared, not enforced."""

    name = "sqlite"

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else default_sqlite_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma journal_mode=wal")
        with self._lock, self._conn:
            self._conn.executescript(SQLITE_DDL)

    # -- helpers --

    @staticmethod
    def _encode(table: str, fields: dict) -> dict:
        out: dict[str, Any] = {}
        for k, v in fields.items():
            if k in JSON_COLUMNS.get(table, ()):
                out[k] = json.dumps(v) if v is not None else None
            elif k in BOOL_COLUMNS.get(table, ()):
                out[k] = int(bool(v)) if v is not None else None
            elif k in DATE_COLUMNS.get(table, ()):
                out[k] = _date_only(v)
            elif isinstance(v, datetime):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out

    @staticmethod
    def _decode(table: str, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        out = dict(row)
        for k in JSON_COLUMNS.get(table, ()):
            if isinstance(out.get(k), str):
                out[k] = json.loads(out[k])
        for k in BOOL_COLUMNS.get(table, ()):
            if out.get(k) is not None:
                out[k] = bool(out[k])
        return out

    def _insert(self, table: str, fields: dict) -> None:
        enc = self._encode(table, fields)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        with self._lock, self._conn:
            self._conn.execute(f"insert into {table} ({cols}) values ({marks})", list(enc.values()))

    def _update(self, table: str, fields: dict, where: str, params: list) -> int:
        enc = self._encode(table, fields)
        if not enc:
            return 0
        sets = ", ".join(f"{k} = ?" for k in enc)
        with self._lock, self._conn:
            cur = self._conn.execute(f"update {table} set {sets} where {where}", [*enc.values(), *params])
            return cur.rowcount

    def _one(self, table: str, sql: str, params: list) -> dict | None:
        with self._lock:
            return self._decode(table, self._conn.execute(sql, params).fetchone())

    def _many(self, table: str, sql: str, params: list) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(table, r) for r in rows]  # type: ignore[misc]

    # -- profiles --

    def get_profile(self, user_id: str) -> dict | None:
        return self._one("profiles", "select * from profiles where id = ?", [user_id])

    def upsert_profile(self, user_id: str, fields: dict) -> dict:
        fields = _pick(PROFILE_COLUMNS, {k: v for k, v in fields.items() if k != "id"})
        existing = self.get_profile(user_id)
        if existing is None:
            row = {"id": user_id, "prefs": {}, "cohort": "student", "created_at": utcnow_iso()}
            row.update({k: v for k, v in fields.items() if v is not None})
            self._insert("profiles", row)
        elif fields:
            self._update("profiles", fields, "id = ?", [user_id])
        return self.get_profile(user_id)  # type: ignore[return-value]

    # -- vendors --

    def list_vendors(self, user_id: str) -> list[dict]:
        return self._many("vendors", "select * from vendors where owner_id = ? order by key", [user_id])

    def upsert_vendors(self, user_id: str, vendors: list[dict]) -> list[dict]:
        out: list[dict] = []
        for v in vendors:
            fields = _pick(VENDOR_COLUMNS, v)
            fields.pop("id", None)
            fields["owner_id"] = user_id
            fields.setdefault("is_demo", False)
            existing = self._one(
                "vendors", "select * from vendors where owner_id = ? and key = ?", [user_id, fields["key"]]
            )
            if existing is None:
                fields["id"] = new_id()
                self._insert("vendors", fields)
                vid = fields["id"]
            else:
                self._update("vendors", fields, "id = ?", [existing["id"]])
                vid = existing["id"]
            out.append(self._one("vendors", "select * from vendors where id = ?", [vid]))  # type: ignore[arg-type]
        return out

    # -- runs --

    def create_run(self, user_id: str, request: str) -> dict:
        row = {
            "id": new_id(),
            "user_id": user_id,
            "request": request,
            "status": "dispatching",
            "created_at": utcnow_iso(),
            "completed_at": None,
        }
        self._insert("runs", row)
        return self._one("runs", "select * from runs where id = ?", [row["id"]])  # type: ignore[return-value]

    def get_run(self, run_id: str, user_id: str | None = None) -> dict | None:
        sql, params = "select * from runs where id = ?", [run_id]
        if user_id is not None:
            sql += " and user_id = ?"
            params.append(user_id)
        run = self._one("runs", sql, params)
        if run is None:
            return None
        run["calls"] = self.list_calls(run_id)
        return run

    def list_runs(self, user_id: str, limit: int = 20) -> list[dict]:
        return self._many(
            "runs", "select * from runs where user_id = ? order by created_at desc limit ?", [user_id, limit]
        )

    def set_run_status(self, run_id: str, status: str, completed_at: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if completed_at is not None:
            fields["completed_at"] = completed_at
        self._update("runs", fields, "id = ?", [run_id])

    # -- calls --

    def insert_calls(self, rows: list[dict]) -> list[dict]:
        ids: list[str] = []
        now = utcnow_iso()
        for r in rows:
            fields = _pick(CALL_COLUMNS, r)
            fields.setdefault("id", new_id())
            fields.setdefault("state", "planned")
            fields.setdefault("state_rank", 0)
            fields.setdefault("simulated", False)
            fields.setdefault("started_at", now)
            self._insert("calls", fields)
            ids.append(fields["id"])
        return [self.get_call(i) for i in ids]  # type: ignore[misc]

    def list_calls(self, run_id: str, nonterminal_only: bool = False) -> list[dict]:
        sql = "select * from calls where run_id = ?"
        if nonterminal_only:
            sql += " and state not in ('done','failed')"
        return self._many("calls", sql + " order by started_at, rowid", [run_id])

    def get_call(self, call_id: str) -> dict | None:
        return self._one("calls", "select * from calls where id = ?", [call_id])

    def update_call(self, call_id: str, fields: dict) -> dict | None:
        fields = _pick(CALL_COLUMNS, {k: v for k, v in fields.items() if k != "id"})
        if fields:
            self._update("calls", fields, "id = ?", [call_id])
        return self.get_call(call_id)

    def update_call_state(self, call_id: str, state: str, rank: int, fields: dict | None = None) -> bool:
        payload = _pick(CALL_COLUMNS, {k: v for k, v in (fields or {}).items() if k != "id"})
        payload.update({"state": state, "state_rank": rank})
        return self._update("calls", payload, "id = ? and state_rank < ?", [call_id, rank]) > 0

    def claim_extraction(self, call_id: str) -> bool:
        return (
            self._update(
                "calls",
                {"state": "extracting", "state_rank": CLAIM_RANK},
                "id = ? and outcome is null and state_rank < ?",
                [call_id, CLAIM_RANK],
            )
            > 0
        )

    # -- events --

    def insert_event(
        self, user_id: str, type: str, data: dict, run_id: str | None = None, call_id: str | None = None
    ) -> dict:
        row = {
            "user_id": user_id,
            "run_id": run_id,
            "call_id": call_id,
            "type": type,
            "data": data or {},
            "created_at": utcnow_iso(),
        }
        enc = self._encode("events", row)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        with self._lock, self._conn:
            cur = self._conn.execute(f"insert into events ({cols}) values ({marks})", list(enc.values()))
            rowid = cur.lastrowid
        return self._one("events", "select * from events where id = ?", [rowid])  # type: ignore[return-value]

    def list_events(self, user_id: str, limit: int = 50) -> list[dict]:
        return self._many(
            "events",
            "select * from events where user_id = ? order by created_at desc, id desc limit ?",
            [user_id, limit],
        )

    def last_outcomes_for_vendor(self, user_id: str, vendor_key: str, limit: int = 3) -> list[dict]:
        return self._many(
            "calls",
            "select calls.* from calls join runs on runs.id = calls.run_id "
            "where runs.user_id = ? and calls.vendor_key = ? and calls.outcome is not null "
            "order by calls.started_at desc, calls.rowid desc limit ?",
            [user_id, vendor_key, limit],
        )

    # -- plan cache --

    def get_plan_cache(self, request_hash: str) -> list[dict] | None:
        row = self._one("plan_cache", "select * from plan_cache where request_hash = ?", [request_hash])
        return row["plans"] if row else None

    def put_plan_cache(self, request_hash: str, plans: list[dict]) -> None:
        enc = self._encode(
            "plan_cache", {"request_hash": request_hash, "plans": plans, "created_at": utcnow_iso()}
        )
        with self._lock, self._conn:
            self._conn.execute(
                "insert into plan_cache (request_hash, plans, created_at) values (?, ?, ?) "
                "on conflict(request_hash) do update set plans = excluded.plans, created_at = excluded.created_at",
                [enc["request_hash"], enc["plans"], enc["created_at"]],
            )

    # -- ledger --

    def insert_ledger(self, rows: list[dict]) -> list[dict]:
        ids: list[str] = []
        now = utcnow_iso()
        for r in rows:
            fields = _pick(LEDGER_COLUMNS, r)
            fields.setdefault("id", new_id())
            fields.setdefault("created_at", now)
            self._insert("ledger", fields)
            ids.append(fields["id"])
        marks = ", ".join("?" for _ in ids) or "''"
        rows_out = self._many("ledger", f"select * from ledger where id in ({marks})", ids)
        by_id = {r["id"]: r for r in rows_out}
        return [by_id[i] for i in ids if i in by_id]

    def list_ledger(self, user_id: str) -> list[dict]:
        return self._many(
            "ledger",
            "select * from ledger where user_id = ? order by expires_on is null, expires_on, created_at",
            [user_id],
        )

    def due_ledger(self, user_id: str, as_of: str) -> list[dict]:
        return self._many(
            "ledger",
            "select * from ledger where user_id = ? and expires_on is not null and expires_on <= ? "
            "order by expires_on, created_at",
            [user_id, _date_only(as_of)],
        )


# ---------------------------------------------------------------------------------------------
# Supabase (PostgREST via supabase-py)
# ---------------------------------------------------------------------------------------------


class SupabaseStore:
    """PostgREST-backed store. Uses the secret key, so it bypasses RLS: server-side only."""

    name = "supabase"

    def __init__(self, url: str | None = None, key: str | None = None) -> None:
        from supabase import create_client

        url = url or os.environ.get("SUPABASE_URL")
        key = key or os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SECRET_KEY are required for SupabaseStore")
        self.client = create_client(url, key)

    def _t(self, table: str):
        return self.client.table(table)

    @staticmethod
    def _rows(resp) -> list[dict]:
        data = resp.data
        return list(data) if isinstance(data, list) else []

    def _first(self, resp) -> dict | None:
        rows = self._rows(resp)
        return rows[0] if rows else None

    @staticmethod
    def _ledger_payload(r: dict) -> dict:
        fields = _pick(LEDGER_COLUMNS, r)
        for k in DATE_COLUMNS["ledger"]:
            if k in fields:
                fields[k] = _date_only(fields[k])
        fields.pop("created_at", None)
        return fields

    # -- profiles --

    def get_profile(self, user_id: str) -> dict | None:
        return self._first(self._t("profiles").select("*").eq("id", user_id).limit(1).execute())

    def upsert_profile(self, user_id: str, fields: dict) -> dict:
        payload = {k: v for k, v in _pick(PROFILE_COLUMNS, fields).items() if k not in ("id", "created_at")}
        payload["id"] = user_id
        row = self._first(self._t("profiles").upsert(payload, on_conflict="id").execute())
        return row or self.get_profile(user_id)  # type: ignore[return-value]

    # -- vendors --

    def list_vendors(self, user_id: str) -> list[dict]:
        return self._rows(self._t("vendors").select("*").eq("owner_id", user_id).order("key").execute())

    def upsert_vendors(self, user_id: str, vendors: list[dict]) -> list[dict]:
        payload = []
        for v in vendors:
            fields = _pick(VENDOR_COLUMNS, v)
            fields.pop("id", None)
            fields["owner_id"] = user_id
            fields.setdefault("is_demo", False)
            payload.append(fields)
        if not payload:
            return []
        return self._rows(self._t("vendors").upsert(payload, on_conflict="owner_id,key").execute())

    # -- runs --

    def create_run(self, user_id: str, request: str) -> dict:
        resp = (
            self._t("runs")
            .insert({"user_id": user_id, "request": request, "status": "dispatching"})
            .execute()
        )
        return self._first(resp)  # type: ignore[return-value]

    def get_run(self, run_id: str, user_id: str | None = None) -> dict | None:
        q = self._t("runs").select("*").eq("id", run_id)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        run = self._first(q.limit(1).execute())
        if run is None:
            return None
        run["calls"] = self.list_calls(run_id)
        return run

    def list_runs(self, user_id: str, limit: int = 20) -> list[dict]:
        q = self._t("runs").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit)
        return self._rows(q.execute())

    def set_run_status(self, run_id: str, status: str, completed_at: str | None = None) -> None:
        fields: dict[str, Any] = {"status": status}
        if completed_at is not None:
            fields["completed_at"] = completed_at
        self._t("runs").update(fields).eq("id", run_id).execute()

    # -- calls --

    def insert_calls(self, rows: list[dict]) -> list[dict]:
        payload = []
        for r in rows:
            fields = _pick(CALL_COLUMNS, r)
            fields.pop("id", None)
            fields.setdefault("state", "planned")
            fields.setdefault("state_rank", 0)
            fields.setdefault("simulated", False)
            payload.append(fields)
        if not payload:
            return []
        return self._rows(self._t("calls").insert(payload).execute())

    def list_calls(self, run_id: str, nonterminal_only: bool = False) -> list[dict]:
        q = self._t("calls").select("*").eq("run_id", run_id)
        if nonterminal_only:
            q = q.not_.in_("state", list(TERMINAL_STATES))
        return self._rows(q.order("started_at").execute())

    def get_call(self, call_id: str) -> dict | None:
        return self._first(self._t("calls").select("*").eq("id", call_id).limit(1).execute())

    def update_call(self, call_id: str, fields: dict) -> dict | None:
        payload = _pick(CALL_COLUMNS, {k: v for k, v in fields.items() if k != "id"})
        if not payload:
            return self.get_call(call_id)
        return self._first(self._t("calls").update(payload).eq("id", call_id).execute())

    def update_call_state(self, call_id: str, state: str, rank: int, fields: dict | None = None) -> bool:
        payload = _pick(CALL_COLUMNS, {k: v for k, v in (fields or {}).items() if k != "id"})
        payload.update({"state": state, "state_rank": rank})
        resp = self._t("calls").update(payload).eq("id", call_id).lt("state_rank", rank).execute()
        return len(self._rows(resp)) > 0

    def claim_extraction(self, call_id: str) -> bool:
        resp = (
            self._t("calls")
            .update({"state": "extracting", "state_rank": CLAIM_RANK})
            .eq("id", call_id)
            .is_("outcome", "null")
            .lt("state_rank", CLAIM_RANK)
            .execute()
        )
        return len(self._rows(resp)) > 0

    # -- events --

    def insert_event(
        self, user_id: str, type: str, data: dict, run_id: str | None = None, call_id: str | None = None
    ) -> dict:
        payload = {"user_id": user_id, "run_id": run_id, "call_id": call_id, "type": type, "data": data or {}}
        return self._first(self._t("events").insert(payload).execute())  # type: ignore[return-value]

    def list_events(self, user_id: str, limit: int = 50) -> list[dict]:
        q = (
            self._t("events")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(limit)
        )
        return self._rows(q.execute())

    def last_outcomes_for_vendor(self, user_id: str, vendor_key: str, limit: int = 3) -> list[dict]:
        q = (
            self._t("calls")
            .select("*, runs!inner(user_id)")
            .eq("runs.user_id", user_id)
            .eq("vendor_key", vendor_key)
            .not_.is_("outcome", "null")
            .order("started_at", desc=True)
            .limit(limit)
        )
        rows = self._rows(q.execute())
        for r in rows:
            r.pop("runs", None)
        return rows

    # -- plan cache --

    def get_plan_cache(self, request_hash: str) -> list[dict] | None:
        row = self._first(
            self._t("plan_cache").select("plans").eq("request_hash", request_hash).limit(1).execute()
        )
        return row["plans"] if row else None

    def put_plan_cache(self, request_hash: str, plans: list[dict]) -> None:
        self._t("plan_cache").upsert(
            {"request_hash": request_hash, "plans": plans}, on_conflict="request_hash"
        ).execute()

    # -- ledger --

    def insert_ledger(self, rows: list[dict]) -> list[dict]:
        payload = [self._ledger_payload(r) for r in rows]
        payload = [p for p in payload if p]
        if not payload:
            return []
        return self._rows(self._t("ledger").insert(payload).execute())

    def list_ledger(self, user_id: str) -> list[dict]:
        q = (
            self._t("ledger")
            .select("*")
            .eq("user_id", user_id)
            .order("expires_on", nullsfirst=False)
            .order("created_at")
        )
        return self._rows(q.execute())

    def due_ledger(self, user_id: str, as_of: str) -> list[dict]:
        q = (
            self._t("ledger")
            .select("*")
            .eq("user_id", user_id)
            .lte("expires_on", _date_only(as_of))
            .order("expires_on")
            .order("created_at")
        )
        return self._rows(q.execute())


def get_store() -> Store:
    """Supabase when SUPABASE_URL and SUPABASE_SECRET_KEY are set, else sqlite at .calls/dormammu.db."""
    load_env()
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    return SqliteStore()
