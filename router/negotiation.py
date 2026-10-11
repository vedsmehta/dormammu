"""Condensed bill-negotiation playbook for the voice agent, with the §5 strategy variants.

Source: negotiation_flow.md at the repo root (the full state machine, thresholds and sources). A voice
prompt cannot carry 440 lines, so this is the minimum viable path from §7 plus the ladder, the interrupts
and the hard rules, phrased as things to say, in order. Keep this file in step with negotiation_flow.md.

Strategies (§5) change the opening, not the ladder. choose_strategy() picks one deterministically from the
objective, the prior context and the vendor row, and negotiation_lines() swaps that strategy's opening in
for the default open-ended frame. The §0 thresholds ride along as one spoken sentence so the agent knows
when an offer is good enough to close.
"""

from __future__ import annotations

import re
from typing import Any

# What to say, in order. Each item is one move; the agent runs them top to bottom and banks every win.
# LADDER[0] is the default C1 opening; a strategy replaces it (see STRATEGIES).
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

# §0 decision thresholds, spoken. The agent cannot see the vendor's floor, so it judges discount depth.
THRESHOLDS = (
    "How to judge an offer: if it's 25 percent or more below what the account pays now, take it and close "
    "before they reconsider. If it's 15 to 25 percent off, make one counter, then close. Under 15 percent, "
    "keep going down the list. Add up everything they've agreed to, not just the last thing they said. And "
    "always ask about the student rate before closing, no matter how good the offer is."
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

# ---------------------------------------------------------------------------------------------
# §5 strategy variants. Each has a label, an IVR target, and the lines that replace LADDER[0].
# Every line is something the agent can say out loud.

DEFAULT_STRATEGY = "S2_loyalty"

STRATEGIES: dict[str, dict[str, Any]] = {
    "S1_anchor": {
        "label": "advertised anchor",
        "ivr_target": "retention",
        "opening": (
            (
                "Open on the price you were given, not on a yes/no question: \"I'm seeing [the advertised or "
                "prior price from your context] for this service. If that's available to a new customer, is there "
                'a way to get closer to it on this account?" Then stop and let them answer.'
            ),
            (
                'If they say "that\'s for new customers only": "That\'s the part that\'s hard to square. Is there an '
                'equivalent for existing accounts?" If they say "then go switch", do not push; move on to the bill '
                "walk-through."
            ),
        ),
    },
    "S2_loyalty": {
        "label": "loyalty and tenure",
        "ivr_target": "billing or retention",
        "opening": (
            (
                "Open on loyalty, not on a yes/no question: \"We've been with you a while and always paid on time. "
                "I'd like to stay. What's available for long-term customers right now?\" Then stop talking and let "
                "them answer."
            ),
        ),
    },
    "S3_cancel": {
        "label": "credible cancellation",
        "ivr_target": "cancellations",
        "opening": (
            (
                "Open on the decision, calmly: \"We're deciding whether to keep this service. Before that call, I "
                'want to know the best rate available on the account." Then stop and let them answer.'
            ),
            (
                'If they start a cancellation workflow, do not confirm it yet: "Before we do that, is there any '
                'retention offer first?" Only confirm a cancellation if the client authorized it in writing in your '
                "objective."
            ),
        ),
    },
    "S4_audit": {
        "label": "audit and unbundle",
        "ivr_target": "billing",
        "opening": (
            (
                "Open with the audit, which needs no approval from them: \"Could you walk me through the bill line "
                "by line? I want to make sure we're not paying for anything unused.\" Ask to drop anything the client "
                "doesn't use."
            ),
            (
                'Once the bill is itemized, ask the open question: "Now that we see it, what promotions or '
                'adjustments are available on it right now?" Then stop talking.'
            ),
        ),
    },
    "new_service": {
        "label": "new service at a new address",
        "ivr_target": "sales or new service",
        "opening": (
            (
                "Give the move-in date from your objective, then ask: \"What's the current new-customer promotion "
                'on that address?" Stop and let them answer.'
            ),
            (
                'Then, one at a time: "Is there a student rate on top of that?" · "Can the installation fee be '
                'waived?" · "Is that available with no contract?" · "Is the first month free on that promotion?"'
            ),
            (
                "Confirm the install date and arrival window out loud before agreeing. If the earliest install is "
                "after the move-in date, ask for the soonest earlier slot and say the client will confirm."
            ),
        ),
    },
}

_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_NEW_SERVICE = re.compile(
    r"\b(set\s*up|setting\s*up|new (?:service|address|account|customer)|install(?:ation)?|move[- ]in|"
    r"moving in|start(?:ing)? service|activate)\b"
)
_CANCEL_AUTH = re.compile(
    r"\b(cancell?ation (?:is )?authori[sz]ed|authori[sz]ed to cancel|ok(?:ay)? to cancel|may cancel|"
    r"can cancel if|cancel if (?:they|it))\b"
)
_LOWER_BILL = re.compile(r"\b(lower|reduce|cut|retention|existing|current bill|renew|promo (?:is )?ending)\b")
_REFUSED = re.compile(r"\b(refused|would not|wouldn't|declined|no deal|rigid)\b")
_TENURE = re.compile(r"\b(years?|since 20\d\d|long[- ]time|loyal)\b")


def choose_strategy(objective: str | None, prior_context: str | None, vendor: dict | None) -> tuple[str, str]:
    """A strategy key from STRATEGIES and a one-line reason. Deterministic; only reads its inputs.

    Order: setting up a new service beats everything (there is no existing bill to anchor on); then the
    cancellation opener, only when the objective authorizes cancellation; then the advertised anchor when
    a dollar figure is in the context; then audit for a vendor that refused last time or is flagged rigid;
    else loyalty, the lowest hang-up risk.
    """
    objective = (objective or "").strip()
    context = (prior_context or "").strip()
    vendor = vendor or {}
    obj_l = objective.lower()
    ctx_l = context.lower()

    if _NEW_SERVICE.search(obj_l) and not _LOWER_BILL.search(obj_l):
        return "new_service", "the objective is to set up service at a new address, so there is no bill to anchor on"
    if _CANCEL_AUTH.search(obj_l):
        return "S3_cancel", "the objective authorizes cancellation, so the cancellation opener is credible"
    if _MONEY.search(context) or _MONEY.search(objective):
        where = "prior context" if _MONEY.search(context) else "objective"
        return "S1_anchor", f"a dollar figure is in the {where}, so the agent can anchor on a price it was given"
    if vendor.get("rigid") is True or _REFUSED.search(ctx_l):
        return "S4_audit", "this vendor refused last time, so the audit opener needs no approval from the rep"
    if _TENURE.search(ctx_l) or _TENURE.search(obj_l):
        return "S2_loyalty", "tenure is on file, so the loyalty opener has the lowest hang-up risk"
    return "S2_loyalty", "no price, refusal or cancellation authority on file, so the loyalty opener is the safe default"


def strategy_label(key: str | None) -> str:
    return STRATEGIES.get(key or "", STRATEGIES[DEFAULT_STRATEGY])["label"]


def negotiation_lines(context_numbers: str | None = None, strategy: str | None = None) -> list[str]:
    """The negotiate-track playbook as instruction lines.

    context_numbers is a sentence of prices the agent may cite (advertised or from a prior call); None means
    no anchor is allowed. strategy is a key from STRATEGIES; None or unknown means the default (S2 loyalty).
    The ladder after the opening is the same for every strategy, as negotiation_flow.md §5 requires.
    """
    key = strategy if strategy in STRATEGIES else DEFAULT_STRATEGY
    spec = STRATEGIES[key]
    lines = [f"Approach for this call: {spec['label']}. Ask the menu or the first person for {spec['ivr_target']}."]
    lines.extend(spec["opening"])
    lines.extend(LADDER[1:])
    if context_numbers:
        # Right before the anchor step (LADDER[3]); five ladder steps follow it.
        lines.insert(len(lines) - 6, f"Numbers you may cite, and only these: {context_numbers}")
    lines.append(THRESHOLDS)
    lines.append("Rules for this call: " + " ".join(RULES))
    lines.append("If something unexpected happens: " + " ".join(INTERRUPTS))
    return lines
