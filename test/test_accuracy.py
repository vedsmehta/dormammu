"""Lane E2: accuracy scoring (pure), the eval script's offline plumbing, and the 24 labeled transcripts.

No network. The one test that drives scripts/eval_extract.py end to end replaces router.llm.structured with a
canned answer per track and checks that each fixture's frozen call_time reached the prompt.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from aggregator import accuracy as acc
from aggregator import extract as ex
from router import llm

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "test" / "fixtures" / "transcripts"
FIXTURE_FILES = sorted(FIXTURE_DIR.glob("*.json"))
SHAPES = {
    "quote": {"track", "amount_usd", "unit", "caveats", "note"},
    "schedule": {"track", "slot_iso", "confirmed", "note"},
    "negotiate": {"track", "action", "saved_usd", "effective_date", "note"},
}
OPENER = "Hi, this is Alex, am I speaking with "
AI_LINE = "AI assistant calling for Vedant"

QUOTE = {"track": "quote", "amount_usd": 1450.0, "unit": "flat", "caveats": ["deposit"], "note": None}
SCHEDULE = {"track": "schedule", "slot_iso": "2026-10-15T14:30:00-07:00", "confirmed": True, "note": None}
NEGOTIATE = {"track": "negotiate", "action": "reduced", "saved_usd": 25.0, "effective_date": "2026-11-01",
             "note": None}


def load_eval_module():
    spec = importlib.util.spec_from_file_location("eval_extract_under_test", ROOT / "scripts" / "eval_extract.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fixtures() -> list[dict]:
    return [json.loads(path.read_text()) for path in FIXTURE_FILES]


# ------------------------------------------------------------------ score() tolerances


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        (1450, 1450.0, True),
        (1450, 1450.9, True),
        (1450, 1449.0, True),
        (1450, 1452, False),
        (None, None, True),
        (None, 1450, False),
        (1450, None, False),
        (1450, "1450", True),
        (1450, "n/a", False),
        (1450, True, False),
    ],
)
def test_amount_within_a_dollar(expected, actual, ok):
    assert acc.amount_match(expected, actual) is ok


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ("flat", "Flat rate", True),
        ("flat", "total", True),
        ("flat", "per job", True),
        ("per hour", "hourly", True),
        ("per hour", "an hour", True),
        ("per hour", "/hr", True),
        ("per month", "monthly", True),
        ("flat", "per hour", False),
        (None, None, True),
        (None, "flat", False),
        ("flat", None, False),
    ],
)
def test_unit_by_meaning(expected, actual, ok):
    assert acc.unit_match(expected, actual) is ok


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ("2026-10-15T14:30:00-07:00", "2026-10-15T14:30:00-07:00", True),
        ("2026-10-15T14:30:00-07:00", "2026-10-15T14:00:00-07:00", True),  # minutes ignored
        ("2026-10-15T14:30:00-07:00", "2026-10-15T15:30:00-07:00", False),  # hour matters
        ("2026-10-15T14:30:00-07:00", "2026-10-16T14:30:00-07:00", False),  # day matters
        ("2026-10-15T14:30:00-07:00", "2026-10-15T21:30:00+00:00", True),  # same instant in UTC
        ("2026-10-15T14:30:00-07:00", "2026-10-15T21:30:00Z", True),
        ("2026-11-03T13:00:00-08:00", "2026-11-03T13:00:00-07:00", True),  # same wall clock, DST slip
        ("2026-10-15T14:30:00-07:00", "2026-10-15T14:30:00", True),  # naive, same wall clock
        (None, None, True),
        (None, "2026-10-15T14:30:00-07:00", False),
        ("2026-10-15T14:30:00-07:00", None, False),
        ("2026-10-15T14:30:00-07:00", "Thursday-ish", False),
    ],
)
def test_slot_same_day_and_hour(expected, actual, ok):
    assert acc.slot_match(expected, actual) is ok


@pytest.mark.parametrize(
    ("expected", "actual", "ok"),
    [
        ("2026-11-01", "2026-11-01", True),
        ("2026-11-01", "2026-11-01T00:00:00-07:00", True),
        ("2026-11-01", "2026-11-02", False),
        (None, None, True),
        (None, "2026-11-01", False),
        ("2026-11-01", "soon", False),
    ],
)
def test_effective_date_same_date(expected, actual, ok):
    assert acc.date_match(expected, actual) is ok


def test_score_quote_ignores_caveats_and_note():
    actual = {**QUOTE, "amount_usd": 1450.5, "unit": "flat rate", "caveats": [], "note": "whatever"}
    scored = acc.score(QUOTE, actual, "quote")
    assert scored == {"track": "quote", "fields": {"amount_usd": True, "unit": True}, "exact": True, "partial": 1.0}


def test_score_partial_and_exact():
    scored = acc.score(SCHEDULE, {**SCHEDULE, "confirmed": False}, "schedule")
    assert scored["fields"] == {"slot_iso": True, "confirmed": False}
    assert scored["exact"] is False
    assert scored["partial"] == 0.5

    scored = acc.score(NEGOTIATE, {**NEGOTIATE, "saved_usd": 24.2, "effective_date": "2026-11-01T09:00:00"},
                       "negotiate")
    assert scored["exact"] is True
    scored = acc.score(NEGOTIATE, {**NEGOTIATE, "action": "none"}, "negotiate")
    assert scored["fields"]["action"] is False
    assert scored["partial"] == round(2 / 3, 4)


def test_score_survives_missing_or_malformed_actual():
    assert acc.score(QUOTE, None, "quote")["fields"] == {"amount_usd": False, "unit": False}
    assert acc.score(QUOTE, "garbage", "quote")["exact"] is False
    empty = ex.empty_outcome("negotiate", "empty transcript")
    assert acc.score(empty, {}, "negotiate")["exact"] is True  # null label, null actual
    assert acc.score(QUOTE, QUOTE, "unknown-track")["track"] == "negotiate"


# ------------------------------------------------------------------ summarize()


def test_summarize_known_set():
    scores = [
        acc.score(QUOTE, QUOTE, "quote"),
        acc.score(QUOTE, {**QUOTE, "amount_usd": 999.0}, "quote"),
        acc.score(SCHEDULE, SCHEDULE, "schedule"),
        acc.score(NEGOTIATE, {**NEGOTIATE, "effective_date": None}, "negotiate"),
    ]
    summary = acc.summarize(scores)
    assert summary["n"] == 4
    assert summary["exact_rate"] == 0.5
    assert summary["field_rates"] == {
        "amount_usd": 0.5, "unit": 1.0, "slot_iso": 1.0, "confirmed": 1.0,
        "action": 1.0, "saved_usd": 1.0, "effective_date": 0.0,
    }
    assert summary["by_track"]["quote"] == {"n": 2, "exact_rate": 0.5, "field_rates": {"amount_usd": 0.5, "unit": 1.0}}
    assert summary["by_track"]["schedule"]["exact_rate"] == 1.0
    assert summary["by_track"]["negotiate"]["exact_rate"] == 0.0
    assert set(summary["by_track"]) == {"quote", "schedule", "negotiate"}


def test_summarize_empty():
    assert acc.summarize([]) == {"n": 0, "exact_rate": 0.0, "field_rates": {}, "by_track": {}}


# ------------------------------------------------------------------ recap_agreement()

AGENT_RECAP_TURNS = [
    {"speaker": "agent", "text": "Hi, this is Alex, am I speaking with Bay Breeze Movers?"},
    {"speaker": "user", "text": "Yes."},
    {"speaker": "agent", "text": "Just to confirm, that's $1,450 flat for Thursday October 15th. Thanks, bye."},
]


def test_recap_agreement_quote():
    assert acc.recap_agreement(AGENT_RECAP_TURNS, QUOTE) is True
    assert acc.recap_agreement(AGENT_RECAP_TURNS, {**QUOTE, "amount_usd": 1450.8}) is True
    assert acc.recap_agreement(AGENT_RECAP_TURNS, {**QUOTE, "amount_usd": 1640.0}) is False
    assert acc.recap_agreement(AGENT_RECAP_TURNS, {**QUOTE, "amount_usd": None}) is False


def test_recap_agreement_schedule_by_day():
    assert acc.recap_agreement(AGENT_RECAP_TURNS, SCHEDULE) is True  # "Thursday"
    turns = [{"speaker": "agent", "text": "Summary: booked for October 15th at 2:30."}]
    assert acc.recap_agreement(turns, SCHEDULE) is True  # month and day
    assert acc.recap_agreement(turns, {**SCHEDULE, "slot_iso": "2026-10-16T14:30:00-07:00"}) is False
    assert acc.recap_agreement(turns, {**SCHEDULE, "slot_iso": None}) is False


def test_recap_agreement_negotiate_and_shapes():
    turns = [{"speaker": "agent", "text": "Just to confirm, $25 a month off starting November 1st. Bye."}]
    assert acc.recap_agreement(turns, NEGOTIATE) is True
    assert acc.recap_agreement(turns, {**NEGOTIATE, "saved_usd": 10.0}) is False
    assert acc.recap_agreement(turns, {**NEGOTIATE, "saved_usd": None}) is False
    # string transcript in the "Speaker: text" form the extractor renders
    text = "Agent: hi there\nUser: yes\nAgent: Just to confirm, $25 a month off. Bye."
    assert acc.recap_agreement(text, NEGOTIATE) is True
    # a user line with the marker is not a recap; only agent lines count
    assert acc.recap_agreement([{"speaker": "user", "text": "Just to confirm, $25 off"}], NEGOTIATE) is False


def test_recap_agreement_without_a_recap_line():
    turns = [t for t in AGENT_RECAP_TURNS if "Just to confirm" not in t["text"]]
    assert acc.recap_line(turns) is None
    assert acc.recap_agreement(turns, QUOTE) is False
    assert acc.recap_agreement([], QUOTE) is False
    assert acc.recap_agreement(None, None) is False


# ------------------------------------------------------------------ the 24 fixtures


def test_fixture_set_counts():
    data = fixtures()
    assert len(data) == 24
    assert len({f["id"] for f in data}) == 24
    by_track = {t: sum(1 for f in data if f["track"] == t) for t in SHAPES}
    assert by_track == {"quote": 8, "schedule": 8, "negotiate": 8}
    assert sum(1 for f in data if not f["has_recap"]) >= 3
    assert sum(1 for f in data if f["recap_disagrees"]) == 2


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=lambda p: p.stem)
def test_fixture_loads_and_matches_outcome_shape(path: Path):
    data = json.loads(path.read_text())
    assert data["id"] == path.stem
    for key in ("id", "track", "scenario", "call_time", "has_recap", "recap_disagrees", "turns", "expected", "notes"):
        assert key in data, key
    track = data["track"]
    assert track in SHAPES
    call_time = datetime.fromisoformat(data["call_time"])
    assert call_time.tzinfo is not None

    expected = data["expected"]
    assert set(expected) == SHAPES[track]
    assert expected["track"] == track
    if track == "quote":
        assert expected["amount_usd"] is None or isinstance(expected["amount_usd"], float)
        assert expected["unit"] is None or isinstance(expected["unit"], str)
        assert isinstance(expected["caveats"], list)
    elif track == "schedule":
        assert isinstance(expected["confirmed"], bool)
        if expected["slot_iso"] is None:
            assert expected["confirmed"] is False
        else:
            assert datetime.fromisoformat(expected["slot_iso"]).tzinfo is not None
    else:
        assert expected["action"] in ("reduced", "canceled", "refused", "none")
        assert expected["saved_usd"] is None or isinstance(expected["saved_usd"], float)
        if expected["effective_date"] is not None:
            datetime.fromisoformat(expected["effective_date"])
    assert expected["note"] is None or isinstance(expected["note"], str)

    turns = data["turns"]
    assert isinstance(turns, list)
    for turn in turns:
        assert set(turn) == {"speaker", "text"}
        assert turn["speaker"] in ("agent", "user")
        assert isinstance(turn["text"], str) and turn["text"].strip()
    if turns:
        assert turns[0]["speaker"] == "agent"
        assert turns[0]["text"].startswith(OPENER)
        assert any(AI_LINE in t["text"] for t in turns if t["speaker"] == "agent")

    # the label is self-consistent under the scorer and the recap flags describe the transcript
    assert acc.score(expected, expected, track)["exact"] is True
    assert (acc.recap_line(turns) is not None) == data["has_recap"]
    checkable = acc.recap_value(expected) is not None
    if data["has_recap"] and checkable:
        assert acc.recap_agreement(turns, expected) is (not data["recap_disagrees"])


# ------------------------------------------------------------------ eval script, offline


def test_eval_exits_2_without_openai_key(monkeypatch, capsys, tmp_path):
    mod = load_eval_module()
    monkeypatch.setattr(mod, "load_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = tmp_path / "report.json"
    assert mod.main(["--out", str(out), "--md", str(tmp_path / "EVAL.md")]) == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    assert not out.exists()


def test_frozen_clock_reaches_the_prompt(monkeypatch):
    mod = load_eval_module()
    monkeypatch.setattr(ex, "datetime", mod.FrozenClock)
    token = mod.CALL_TIME.set(datetime.fromisoformat("2026-10-13T14:05:00-07:00"))
    try:
        prompt = ex._user_prompt("schedule", "Agent: hi")
    finally:
        mod.CALL_TIME.reset(token)
    assert "Call time: 2026-10-13T14:05-07:00" in prompt
    assert isinstance(mod.FrozenClock.now(), datetime)  # no fixture time set: falls through to the real clock


CANNED = {
    "quote": {"amount_usd": 1450, "unit": "flat", "caveats": [], "note": None},
    "schedule": {"slot_iso": "2026-10-15T14:30:00-07:00", "confirmed": True, "note": None},
    "negotiate": {"action": "reduced", "saved_usd": 25, "effective_date": "2026-11-01", "note": None},
}


async def test_evaluate_offline_freezes_clock_per_fixture(monkeypatch, tmp_path):
    mod = load_eval_module()
    seen_call_times: list[str] = []

    async def fake_structured(system, user, schema, timeout=20.0):
        track = user.split("\n", 1)[0].removeprefix("Track: ").strip()
        for line in user.splitlines():
            if line.startswith("Call time: "):
                seen_call_times.append(line.removeprefix("Call time: ").strip())
        return schema(**CANNED[track])

    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(ex, "datetime", mod.FrozenClock)
    loaded = mod.load_fixtures(FIXTURE_DIR)
    assert len(loaded) == 24
    cases = await mod.evaluate(loaded, concurrency=4)
    assert len(cases) == 24

    # every non-empty fixture reached the model with ITS call time, even with four in flight at once
    want = sorted(mod.parse_call_time(f["call_time"]).isoformat(timespec="minutes") for f in loaded if f["turns"])
    assert sorted(seen_call_times) == want
    assert "2026-10-13T16:40-07:00" in seen_call_times and "2026-10-13T10:12-07:00" in seen_call_times

    by_id = {c["id"]: c for c in cases}
    assert by_id["quote_01_flat_clean"]["exact"] is True
    assert by_id["quote_08_recap_disagrees"]["fields"]["amount_usd"] is False
    assert by_id["schedule_01_dentist_confirmed"]["exact"] is True
    assert by_id["negotiate_01_reduced_effective"]["exact"] is True
    assert by_id["negotiate_08_empty"]["actual"] == ex.empty_outcome("negotiate", "empty transcript")
    assert by_id["negotiate_08_empty"]["exact"] is True
    assert by_id["negotiate_08_empty"]["recap_agrees"] is None
    assert by_id["quote_01_flat_clean"]["recap_agrees"] is True
    for case in cases:
        assert set(case) >= {"id", "track", "scenario", "exact", "fields", "expected", "actual", "seconds"}
        assert case["error"] is None

    report = mod.build_report(cases, "fake-model")
    assert set(report) >= {"generated_at", "model", "n", "exact_rate", "field_rates", "by_track", "cases"}
    assert report["n"] == 24 and report["model"] == "fake-model"
    assert set(report["by_track"]) == {"quote", "schedule", "negotiate"}
    assert report["fixtures"]["recap_disagrees"] == 2 and report["fixtures"]["no_recap"] >= 3

    markdown = mod.render_markdown(report)
    assert "| quote_01_flat_clean | quote | yes |" in markdown
    assert "## Misses" in markdown and "## Method" in markdown
    out = tmp_path / "eval-report.json"
    out.write_text(json.dumps(report))
    assert json.loads(out.read_text())["n"] == 24
