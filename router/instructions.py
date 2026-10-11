"""STUB. Lane D replaces this file. Signature is the contract (docs/CONTRACT.md)."""

from __future__ import annotations

AI_DISCLOSURE = "Hi, I'm an AI assistant calling on behalf of a client."
SUMMARY_CLOSER = (
    'Before you hang up, say exactly: "Summary: " followed by the key facts in one sentence, '
    "the price, the date, or the outcome we agreed. Then say goodbye."
)


def build(plan: dict, vendor: dict, profile: dict | None, prior_context: str | None = None) -> str:
    parts = [AI_DISCLOSURE, f"Objective: {plan.get('objective', '')}"]
    if prior_context:
        parts.append(f"Context from last time: {prior_context}")
    parts.append(SUMMARY_CLOSER)
    return "\n".join(parts)
