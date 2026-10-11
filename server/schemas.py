"""Pydantic request bodies for server/app.py. Rows go out as plain dicts (docs/CONTRACT.md shapes)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Track = Literal["quote", "schedule", "negotiate"]


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    phone: str | None = None
    prefs: dict[str, Any] | None = None
    cohort: str | None = None


class VendorIn(BaseModel):
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    track: Track
    phone: str = Field(min_length=1)
    is_demo: bool = False


class VendorsIn(BaseModel):
    vendors: list[VendorIn]


class RunCreate(BaseModel):
    request: str = Field(min_length=1)


class LedgerFire(BaseModel):
    as_of: str = Field(min_length=10)


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = None
