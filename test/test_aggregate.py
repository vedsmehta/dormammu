"""Aggregator tests: nulls last and never best, multiplier on a known set, failed calls excluded from wall clock."""

from __future__ import annotations

import copy

import pytest

from aggregator import aggregate as agg


def call(cid, track, state="done", outcome=None, start=None, end=None, duration=None, simulated=False):
    return {
        "id": cid, "track": track, "state": state, "outcome": outcome, "started_at": start, "ended_at": end,
        "duration_seconds": duration, "simulated": simulated,
    }


def quote(amount):
    return {"track": "quote", "amount_usd": amount, "unit": None, "caveats": [], "note": None}


def slot(iso, confirmed=True):
    return {"track": "schedule", "slot_iso": iso, "confirmed": confirmed, "note": None}


def saved(amount, action="reduced"):
    return {"track": "negotiate", "action": action, "saved_usd": amount, "effective_date": None, "note": None}


def ids(rows):
    return [r["id"] for r in rows]


def test_quote_nulls_last_and_never_best():
    run = {"calls": [
        call("a", "quote", outcome=quote(None)),
        call("b", "quote", outcome=quote(1240)),
        call("c", "quote", outcome=quote(None)),
        call("d", "quote", outcome=quote(980)),
        call("e", "quote", state="failed", outcome=None),
    ]}
    rows = agg.aggregate(run)["tracks"]["quote"]
    assert ids(rows) == ["d", "b", "a", "c", "e"]
    assert [r["best"] for r in rows] == [True, False, False, False, False]


def test_quote_all_null_has_no_best():
    run = {"calls": [call("a", "quote", outcome=quote(None)), call("b", "quote", outcome=None)]}
    rows = agg.aggregate(run)["tracks"]["quote"]
    assert not any(r["best"] for r in rows)
    assert len(rows) == 2


def test_schedule_order_confirmed_then_unconfirmed_then_null():
    run = {"calls": [
        call("late", "schedule", outcome=slot("2026-10-20T09:00:00-07:00")),
        call("none", "schedule", outcome=slot(None, confirmed=False)),
        call("unconf", "schedule", outcome=slot("2026-10-12T09:00:00-07:00", confirmed=False)),
        call("early", "schedule", outcome=slot("2026-10-15T14:30:00-07:00")),
        call("utc", "schedule", outcome=slot("2026-10-15T21:00:00Z")),
    ]}
    rows = agg.aggregate(run)["tracks"]["schedule"]
    assert ids(rows) == ["utc", "early", "late", "unconf", "none"]
    assert [r["best"] for r in rows] == [True, False, False, False, False]


def test_negotiate_descending_saved_nulls_last():
    run = {"calls": [
        call("n", "negotiate", outcome=saved(None, "refused")),
        call("s", "negotiate", outcome=saved(10)),
        call("b", "negotiate", outcome=saved(25)),
        call("z", "negotiate", outcome=saved(0, "none")),
    ]}
    rows = agg.aggregate(run)["tracks"]["negotiate"]
    assert ids(rows) == ["b", "s", "z", "n"]
    assert [r["best"] for r in rows] == [True, False, False, False]
    only_null = agg.aggregate({"calls": [call("n", "negotiate", outcome=saved(None, "refused"))]})
    assert only_null["tracks"]["negotiate"][0]["best"] is False


def test_multiplier_on_known_set():
    run = {"calls": [
        call("a", "quote", start="2026-10-11T20:00:00Z", end="2026-10-11T20:01:00Z", duration=60, outcome=quote(1)),
        call("b", "quote", start="2026-10-11T20:00:05Z", end="2026-10-11T20:01:40Z", duration=90, outcome=quote(2)),
    ]}
    view = agg.aggregate(run)
    assert view["wall_clock_seconds"] == pytest.approx(100.0)
    assert view["talk_seconds"] == pytest.approx(150.0)
    assert view["dial_overhead_seconds"] == agg.DIAL_OVERHEAD_SECONDS
    expected_seq = 150.0 + 2 * agg.DIAL_OVERHEAD_SECONDS
    assert view["sequential_estimate_seconds"] == pytest.approx(expected_seq)
    assert view["multiplier"] == pytest.approx(expected_seq / 100.0)
    assert view["n_calls"] == 2 and view["n_completed"] == 2 and view["n_failed"] == 0 and view["n_live"] == 0


def test_failed_and_live_excluded_from_wall_clock():
    run = {"calls": [
        call("a", "quote", start="2026-10-11T20:00:00+00:00", end="2026-10-11T20:01:00+00:00", duration=60,
             outcome=quote(5)),
        call("f", "quote", state="failed", start="2026-10-11T19:00:00+00:00", end="2026-10-11T20:30:00+00:00",
             duration=0),
        call("l", "schedule", state="talking", start="2026-10-11T18:00:00+00:00", simulated=True),
        call("p", "negotiate", state="planned"),
    ]}
    view = agg.aggregate(run)
    assert view["wall_clock_seconds"] == pytest.approx(60.0)
    assert view["talk_seconds"] == pytest.approx(60.0)
    assert view["sequential_estimate_seconds"] == pytest.approx(60.0 + agg.DIAL_OVERHEAD_SECONDS)
    assert view["n_calls"] == 4
    assert view["n_completed"] == 1
    assert view["n_failed"] == 1
    assert view["n_live"] == 2
    assert view["n_simulated"] == 1


def test_empty_and_no_timestamps():
    view = agg.aggregate({"calls": []})
    assert view["tracks"] == {"quote": [], "schedule": [], "negotiate": []}
    assert view["wall_clock_seconds"] is None
    assert view["talk_seconds"] == 0.0
    assert view["sequential_estimate_seconds"] is None
    assert view["multiplier"] is None
    assert view["n_calls"] == 0
    assert agg.aggregate({})["n_calls"] == 0

    view = agg.aggregate({"calls": [call("a", "quote", duration=30, outcome=quote(1))]})
    assert view["wall_clock_seconds"] is None
    assert view["multiplier"] is None
    assert view["sequential_estimate_seconds"] == pytest.approx(30.0 + agg.DIAL_OVERHEAD_SECONDS)


def test_zero_wall_clock_gives_no_multiplier():
    run = {"calls": [call("a", "quote", start="2026-10-11T20:00:00Z", end="2026-10-11T20:00:00Z", duration=10,
                          outcome=quote(1))]}
    view = agg.aggregate(run)
    assert view["wall_clock_seconds"] == 0.0
    assert view["multiplier"] is None


def test_pure_and_json_outcome_tolerated():
    run = {"calls": [
        call("a", "quote", outcome='{"track":"quote","amount_usd":50}'),
        call("b", "quote", outcome=quote(40)),
        call("x", "mystery", outcome=None),
    ]}
    before = copy.deepcopy(run)
    view = agg.aggregate(run)
    assert run == before
    assert ids(view["tracks"]["quote"]) == ["b", "a"]
    assert "best" not in run["calls"][0]
    assert view["n_calls"] == 3


def test_parse_ts():
    assert agg.parse_ts("2026-10-11T20:00:00Z").tzinfo is not None
    assert agg.parse_ts("2026-10-11T20:00:00").tzinfo is not None
    assert agg.parse_ts("garbage") is None
    assert agg.parse_ts(None) is None
    assert isinstance(agg.DIAL_OVERHEAD_SECONDS, int)
