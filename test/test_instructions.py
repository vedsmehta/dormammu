"""Instruction builder tests: opener first, one AI line second, recap last, no None/undefined, cohort asks,
prior context, and the negotiation playbook on the negotiate track."""

from __future__ import annotations

import re

import pytest

from router.cohorts import COHORTS
from router.instructions import AI_DISCLOSURE, SUMMARY_CLOSER, build, caller_name_for, opener

VENDORS = {
    "quote": {"key": "bearhug", "name": "BearHug Moving", "track": "quote", "phone": "+15550001111"},
    "schedule": {"key": "chen", "name": "Dr. Chen", "track": "schedule", "phone": "+15550002222"},
    "negotiate": {"key": "comcast", "name": "Comcast", "track": "negotiate", "phone": "+15550003333"},
}
PLANS = {
    "quote": {"vendor_key": "bearhug", "track": "quote", "objective": "Get a quote for a 1BR move to SF.",
              "voice_gender": "female"},
    "schedule": {"vendor_key": "chen", "track": "schedule", "objective": "Book a cleaning next month.",
                 "voice_gender": "male"},
    "negotiate": {"vendor_key": "comcast", "track": "negotiate", "objective": "Lower the internet bill.",
                  "voice_gender": "female"},
}
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


@pytest.mark.parametrize("track", ["quote", "schedule", "negotiate"])
def test_every_track_has_frame_and_cohort(track):
    text = build(PLANS[track], VENDORS[track], PROFILE)
    check_frame(text)
    assert opener("Alex", VENDORS[track]["name"]) in text
    assert f"{AI_DISCLOSURE} Vedant." in text
    for ask in COHORTS["student"]["asks"]:
        assert ask in text
    assert COHORTS["student"]["label"] in text
    assert PLANS[track]["objective"] in text
    assert VENDORS[track]["phone"] not in text


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


def test_quote_mentions_budget_and_totals():
    text = build(PLANS["quote"], VENDORS["quote"], PROFILE)
    assert "$900" in text
    assert "all-in" in text
    assert "dates" in text


def test_negotiate_uses_the_playbook():
    text = build(PLANS["negotiate"], VENDORS["negotiate"], PROFILE)
    assert "account under the name Vedant" in text
    assert "Is there a student rate on this account?" in text
    assert "Is that the best rate available on the account?" in text
    assert "may not add services" in text
    assert "line by line" in text
    assert "Escalate once at most" in text
    assert "including taxes and fees" in text
    assert "Numbers you may cite" not in text
    with_context = build(PLANS["negotiate"], VENDORS["negotiate"], PROFILE, prior_context="Last time $89 a month.")
    assert "Numbers you may cite, and only these: Last time $89 a month." in with_context


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


def test_cohort_asks_not_duplicated_when_objective_has_them():
    ask_line = "Ask for: " + ", ".join(COHORTS["student"]["asks"]) + "."
    plan = {**PLANS["negotiate"], "objective": "Lower the internet bill. " + ask_line}
    text = build(plan, VENDORS["negotiate"], PROFILE)
    assert text.count(ask_line) == 1


def test_build_is_pure():
    plan, vendor, profile = dict(PLANS["quote"]), dict(VENDORS["quote"]), {**PROFILE, "prefs": dict(PROFILE["prefs"])}
    a = build(plan, vendor, profile)
    b = build(plan, vendor, profile)
    assert a == b
    assert plan == PLANS["quote"] and vendor == VENDORS["quote"]
