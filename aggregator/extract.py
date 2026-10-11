"""STUB. Lane D replaces this file. Signature is the contract (docs/CONTRACT.md)."""

from __future__ import annotations


def empty_outcome(track: str, note: str) -> dict:
    if track == "quote":
        return {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": note}
    if track == "schedule":
        return {"track": "schedule", "slot_iso": None, "confirmed": False, "note": note}
    return {"track": "negotiate", "action": "none", "saved_usd": None, "effective_date": None, "note": note}


async def extract(track: str, transcript: str | list[dict]) -> dict:
    return empty_outcome(track, "stub extractor")
