"""Preflight: Dial limits, the user's vendor roster (masked), and a warm plan cache for the demo sentence.

    uv run python scripts/preflight.py [--user-id UUID] [--refresh]

Safe to run repeatedly: the plan cache is keyed by request hash, so a second run is a cache hit and makes
no LLM call. Pass --refresh after editing the vendor roster to re-plan and overwrite the cached entry.
Never prints secrets or phone numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings import load_env

load_env()

from router.plan import plan, request_hash

DEMO_SENTENCE = "I'm moving to SF next month, handle it"
REDACT_HINTS = ("key", "token", "secret", "phone", "number", "email", "password")


def mask_phone(phone: object) -> str:
    return "+1***" if phone else "(no phone)"


def redact(value: object) -> object:
    """Drop any key that looks like a credential or a phone number, recursively."""
    if isinstance(value, dict):
        return {
            k: redact(v) for k, v in value.items() if not any(h in str(k).lower() for h in REDACT_HINTS)
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


async def show_dial_limits() -> None:
    print("== Dial account ==")
    try:
        from call.dial_client import get_account
    except ImportError:
        print("  skipped: call.dial_client is not available yet (Lane C)")
        return
    try:
        account = await get_account()
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")
        return
    limits = account.get("limits") if isinstance(account, dict) else None
    print(f"  limits: {redact(limits) if limits is not None else redact(account)}")


def get_store_or_none():
    try:
        from server.store import get_store
    except ImportError:
        print("  skipped: server.store is not available yet (Lane A)")
        return None
    try:
        return get_store()
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")
        return None


def show_vendors(store, user_id: str) -> list[dict]:
    print(f"== Vendors for user {user_id[:8]}... ==")
    try:
        vendors = store.list_vendors(user_id) or []
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {type(exc).__name__}: {exc}")
        return []
    if not vendors:
        print("  none. Seed vendors through onboarding (POST /api/py/vendors) first.")
        return []
    for v in vendors:
        demo = " demo" if v.get("is_demo") else ""
        print(f"  {v.get('key', '?'):<16} {v.get('name', '?')!s:<28} {v.get('track', '?'):<10} "
              f"{mask_phone(v.get('phone'))}{demo}")
    return vendors


async def seed_plan_cache(store, user_id: str, refresh: bool) -> None:
    print(f"== Plan cache for: {DEMO_SENTENCE!r} ==")
    key = request_hash(DEMO_SENTENCE)
    try:
        cached = store.get_plan_cache(key)
    except Exception as exc:  # noqa: BLE001
        cached = None
        print(f"  cache read failed: {type(exc).__name__}: {exc}")
    if cached and not refresh:
        print(f"  cache: hit ({len(cached)} plans); no LLM call. Use --refresh to re-plan.")
    else:
        print("  cache: miss, planning now..." if not cached else "  cache: refresh requested, re-planning...")
        if not os.environ.get("OPENAI_API_KEY"):
            print("  OPENAI_API_KEY is not set; the fallback roster will be cached instead of an LLM plan.")
    plans = await plan(store, DEMO_SENTENCE, user_id, refresh=refresh)
    if not plans:
        print("  no plans (no vendors for this user?)")
        return
    print(f"  {len(plans)} plans:")
    for i, p in enumerate(plans, 1):
        print(f"  {i}. [{p.get('track')}] {p.get('vendor_key')} ({p.get('voice_gender')}): {p.get('objective')}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user-id", default=os.environ.get("DEV_USER_ID"), help="defaults to DEV_USER_ID")
    parser.add_argument("--refresh", action="store_true", help="re-plan and overwrite the cached demo plan")
    args = parser.parse_args()

    await show_dial_limits()

    print("== Store ==")
    store = get_store_or_none()
    if store is None:
        return 0
    print(f"  using {type(store).__name__}")
    if not args.user_id:
        print("  no user id: set DEV_USER_ID in .env or pass --user-id. Skipping vendors and plan cache.")
        return 0

    vendors = show_vendors(store, args.user_id)
    if vendors:
        await seed_plan_cache(store, args.user_id, args.refresh)
    print("preflight done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
