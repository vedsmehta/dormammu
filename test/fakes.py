"""In-memory Store for tests. Implements every method of the contract's Store protocol
(docs/CONTRACT.md) with the same semantics as the Postgres backend: monotonic state writes,
atomic extraction claim, newest-first listings. Shared across lanes; keep it faithful."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime

from call.state import STATE_RANK, is_terminal


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def call_row(**overrides) -> dict:
    """A planned calls row with every contract column present. Override what the test cares about."""
    row = {
        "id": new_id(),
        "run_id": overrides.get("run_id") or new_id(),
        "vendor_id": new_id(),
        "vendor_key": "movers_a",
        "track": "quote",
        "target_name": "Bay Movers",
        "target_phone": "+14155550100",
        "objective": "Get a quote to move a 2-bedroom apartment on Nov 1.",
        "prior_context": None,
        "dial_call_id": None,
        "state": "planned",
        "state_rank": 0,
        "termination_type": None,
        "transcript": None,
        "transcript_turns": None,
        "outcome": None,
        "simulated": False,
        "error": None,
        "duration_seconds": None,
        "started_at": now_iso(),
        "ended_at": None,
    }
    row.update(overrides)
    return row


class FakeStore:
    def __init__(self) -> None:
        self.profiles: dict[str, dict] = {}
        self.vendors: list[dict] = []
        self.runs: dict[str, dict] = {}
        self.calls: dict[str, dict] = {}
        self.events: list[dict] = []
        self.plan_cache: dict[str, list[dict]] = {}
        self.ledger: list[dict] = []
        self._event_seq = 0

    # ----- helpers -----------------------------------------------------------------------------

    @staticmethod
    def _copy(row):
        return copy.deepcopy(row)

    def events_of_type(self, type: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type]

    def seed_run(self, user_id: str, request: str = "test request", calls: list[dict] | None = None) -> dict:
        """Create a run and attach the given call rows (ids/run_id filled in). Returns get_run()."""
        run = self.create_run(user_id, request)
        rows = []
        for c in calls or []:
            c = dict(c)
            c["run_id"] = run["id"]
            c.setdefault("id", new_id())
            rows.append(c)
        if rows:
            self.insert_calls(rows)
        return self.get_run(run["id"])

    # ----- profiles -----------------------------------------------------------------------------

    def get_profile(self, user_id: str) -> dict | None:
        row = self.profiles.get(user_id)
        return self._copy(row) if row else None

    def upsert_profile(self, user_id: str, fields: dict) -> dict:
        row = self.profiles.get(user_id) or {
            "id": user_id,
            "display_name": None,
            "phone": None,
            "prefs": {},
            "cohort": "student",
            "created_at": now_iso(),
        }
        row.update({k: v for k, v in fields.items() if k != "id"})
        self.profiles[user_id] = row
        return self._copy(row)

    # ----- vendors ------------------------------------------------------------------------------

    def list_vendors(self, user_id: str) -> list[dict]:
        return [self._copy(v) for v in self.vendors if v["owner_id"] == user_id]

    def upsert_vendors(self, user_id: str, vendors: list[dict]) -> list[dict]:
        out = []
        for v in vendors:
            existing = next(
                (x for x in self.vendors if x["owner_id"] == user_id and x["key"] == v["key"]), None
            )
            if existing is None:
                existing = {"id": new_id(), "owner_id": user_id, "is_demo": False}
                self.vendors.append(existing)
            existing.update({k: v[k] for k in ("key", "name", "track", "phone", "is_demo") if k in v})
            out.append(self._copy(existing))
        return out

    # ----- runs ---------------------------------------------------------------------------------

    def create_run(self, user_id: str, request: str) -> dict:
        row = {
            "id": new_id(),
            "user_id": user_id,
            "request": request,
            "status": "dispatching",
            "created_at": now_iso(),
            "completed_at": None,
        }
        self.runs[row["id"]] = row
        return self._copy(row)

    def get_run(self, run_id: str, user_id: str | None = None) -> dict | None:
        row = self.runs.get(run_id)
        if row is None or (user_id is not None and row["user_id"] != user_id):
            return None
        out = self._copy(row)
        out["calls"] = self.list_calls(run_id)
        return out

    def list_runs(self, user_id: str, limit: int = 20) -> list[dict]:
        rows = [r for r in self.runs.values() if r["user_id"] == user_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [self._copy(r) for r in rows[:limit]]

    def set_run_status(self, run_id: str, status: str, completed_at: str | None = None) -> None:
        row = self.runs.get(run_id)
        if row is None:
            return
        row["status"] = status
        if completed_at is not None:
            row["completed_at"] = completed_at

    # ----- calls --------------------------------------------------------------------------------

    def insert_calls(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            row = call_row(**r)
            row["state"] = r.get("state", "planned")
            row["state_rank"] = r.get("state_rank", STATE_RANK[row["state"]])
            self.calls[row["id"]] = row
            out.append(self._copy(row))
        return out

    def list_calls(self, run_id: str, nonterminal_only: bool = False) -> list[dict]:
        rows = [c for c in self.calls.values() if c["run_id"] == run_id]
        if nonterminal_only:
            rows = [c for c in rows if not is_terminal(c["state"])]
        rows.sort(key=lambda c: (c.get("started_at") or "", c["id"]))
        return [self._copy(c) for c in rows]

    def get_call(self, call_id: str) -> dict | None:
        row = self.calls.get(call_id)
        return self._copy(row) if row else None

    def update_call(self, call_id: str, fields: dict) -> dict | None:
        row = self.calls.get(call_id)
        if row is None:
            return None
        row.update({k: copy.deepcopy(v) for k, v in fields.items() if k != "id"})
        return self._copy(row)

    def update_call_state(self, call_id: str, state: str, rank: int, fields: dict | None = None) -> bool:
        row = self.calls.get(call_id)
        if row is None or not row["state_rank"] < rank:
            return False
        row["state"] = state
        row["state_rank"] = rank
        if fields:
            row.update({k: copy.deepcopy(v) for k, v in fields.items() if k != "id"})
        return True

    def claim_extraction(self, call_id: str) -> bool:
        row = self.calls.get(call_id)
        if row is None or row.get("outcome") is not None or row["state"] == "extracting":
            return False
        row["state"] = "extracting"
        row["state_rank"] = STATE_RANK["extracting"]
        return True

    # ----- events -------------------------------------------------------------------------------

    def insert_event(
        self, user_id: str, type: str, data: dict, run_id: str | None = None, call_id: str | None = None
    ) -> dict:
        self._event_seq += 1
        row = {
            "id": self._event_seq,
            "user_id": user_id,
            "run_id": run_id,
            "call_id": call_id,
            "type": type,
            "data": copy.deepcopy(data or {}),
            "created_at": now_iso(),
        }
        self.events.append(row)
        return self._copy(row)

    def list_events(self, user_id: str, limit: int = 50) -> list[dict]:
        rows = [e for e in self.events if e["user_id"] == user_id]
        rows.sort(key=lambda e: e["id"], reverse=True)
        return [self._copy(e) for e in rows[:limit]]

    def last_outcomes_for_vendor(self, user_id: str, vendor_key: str, limit: int = 3) -> list[dict]:
        run_ids = {r["id"] for r in self.runs.values() if r["user_id"] == user_id}
        rows = [
            c
            for c in self.calls.values()
            if c["run_id"] in run_ids
            and c.get("vendor_key") == vendor_key
            and c["state"] == "done"
            and c.get("outcome") is not None
        ]
        rows.sort(key=lambda c: c.get("ended_at") or c.get("started_at") or "", reverse=True)
        return [self._copy(c) for c in rows[:limit]]

    # ----- plan cache ---------------------------------------------------------------------------

    def get_plan_cache(self, request_hash: str) -> list[dict] | None:
        plans = self.plan_cache.get(request_hash)
        return self._copy(plans) if plans is not None else None

    def put_plan_cache(self, request_hash: str, plans: list[dict]) -> None:
        self.plan_cache[request_hash] = self._copy(plans)

    # ----- ledger -------------------------------------------------------------------------------

    def insert_ledger(self, rows: list[dict]) -> list[dict]:
        out = []
        for r in rows:
            row = {
                "id": new_id(),
                "user_id": None,
                "vendor_id": None,
                "kind": None,
                "label": None,
                "amount_usd": None,
                "effective_on": None,
                "expires_on": None,
                "source_call": None,
                "created_at": now_iso(),
            }
            row.update(r)
            self.ledger.append(row)
            out.append(self._copy(row))
        return out

    def list_ledger(self, user_id: str) -> list[dict]:
        rows = [r for r in self.ledger if r["user_id"] == user_id]
        rows.sort(key=lambda r: (r.get("expires_on") or "", r["created_at"]))
        return [self._copy(r) for r in rows]

    def due_ledger(self, user_id: str, as_of: str) -> list[dict]:
        return [
            self._copy(r)
            for r in self.ledger
            if r["user_id"] == user_id and r.get("expires_on") is not None and str(r["expires_on"]) <= as_of
        ]
