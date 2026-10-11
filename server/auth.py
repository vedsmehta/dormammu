"""Who is calling. docs/CONTRACT.md "Auth".

Order of checks:
  1. X-Internal-Secret == INTERNAL_API_SECRET plus X-Dormammu-User: server-to-server.
  2. Authorization: Bearer <supabase access token>: verified against GoTrue /auth/v1/user with the
     publishable key, cached by token for 60 seconds.
  3. No bearer and DEV_USER_ID set: local dev only.
Anything else is a 401.
"""

from __future__ import annotations

import hmac
import os
import time
from typing import Any

import httpx
from fastapi import HTTPException, Request

from settings import load_env

load_env()

CACHE_TTL_SECONDS = 60.0
_cache: dict[str, tuple[str, float]] = {}


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _cached(token: str) -> str | None:
    hit = _cache.get(token)
    if hit and hit[1] > time.monotonic():
        return hit[0]
    _cache.pop(token, None)
    return None


def _remember(token: str, user_id: str) -> None:
    if len(_cache) > 1024:
        now = time.monotonic()
        for k in [k for k, (_, exp) in _cache.items() if exp <= now]:
            _cache.pop(k, None)
    _cache[token] = (user_id, time.monotonic() + CACHE_TTL_SECONDS)


async def verify_supabase_token(token: str) -> str:
    """Return the user id for a Supabase access token, or raise HTTPException(401)."""
    cached = _cached(token)
    if cached:
        return cached
    url = os.environ.get("SUPABASE_URL")
    apikey = os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
    if not url or not apikey:
        raise HTTPException(status_code=401, detail="auth is not configured on the server")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{url.rstrip('/')}/auth/v1/user",
                headers={"apikey": apikey, "Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=401, detail=f"auth lookup failed: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    body: dict[str, Any] = resp.json()
    user_id = body.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="token has no user")
    _remember(token, user_id)
    return user_id


async def current_user_id(request: Request) -> str:
    internal = request.headers.get("x-internal-secret")
    if internal:
        expected = os.environ.get("INTERNAL_API_SECRET") or ""
        if expected and hmac.compare_digest(internal, expected):
            user = request.headers.get("x-dormammu-user")
            if user:
                return user
            raise HTTPException(status_code=401, detail="X-Dormammu-User is required with X-Internal-Secret")
        raise HTTPException(status_code=401, detail="bad internal secret")

    token = bearer_token(request)
    if token:
        return await verify_supabase_token(token)

    dev_user = os.environ.get("DEV_USER_ID")
    if dev_user:
        return dev_user
    raise HTTPException(status_code=401, detail="missing bearer token")


def ensure_profile(store: Any, user_id: str) -> dict:
    """Return the profile row, creating a default one if absent (SqliteStore has no auth trigger)."""
    profile = store.get_profile(user_id)
    if profile is not None:
        return profile
    return store.upsert_profile(user_id, {"display_name": None, "prefs": {}, "cohort": "student"})
