from datetime import date

from aggregator.ledger import add_months, ledger_rows, stated_promo_months

TODAY = date(2026, 10, 11)


def _call(track, outcome, **extra):
    row = {
        "id": "call-1",
        "run_id": "run-1",
        "vendor_id": "vendor-1",
        "vendor_key": "comcast",
        "track": track,
        "target_name": "Comcast",
        "target_phone": "+1***",
        "state": "done",
        "transcript": None,
        "transcript_turns": None,
        "outcome": outcome,
        "simulated": False,
    }
    row.update(extra)
    return row


def _assert_common(row):
    assert row["vendor_id"] == "vendor-1"
    assert row["source_call"] == "call-1"
    assert "id" not in row and "user_id" not in row


# quote


def test_quote_row_30_day_validity():
    outcome = {"track": "quote", "amount_usd": 89.99, "unit": "per month", "caveats": [], "note": None}
    (row,) = ledger_rows(_call("quote", outcome), today=TODAY)
    _assert_common(row)
    assert row["kind"] == "quote"
    assert row["label"] == "Comcast quote $89.99 per month (30-day validity assumed)"
    assert row["amount_usd"] == 89.99
    assert row["effective_on"] == "2026-10-11"
    assert row["expires_on"] == "2026-11-10"


def test_quote_whole_dollars_and_missing_unit():
    outcome = {"track": "quote", "amount_usd": 120.0, "unit": None, "caveats": [], "note": None}
    (row,) = ledger_rows(_call("quote", outcome), today=TODAY)
    assert row["label"] == "Comcast quote $120 (30-day validity assumed)"


def test_quote_without_amount_yields_nothing():
    outcome = {"track": "quote", "amount_usd": None, "unit": None, "caveats": [], "note": "unreadable"}
    assert ledger_rows(_call("quote", outcome), today=TODAY) == []
    assert ledger_rows(_call("quote", {**outcome, "amount_usd": 0}), today=TODAY) == []
    assert ledger_rows(_call("quote", {**outcome, "amount_usd": "n/a"}), today=TODAY) == []


# schedule


def test_confirmed_slot_becomes_appointment():
    outcome = {"track": "schedule", "slot_iso": "2026-10-15T14:00:00-07:00", "confirmed": True, "note": None}
    (row,) = ledger_rows(_call("schedule", outcome, target_name="Dr. Patel"), today=TODAY)
    _assert_common(row)
    assert row["kind"] == "appointment"
    assert row["label"] == "Dr. Patel appointment"
    assert row["amount_usd"] is None
    assert row["effective_on"] == "2026-10-15"
    assert row["expires_on"] == "2026-10-15"


def test_unconfirmed_or_slotless_schedule_yields_nothing():
    base = {"track": "schedule", "slot_iso": "2026-10-15T14:00:00-07:00", "confirmed": False, "note": None}
    assert ledger_rows(_call("schedule", base), today=TODAY) == []
    assert ledger_rows(_call("schedule", {**base, "confirmed": True, "slot_iso": None}), today=TODAY) == []
    assert ledger_rows(_call("schedule", {**base, "confirmed": True, "slot_iso": "next tuesday"}), today=TODAY) == []


# negotiate


def test_reduced_promo_defaults_to_11_months():
    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 20, "effective_date": "2026-10-01", "note": None}
    (row,) = ledger_rows(_call("negotiate", outcome), today=TODAY)
    _assert_common(row)
    assert row["kind"] == "promo_expiry"
    assert row["label"] == "Comcast promo rate $20/mo off (11-month promo assumed)"
    assert row["amount_usd"] == 20
    assert row["effective_on"] == "2026-10-01"
    assert row["expires_on"] == "2027-09-01"


