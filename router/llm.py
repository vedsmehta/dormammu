"""Shared OpenAI helper: one structured call, pydantic model in, pydantic instance out, None on failure.

Verified against openai 3.29.0 (Oct 11 2026). The supported structured-output path is the Responses API:

    async with AsyncOpenAI(api_key=..., timeout=..., max_retries=1) as client:
        response = await client.responses.parse(
            model="gpt-4.1", instructions=<system>, input=<user>, text_format=<pydantic model class>
        )
    response.output_parsed  # instance of the model, or None on refusal / no parsed text

`text_format` converts the pydantic class into a strict json_schema text format. A refusal yields no
parsed content (`output_parsed is None`) rather than an exception. Parse failures raise inside the SDK.
Every path that is not a parsed instance of `schema` returns None here, so callers never need try/except.
"""

from __future__ import annotations

import asyncio
import os
from typing import TypeVar

from pydantic import BaseModel

DEFAULT_MODEL = "gpt-4.1"

T = TypeVar("T", bound=BaseModel)


def model_name() -> str:
    return os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


async def structured(system: str, user: str, schema: type[T], timeout: float = 20.0) -> T | None:
    """Ask the model for one `schema` instance. Returns None on missing key, timeout, refusal, or parse failure."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        return await asyncio.wait_for(_call(api_key, system, user, schema, timeout), timeout=timeout)
    except Exception:  # noqa: BLE001
        return None


async def _call(api_key: str, system: str, user: str, schema: type[T], timeout: float) -> T | None:
    from openai import AsyncOpenAI

    async with AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=1) as client:
        response = await client.responses.parse(
            model=model_name(),
            instructions=system,
            input=user,
            text_format=schema,
        )
    parsed = response.output_parsed
    if not isinstance(parsed, schema):
        return None
    return parsed
