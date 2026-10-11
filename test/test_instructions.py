"""Instruction builder tests: opener first, one AI line second, recap last, no None/undefined, per-track cohort
asks, prior context, the negotiation playbook with its strategy variants and the §0 thresholds on the negotiate
track."""

from __future__ import annotations

import re

import pytest

from router.cohorts import COHORTS, ask_line, cohort_asks
from router.instructions import AI_DISCLOSURE, SUMMARY_CLOSER, build, caller_name_for, opener, strategy_for
from router.negotiation import STRATEGIES, THRESHOLDS, choose_strategy, negotiation_lines

VENDORS = {
    "quote": {"key": "bearhug", "name": "BearHug Moving", "track": "quote", "phone": "+15550001111"},
    "schedule": {"key": "chen", "name": "Dr. Chen", "track": "schedule", "phone": "+15550002222"},
    "negotiate": {"key": "comcast", "name": "Comcast", "track": "negotiate", "phone": "+15550003333"},
}
PLANS = {
    "quote": {"vendor_key": "bearhug", "track": "quote",
              "objective": "Get an all-in quote to move a 1-bedroom from Stanford to Palo Alto on October 25.",
              "voice_gender": "female"},
    "schedule": {"vendor_key": "chen", "track": "schedule",
                 "objective": "Book a new-patient dental cleaning around the move on October 25.",
                 "voice_gender": "male"},
    "negotiate": {"vendor_key": "comcast", "track": "negotiate", "objective": "Lower the internet bill.",
                  "voice_gender": "female"},
}  # fmt: skip
NEW_SERVICE = (
    "Set up internet with Comcast at the client's new address in Palo Alto for move-in on October 25: ask for the "
    "current new-customer promotion, the student rate, no contract, the installation fee waived, and confirm the "
    "install date."
)
PROFILE = {
    "id": "u1",
    "display_name": "Vedant",
    "phone": "+15550009999",
    "cohort": "student",
    "prefs": {
        "budget_ceiling_usd": 900,
        "preferred_times": ["weekday afternoons"],
        "availability": [{"start": "2026-10-15T14:00:00-07:00", "end": "2026-10-15T17:00:00-07:00"}],
    },
}
BAD_TOKENS = re.compile(r"\bNone\b|\bundefined\b|\bnull\b")


def check_frame(text: str) -> None:
    assert text.strip()
    first, second = text.splitlines()[0], text.splitlines()[1]
    assert first.startswith('Your first words on this call, exactly: "Hi, this is ')
    assert "am I speaking with" in first
    assert AI_DISCLOSURE in second
    assert text.rstrip().endswith(SUMMARY_CLOSER)
    assert text.count(SUMMARY_CLOSER) == 1
    assert "Never claim to be human" in text
    assert not BAD_TOKENS.search(text)


def negotiate(objective=None, prior_context=None, vendor=None):
    plan = {**PLANS["negotiate"], "objective": objective or PLANS["negotiate"]["objective"]}
    return build(plan, vendor or VENDORS["negotiate"], PROFILE, prior_context=prior_context)


@pytest.mark.parametrize("track", ["quote", "schedule", "negotiate"])
def test_every_track_has_frame_and_cohort(track):
    text = build(PLANS[track], VENDORS[track], PROFILE)
    check_frame(text)
    assert opener("Alex", VENDORS[track]["name"]) in text
    assert f"{AI_DISCLOSURE} Vedant." in text
    for ask in cohort_asks(COHORTS["student"], track):
        assert ask in text
    assert text.count("Ask for:") == 1
    assert COHORTS["student"]["label"] in text
    assert "Only claim that" in text
    assert PLANS[track]["objective"] in text
    assert VENDORS[track]["phone"] not in text


def test_cohort_asks_are_track_specific():
    assert "no deposit above 10 percent" in build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "accepts student insurance or self-pay rate" in build(PLANS["schedule"], VENDORS["schedule"], PROFILE)
    assert "first month free if offered" in negotiate()
    assert "no deposit above 10 percent" not in negotiate()
    assert "first month free" not in build(PLANS["quote"], VENDORS["quote"], PROFILE)