def test_stated_duration_in_transcript_wins():
    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 15.5, "effective_date": "2026-10-01", "note": None}
    call = _call("negotiate", outcome, transcript="Agent: that promo rate is locked for 6 months from today.")
    (row,) = ledger_rows(call, today=TODAY)
    assert row["label"] == "Comcast promo rate $15.50/mo off (6-month promo stated on call)"
    assert row["expires_on"] == "2027-03-01"


def test_stated_duration_in_note_or_caveats_or_turns():
    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 10, "effective_date": "2026-10-01",
               "note": "12-month promotional pricing"}
    (row,) = ledger_rows(_call("negotiate", outcome), today=TODAY)
    assert row["expires_on"] == "2027-09-01" and "12-month promo stated" in row["label"]

    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 10, "effective_date": "2026-10-01",
               "note": None, "caveats": ["valid 3 months"]}
    (row,) = ledger_rows(_call("negotiate", outcome), today=TODAY)
    assert row["expires_on"] == "2026-12-01"

    outcome = {"track": "negotiate", "action": "reduced", "saved_usd": 10, "effective_date": "2026-10-01", "note": None}
    turns = [{"speaker": "callee", "text": "That rate holds for 24 months."}]
    (row,) = ledger_rows(_call("negotiate", outcome, transcript_turns=turns), today=TODAY)
    assert row["expires_on"] == "2028-09-01"


def test_canceled_without_effective_date_starts_today():
    outcome = {"track": "negotiate", "action": "canceled", "saved_usd": 45, "effective_date": None, "note": None}
    (row,) = ledger_rows(_call("negotiate", outcome), today=TODAY)
    assert row["effective_on"] == "2026-10-11"
    assert row["expires_on"] == "2027-09-11"


def test_refused_or_unsaved_negotiation_yields_nothing():
    base = {"track": "negotiate", "action": "refused", "saved_usd": None, "effective_date": None, "note": None}
    assert ledger_rows(_call("negotiate", base), today=TODAY) == []
    assert ledger_rows(_call("negotiate", {**base, "action": "none", "saved_usd": 20}), today=TODAY) == []
    assert ledger_rows(_call("negotiate", {**base, "action": "reduced"}), today=TODAY) == []
    assert ledger_rows(_call("negotiate", {**base, "action": "reduced", "saved_usd": 0}), today=TODAY) == []


# null safety and helpers


def test_null_outcome_unknown_track_and_garbage_rows():
    assert ledger_rows(_call("quote", None), today=TODAY) == []
    assert ledger_rows(_call("quote", "not a dict"), today=TODAY) == []
    assert ledger_rows(_call("mystery", {"track": "mystery", "amount_usd": 5}), today=TODAY) == []
    assert ledger_rows(None, today=TODAY) == []
    assert ledger_rows({}, today=TODAY) == []


def test_track_falls_back_to_call_row_track():
    (row,) = ledger_rows(_call("quote", {"amount_usd": 50, "unit": "flat"}), today=TODAY)
    assert row["kind"] == "quote"


def test_target_name_falls_back_to_vendor_key():
    outcome = {"track": "quote", "amount_usd": 50, "unit": None, "caveats": [], "note": None}
    (row,) = ledger_rows(_call("quote", outcome, target_name=None), today=TODAY)
    assert row["label"].startswith("comcast quote $50")


def test_default_today_is_used_when_omitted():
    outcome = {"track": "quote", "amount_usd": 50, "unit": None, "caveats": [], "note": None}
    (row,) = ledger_rows(_call("quote", outcome))
    assert row["effective_on"] == date.today().isoformat()


def test_add_months_clamps_month_end():
    assert add_months(date(2026, 3, 31), 11) == date(2027, 2, 28)
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 10, 1), 0) == date(2026, 10, 1)
    assert add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)


def test_stated_promo_months_parsing():
    assert stated_promo_months("locked in for 12 months") == 12
    assert stated_promo_months("a 6-month promo") == 6
    assert stated_promo_months("3 mos") == 3
    assert stated_promo_months("no duration here") is None
    assert stated_promo_months("") is None
