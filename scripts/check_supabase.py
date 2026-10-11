"""Check that every table from supabase/migrations/0001_init.sql is reachable through PostgREST.

    uv run python scripts/check_supabase.py

Prints ok/missing per table with the row count, exits nonzero if any table is missing.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postgrest.types import CountMethod

from server.store import SupabaseStore
from settings import load_env

TABLES = ("profiles", "vendors", "runs", "calls", "events", "plan_cache", "ledger")


def main() -> int:
    load_env()
    try:
        store = SupabaseStore()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot build SupabaseStore: {exc}")
        return 2
    missing = 0
    for table in TABLES:
        try:
            resp = store.client.table(table).select("*", count=CountMethod.exact, head=True).execute()
            print(f"ok       {table:<11} rows={resp.count}")
        except Exception as exc:  # noqa: BLE001
            missing += 1
            detail = str(exc).replace("\n", " ")[:160]
            print(f"missing  {table:<11} {detail}")
    if missing:
        print(
            f"{missing} table(s) missing: apply supabase/migrations/0001_init.sql (scripts/apply_migration.py)"
        )
        return 1
    print("all tables present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