@pytest.mark.parametrize("track", ["quote", "schedule", "negotiate"])
def test_profile_none_is_safe(track):
    text = build(PLANS[track], VENDORS[track], None)
    check_frame(text)
    assert f"{AI_DISCLOSURE} a client." in text


def test_opener_is_exactly_the_requested_sentence():
    assert opener("Alex", "BearHug Moving") == "Hi, this is Alex, am I speaking with BearHug Moving?"
    text = build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert text.splitlines()[0] == (
        'Your first words on this call, exactly: "Hi, this is Alex, am I speaking with BearHug Moving?" '
        "Then wait for them to answer."
    )


def test_caller_name_from_prefs_then_env(monkeypatch):
    monkeypatch.delenv("CALLER_NAME", raising=False)
    assert caller_name_for(PROFILE) == "Alex"
    named = {**PROFILE, "prefs": {**PROFILE["prefs"], "caller_name": "Sam"}}
    assert caller_name_for(named) == "Sam"
    assert "Hi, this is Sam, am I speaking with BearHug Moving?" in build(PLANS["quote"], VENDORS["quote"], named)
    monkeypatch.setenv("CALLER_NAME", "Jordan")
    assert caller_name_for(PROFILE) == "Jordan"
    assert caller_name_for(None) == "Jordan"


def test_prior_context_opens_near_top():
    context = "Last time (Oct 3) BearHug Moving quoted $1,240 flat."
    text = build(PLANS["quote"], VENDORS["quote"], PROFILE, prior_context=context)
    check_frame(text)
    lines = text.splitlines()
    assert any(context in line for line in lines[:3])
    assert context not in build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "None" not in build(PLANS["quote"], VENDORS["quote"], PROFILE, prior_context=None)


def test_schedule_lists_availability_inline():
    text = build(PLANS["schedule"], VENDORS["schedule"], PROFILE)
    assert "Oct 15" in text
    assert "2:00pm to 5:00pm" in text
    assert "earliest" in text
    assert "under the name Vedant" in text
    no_windows = {**PROFILE, "prefs": {"preferred_times": ["weekday afternoons"]}}
    assert "weekday afternoons" in build(PLANS["schedule"], VENDORS["schedule"], no_windows)
    flexible = build(PLANS["schedule"], VENDORS["schedule"], {**PROFILE, "prefs": {}})
    check_frame(flexible)
    assert "flexible" in flexible


def test_quote_mentions_budget_totals_stairs_and_packing():
    text = build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "$900" in text
    assert "all-in" in text
    assert "dates" in text
    assert "bedroom count" in text
    assert "stairs or a long carry" in text
    assert "packing is extra" in text
    assert "deposit" in text


def test_negotiate_uses_the_playbook():
    text = negotiate()
    check_frame(text)
    assert "account under the name Vedant" in text
    assert "Is there a student rate on this account?" in text
    assert "Is that the best rate available on the account?" in text
    assert "may not add services" in text
    assert "line by line" in text
    assert "Escalate once at most" in text
    assert "including taxes and fees" in text
    assert "Numbers you may cite" not in text
    with_context = negotiate(prior_context="Last time $89 a month.")
    assert "Numbers you may cite, and only these: Last time $89 a month." in with_context
    # The numbers line sits before the anchor step and after the floor question.
    lines = with_context.splitlines()
    numbers_at = next(i for i, line in enumerate(lines) if "Numbers you may cite" in line)
    assert "best rate available" in lines[numbers_at - 1]
    assert "anchor on it" in lines[numbers_at + 1]


def test_thresholds_spoken_on_negotiate_track_only():
    text = negotiate()
    assert THRESHOLDS in text
    assert "25 percent or more" in text
    assert "15 to 25 percent" in text
    assert "Under 15 percent" in text
    assert "always ask about the student rate before closing" in text
    assert "25 percent" not in build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "25 percent" not in build(PLANS["schedule"], VENDORS["schedule"], PROFILE)


