"""Apply supabase/migrations/0001_init.sql to Postgres.

    DATABASE_URL=postgresql://postgres.<ref>:<password>@<host>:5432/postgres uv run python scripts/apply_migration.py

Uses psycopg when it is installed. Otherwise prints the psql command to run by hand. The migration is
idempotent (create ... if not exists, drop policy if exists), so re-running it is safe.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from settings import ROOT, load_env

MIGRATION = ROOT / "supabase" / "migrations" / "0001_init.sql"


def main() -> int:
    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set. Find it in the Supabase dashboard under Project Settings > Database.")
        print("Then either set it and re-run this script, or apply the file by hand:")
        print(f'  docker run --rm -i postgres:16 psql "$DATABASE_URL" < {MIGRATION.relative_to(ROOT)}')
        print("or paste the file into the Supabase SQL editor.")
        return 2
    sql = Path(MIGRATION).read_text()
    try:
        import psycopg
    except ImportError:
        print("psycopg is not installed (uv add psycopg[binary]). Apply the migration with psql instead:")
        print(f'  docker run --rm -i postgres:16 psql "$DATABASE_URL" < {MIGRATION.relative_to(ROOT)}')
        return 2
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(sql)
    print(f"applied {MIGRATION.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
