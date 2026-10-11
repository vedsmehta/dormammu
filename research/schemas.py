"""Pydantic models for the research engine.

Two families:

1. Model-facing (what the LLM returns through the Responses API with `text_format`). These must stay
   strict-schema friendly for OpenAI structured outputs: every field is required (nullable fields are typed
   `X | None` with no default), no dict fields, no defaults. The model never sees our proposal shape.
2. Proposal-facing (what research() returns and the UI / approval lane consume). Defaults are allowed, and
   extra keys round-trip so a later lane can annotate items without breaking validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Track = Literal["quote", "schedule", "negotiate"]


# ---------------------------------------------------------------------------------------------
# Model-facing (strict). Phones and prices are only ever copied from a source the model opened.


class SourcedPlan(BaseModel):
    name: str
    price_usd: float | None
    unit: str | None
    notes: str | None
    source_url: str | None


class SourcedDiscount(BaseModel):
    label: str
    who_qualifies: str
    how_to_claim: str
    source_url: str | None


class VendorFinding(BaseModel):
    name: str
    phone_as_written: str | None
    phone_source_url: str | None
    website: str | None
    address: str | None
    why: str
    plans: list[SourcedPlan]
    discounts: list[SourcedDiscount]
    item_asks: list[str]
    opening: str | None
    confidence: float
    source_urls: list[str]


class CategoryFindings(BaseModel):
    vendors: list[VendorFinding]
    notes: str | None


class PhoneLookup(BaseModel):
    """Second pass: one named vendor's phone number, copied from a page the model opened."""

    name: str
    phone_as_written: str | None
    phone_source_url: str | None
    website: str | None
    address: str | None
    confidence: float
    source_urls: list[str]


class InfoLookup(BaseModel):
    """Second pass: one named vendor's plans and discounts, each pointing at the page that states it."""

    name: str
    plans: list[SourcedPlan]
    discounts: list[SourcedDiscount]
    source_urls: list[str]


class RegulationFinding(BaseModel):
    key: str
    rule: str
    jurisdiction: str
    applies: bool
    what_we_do: str
    citation_title: str | None
    citation_url: str | None


class RegulationFindings(BaseModel):
    rules: list[RegulationFinding]


# ---------------------------------------------------------------------------------------------
# Proposal-facing. research() returns plain dicts shaped like these; tests validate against them.


class Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class Citation(Loose):
    title: str
    url: str
    verified: Literal["api", "fetched", "roster"] | None = None


class Plan(Loose):
    name: str
    price_usd: float | None = None
    unit: str | None = None
    notes: str | None = None
    citation: int
    price_in_source: bool | None = None


class Discount(Loose):
    label: str
    who_qualifies: str
    how_to_claim: str
    citation: int


class Strategy(Loose):
    variant: str
    label: str | None = None
    reason: str | None = None
    asks: list[str] = Field(default_factory=list)
    opening: str
    walk_away: str
    objective: str


class Vendor(Loose):
    key: str
    name: str
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    is_demo: bool = False
    roster_vendor_id: str | None = None


class Item(Loose):
    id: str
    category: str
    track: Track
    source: Literal["roster", "web", "profile"] = "web"
    vendor: Vendor
    why: str
    plans: list[Plan] = Field(default_factory=list)
    discounts: list[Discount] = Field(default_factory=list)
    strategy: Strategy
    confidence: float = Field(ge=0.0, le=1.0)
    citations: list[Citation] = Field(default_factory=list)
    recommended: bool = False
    phone_verified: bool = False
    phone_source: Literal["roster", "model_and_page", "page", "own_site"] | None = None
    phone_note: str | None = None
    skip_reason: str | None = None


class Category(Loose):
    key: str
    label: str
    track: Track
    why: str
    source: Literal["request", "profile", "roster"] = "request"
    query: str | None = None


class Regulation(Loose):
    rule: str
    jurisdiction: str
    applies: bool
    what_we_do: str
    citation: Citation | None = None
    claimed_citation: Citation | None = None  # named by the model or our baseline, not verified
    key: str | None = None
    note: str | None = None


class SecondPass(Loose):
    """What the second pass did: targeted searches, vendor-site fetches, and how many phones it proved."""

    phone_searches: int = 0
    site_fetches: int = 0
    info_searches: int = 0
    phones_found: int = 0
    seconds: float = 0.0


class Proposal(Loose):
    request: str
    details: dict
    categories: list[Category]
    items: list[Item]
    regulations: list[Regulation]
    summary: str
    generated_at: str
    model: str
    web_search: bool
    search_count: int
    errors: list[str] = Field(default_factory=list)
    seconds: float | None = None
    second_pass: SecondPass = Field(default_factory=SecondPass)
