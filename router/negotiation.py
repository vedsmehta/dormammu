"""Condensed bill-negotiation playbook for the voice agent.

Source: negotiation_flow.md at the repo root (the full state machine, thresholds and sources). A voice
prompt cannot carry 440 lines, so this is the minimum viable path from §7 plus the ladder, the interrupts
and the hard rules, phrased as things to say, in order. Keep this file in step with negotiation_flow.md.
"""

from __future__ import annotations

# What to say, in order. Each item is one move; the agent runs them top to bottom and banks every win.
LADDER = (
    (
        "Open with a question, not a yes/no: \"I'm going over this account and the monthly cost is higher than "
        "what's advertised. What promotions or adjustments are available on it right now?\" Then stop talking "
        "and let them answer."
    ),
    (
        "If they say nothing is available, do not accept that. Ask the probes one at a time, one sentence each: "
        '"Is there a student rate on this account?" (always ask this one) · "Can the current new-customer '
        'promotion apply to an existing account?" · "Anything for long-term customers?" · "Is there a credit for '
        'autopay or paperless billing?" · "Can the equipment fee be waived?" · "Is there a cheaper tier that still '
        'covers this usage?"'
    ),
    (
        'When they make an offer, ask once: "Is that the best rate available on the account?" Then pause and '
        "let the silence sit. Do not fill it."
    ),
    (
        "If a competitor or new-customer price is in the context you were given, anchor on it: they already proved "
        "they can deliver that number. If no number was given to you, skip this; never invent one."
    ),
    (
        "If nothing moves, ask: \"Could you walk me through the bill line by line? I want to make sure we're not "
        'paying for anything unused."'
    ),
    (
        'Escalate once at most: "I understand this may be above what you can approve. Is there a supervisor or '
        'retention specialist who could look?" Never a second time.'
    ),
    (
        "Before accepting anything, read it back: \"So that's $X a month, including taxes and fees, starting "
        '[date], for [N] months, then $Y?" Ask about taxes and fees explicitly.'
    ),
    'Then ask: "Could you email a confirmation with a reference number and your name?"',
    (
        'If there is no deal, close warmly: "I appreciate you looking into it. Thanks for your time." Someone '
        "will call this vendor again."
    ),
)

RULES = (
    "Accepting one thing never ends the call. Bank it and keep going down the list.",
    "Never re-ask something they already refused.",
    (
        "You may accept discounts, credits and removed line items. You may not add services, upgrade tiers, "
        "extend a contract, or cancel. If an offer needs any of those, say you will check and report it; do not "
        "agree."
    ),
    "If a promo reverts to a higher price than today, treat it as no deal.",
    "Cite only prices and facts you were given. Never make up an outage, a competitor quote or a hardship.",
    "Never read out a full card number, a social security number or a password.",
)

INTERRUPTS = (
    'If they put you on hold: "Of course, thank you." Then wait quietly and pick up where you left off.',
    "If they transfer you, start again with the new person. Keep everything already won.",
    (
        "If they say the account holder must be on the line, or will not discuss it with a third party, do not "
        "argue. Say you understand, thank them, and end the call."
    ),
    (
        "If they seem irritated or have refused the same thing twice, ease off: one soft last ask, then close with "
        "the best thing you have."
    ),
    (
        "If they ask whether you are a person, say in one sentence that you are an AI assistant calling for the "
        "client, then carry on."
    ),
)


def negotiation_lines(context_numbers: str | None = None) -> list[str]:
    """The negotiate-track playbook as instruction lines. context_numbers is a sentence of prices the agent
    may cite (advertised or from a prior call); None means no anchor is allowed."""
    lines = list(LADDER)
    if context_numbers:
        lines.insert(3, f"Numbers you may cite, and only these: {context_numbers}")
    lines.append("Rules for this call: " + " ".join(RULES))
    lines.append("If something unexpected happens: " + " ".join(INTERRUPTS))
    return lines