# ---------------------------------------------------------------------------------------------
# Strategy variants (negotiation_flow.md §5), one test each, plus precedence and determinism.


def test_strategy_s2_loyalty_is_the_default():
    key, reason = choose_strategy("Lower the internet bill.", None, VENDORS["negotiate"])
    assert key == "S2_loyalty"
    assert "default" in reason
    text = negotiate()
    assert "Approach for this call: loyalty and tenure." in text
    assert "What's available for long-term customers right now?" in text
    assert "Why: " in text
    assert "I'm going over this account and the monthly cost is higher" not in text  # the C1 default was replaced


def test_strategy_s1_anchor_needs_a_number_in_context():
    key, reason = choose_strategy("Lower the internet bill.", "Last time (Oct 3) Comcast quoted $89 a month.", {})
    assert key == "S1_anchor"
    assert "dollar figure" in reason and "prior context" in reason
    text = negotiate(prior_context="Last time (Oct 3) Comcast quoted $89 a month.")
    assert "Approach for this call: advertised anchor." in text
    assert "If that's available to a new customer, is there a way to get closer to it on this account?" in text
    assert "Numbers you may cite, and only these: Last time (Oct 3) Comcast quoted $89 a month." in text
    # No number anywhere: the anchor is never chosen, so the agent cannot be told to cite one.
    assert choose_strategy("Lower the bill, they charge too much.", "Last time they were friendly.", {})[0] != "S1_anchor"


def test_strategy_s3_cancellation_only_when_authorized():
    authorized = "Lower the internet bill. Cancellation is authorized if they will not move on price."
    key, reason = choose_strategy(authorized, None, {})
    assert key == "S3_cancel"
    assert "authorizes cancellation" in reason
    text = negotiate(objective=authorized)
    assert "Approach for this call: credible cancellation." in text
    assert "We're deciding whether to keep this service." in text
    assert "is there any retention offer first?" in text
    # Mentioning cancellation without authorizing it is not enough.
    assert choose_strategy("Lower the bill; do not cancel anything.", None, {})[0] == "S2_loyalty"
    assert choose_strategy("They might cancel us.", None, {})[0] == "S2_loyalty"


def test_strategy_s4_audit_for_rigid_vendors():
    refused = "Last time (Oct 3) Comcast refused to lower the bill."
    key, reason = choose_strategy("Lower the internet bill.", refused, {})
    assert key == "S4_audit"
    assert "refused last time" in reason
    text = negotiate(prior_context=refused)
    assert "Approach for this call: audit and unbundle." in text
    assert "Open with the audit, which needs no approval from them" in text
    assert "Now that we see it, what promotions or adjustments are available" in text
    assert choose_strategy("Lower the internet bill.", None, {"key": "x", "rigid": True})[0] == "S4_audit"


def test_strategy_new_service_for_internet_at_a_new_address():
    key, reason = choose_strategy(NEW_SERVICE, None, {})
    assert key == "new_service"
    assert "new address" in reason
    text = negotiate(objective=NEW_SERVICE)
    check_frame(text)
    assert "Approach for this call: new service at a new address." in text
    assert "setting up new internet service at the client's new address, under the name Vedant" in text
    assert "What's the current new-customer promotion on that address?" in text
    assert "Is there a student rate on top of that?" in text
    assert "Can the installation fee be waived?" in text
    assert "Is that available with no contract?" in text
    assert "Confirm the install date and arrival window" in text
    assert "going over the bill" not in text
    # The ladder still follows: floor question, read-back, email confirmation.
    assert "Is that the best rate available on the account?" in text
    assert "including taxes and fees" in text
    assert "reference number" in text


