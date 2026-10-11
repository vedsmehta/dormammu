"""Pydantic request bodies for server/app.py. Rows go out as plain dicts (docs/CONTRACT.md shapes)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Track = Literal["quote", "schedule", "negotiate"]


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupIn(BaseModel):
    """POST /auth/signup. Password accounts are created server-side, so no email is ever sent."""

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_RE.match(value):
            raise ValueError("email must look like name@domain.tld")
        return value

    @field_validator("display_name")
    @classmethod
    def _blank_name_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


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
    # Omitted (None) means "leave as is": the store default is manual, and approval writes research.
    source: Literal["manual", "research"] | None = None
    website: str | None = Field(default=None, max_length=500)


class VendorsIn(BaseModel):
    vendors: list[VendorIn]


class RunCreate(BaseModel):
    request: str = Field(min_length=1)


class ResearchIn(BaseModel):
    """POST /research: the same one-sentence request POST /runs takes, researched before anything is dialed."""

    request: str = Field(min_length=1, max_length=2000)


class ProposalApprove(BaseModel):
    """POST /proposals/{id}/approve: the proposal item ids the user wants executed."""

    item_ids: list[str] = Field(min_length=1, max_length=50)


class LedgerFire(BaseModel):
    as_of: str = Field(min_length=10)


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = None