def test_strategy_precedence_and_determinism():
    # New service wins even with a number in context (there is no existing bill to anchor on).
    assert choose_strategy(NEW_SERVICE, "Last time (Oct 3) Comcast quoted $89 a month.", {})[0] == "new_service"
    # Authorized cancellation beats the anchor.
    authorized = "Lower the bill. Cancellation is authorized."
    assert choose_strategy(authorized, "Last time $89 a month.", {})[0] == "S3_cancel"
    # The anchor beats the audit when both apply.
    assert choose_strategy("Lower the bill.", "Last time Comcast refused; new customers pay $59.", {})[0] == "S1_anchor"
    # A move that is explicitly about the existing bill is not new service.
    assert choose_strategy("Lower the existing bill before the move-in.", None, {})[0] == "S2_loyalty"
    for args in [(NEW_SERVICE, None, {}), ("Lower it.", "Last time $89.", {}), (authorized, None, {}),
                 ("Lower it.", "Last time they refused.", {}), ("Lower it.", None, {}), (None, None, None)]:  # fmt: skip
        assert choose_strategy(*args) == choose_strategy(*args)
    assert choose_strategy(None, None, None)[0] == "S2_loyalty"


def test_every_strategy_keeps_the_ladder_and_rules():
    for key, spec in STRATEGIES.items():
        lines = negotiation_lines(None, key)
        assert lines[0].startswith(f"Approach for this call: {spec['label']}.")
        assert spec["ivr_target"] in lines[0]
        for line in spec["opening"]:
            assert line in lines
        joined = "\n".join(lines)
        assert "Is there a student rate on this account?" in joined
        assert "Is that the best rate available on the account?" in joined
        assert "Escalate once at most" in joined
        assert THRESHOLDS in joined
        assert "Rules for this call:" in joined
        assert "If something unexpected happens:" in joined
        assert "Numbers you may cite" not in joined
    assert negotiation_lines(None, "not-a-strategy")[0].startswith("Approach for this call: loyalty and tenure.")
    assert negotiation_lines(None)[0] == negotiation_lines(None, "S2_loyalty")[0]


def test_strategy_for_exposes_the_same_choice():
    info = strategy_for(PLANS["negotiate"], VENDORS["negotiate"], "Last time (Oct 3) Comcast quoted $89 a month.")
    assert info == {"key": "S1_anchor", "label": "advertised anchor", "reason": info["reason"]}
    assert "dollar figure" in info["reason"]
    assert strategy_for({"objective": NEW_SERVICE}, {}, None)["key"] == "new_service"
    assert strategy_for({}, {}, None)["key"] == "S2_loyalty"


def test_voice_rules_present():
    text = build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "One question at a time" in text
    assert "Don't over-explain" in text
    assert "I'd have to check on that" in text
    assert "Never narrate" in text


def test_missing_fields_never_leak():
    text = build({"vendor_key": "x"}, {"key": "x"}, {"display_name": None, "phone": None, "prefs": None})
    check_frame(text)
    assert "What you're calling about:" in text
    text = build({}, {}, {"display_name": "None", "cohort": "unknown-cohort"})
    check_frame(text)
    assert "am I speaking with there?" in text
    text = build({"track": "negotiate", "objective": None}, {"key": "comcast"}, None, prior_context=None)
    check_frame(text)
    assert "Approach for this call: loyalty and tenure." in text


def test_cohort_asks_not_duplicated_when_objective_has_them():
    line = ask_line(COHORTS["student"], "negotiate")
    plan = {**PLANS["negotiate"], "objective": "Lower the internet bill. " + line}
    text = build(plan, VENDORS["negotiate"], PROFILE)
    assert text.count(line) == 1
    assert text.count("Ask for:") == 1
    # An older cached objective carrying the generic asks still yields exactly one "Ask for:".
    generic = {**PLANS["negotiate"], "objective": "Lower the internet bill. Ask for: student rate, waive activation fee."}
    assert build(generic, VENDORS["negotiate"], PROFILE).count("Ask for:") == 1


def test_build_is_pure():
    plan, vendor, profile = dict(PLANS["quote"]), dict(VENDORS["quote"]), {**PROFILE, "prefs": dict(PROFILE["prefs"])}
    a = build(plan, vendor, profile)
    b = build(plan, vendor, profile)
    assert a == b
    assert plan == PLANS["quote"] and vendor == VENDORS["quote"]
