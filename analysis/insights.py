"""Insights over store rows: what many calls and runs say about each vendor, track and time of day.

Pure Python, no network. Reads `store.list_runs` + `store.get_run` (capped by `max_runs`) and
`store.list_events` and folds them into one JSON-serializable report. Every number in the report is
computed from rows; nothing is estimated except the sequential-time figure, which is labeled "est."
exactly as aggregator.aggregate does.

Null-safety is the contract: missing outcomes, missing ended_at, failed rows, outcomes stored as JSON
strings and unknown termination types all degrade to None or zero, never to an exception.

Timestamps in the store are UTC. Hour-of-day buckets use `tz` (default DORMAMMU_TZ or
America/Los_Angeles) because "call after 2pm" only means something in the user's clock.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from aggregator.aggregate import DIAL_OVERHEAD_SECONDS, parse_ts

TRACKS = ("quote", "schedule", "negotiate")
TERMINAL_STATES = ("done", "failed")
DEFAULT_MAX_RUNS = 200
EVENTS_LIMIT = 5000  # PostgREST caps a page at 1000 rows by default; the count is labeled as a sample
MIN_TIME_OF_DAY_CALLS = 20
MIN_BUCKET_CALLS = 5
MIN_VENDOR_CALLS_FOR_TIME_ADVICE = 4
MAX_RECOMMENDATIONS = 10
DEFAULT_TZ = "America/Los_Angeles"

# Seed-data tags shared with scripts/seed_demo.py. Changing them here changes what counts as seeded.
SEEDED_REQUEST_PREFIX = "[seeded] "
SEEDED_ERROR_TAG = "seeded"

HOUR_BUCKETS: tuple[tuple[str, int, int, str], ...] = (
    ("before_11", 0, 11, "before 11am"),
    ("11_to_14", 11, 14, "11am to 2pm"),
    ("14_to_17", 14, 17, "2pm to 5pm"),
    ("after_17", 17, 24, "after 5pm"),
)
FAILURE_LABELS = {"voicemail": "voicemail", "no-answer": "no answer", "busy": "busy", "unknown": "unknown"}


# ---------------------------------------------------------------------------------------------
# small helpers


def _safe(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _rate(numerator: float, denominator: float) -> float | None:
    return None if not denominator else round(float(numerator) / float(denominator), 3)


def _mean(values: list[float]) -> float | None:
    return _round(statistics.fmean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return _round(statistics.median(values)) if values else None


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _short_date(dt: datetime | None) -> str | None:
    return f"{dt:%b} {dt.day}" if dt else None


def _outcome(call: dict) -> dict:
    outcome = call.get("outcome")
    if isinstance(outcome, str):
        try:
            outcome = json.loads(outcome)
        except ValueError:
            return {}
    return outcome if isinstance(outcome, dict) else {}


def _try_zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return None


def _zone(tz: str | None) -> ZoneInfo | Any:
    for name in (tz, os.environ.get("DORMAMMU_TZ"), DEFAULT_TZ):
        zone = _try_zone(name)
        if zone is not None:
            return zone
    return UTC


def _bucket_for_hour(hour: int) -> str:
    for key, start, end, _label in HOUR_BUCKETS:
        if start <= hour < end:
            return key
    return HOUR_BUCKETS[-1][0]


def _bucket_label(key: str) -> str:
    return next((label for k, _s, _e, label in HOUR_BUCKETS if k == key), key)


def _termination(call: dict) -> str:
    kind = call.get("termination_type")
    if kind in (None, "", "completed"):
        if call.get("state") == "failed":
            return "unknown"
        return "completed"
    return str(kind)


def is_seeded_call(call: dict) -> bool:
    return bool(call.get("simulated")) and call.get("error") == SEEDED_ERROR_TAG


def is_seeded_run(run: dict) -> bool:
    return str(run.get("request") or "").startswith(SEEDED_REQUEST_PREFIX)


def is_seeded_event(event: dict) -> bool:
    data = event.get("data")
    return isinstance(data, dict) and bool(data.get("seeded"))


# ---------------------------------------------------------------------------------------------
# loading


def load_rows(
    store: Any, user_id: str, *, max_runs: int = DEFAULT_MAX_RUNS, events_limit: int = EVENTS_LIMIT
) -> tuple[list[dict], list[dict], list[dict]]:
    """(runs with their calls, flat calls, events) for one user. Never raises; missing pieces are empty.

    One query per table: list_runs, list_calls_for_user (paged), list_events. Stores without
    list_calls_for_user fall back to one get_run per run."""
    runs_raw = [
        r
        for r in (_safe(store.list_runs, user_id, limit=max_runs) or [])
        if isinstance(r, dict) and r.get("id")
    ]
    by_run: dict[str, list[dict]] = {}
    if hasattr(store, "list_calls_for_user"):
        offset = 0
        for _ in range(CALLS_MAX_PAGES):
            page = _safe(store.list_calls_for_user, user_id, limit=CALLS_PAGE_SIZE, offset=offset) or []
            for call in page:
                if isinstance(call, dict) and call.get("run_id"):
                    by_run.setdefault(str(call["run_id"]), []).append(call)
            if len(page) < CALLS_PAGE_SIZE:
                break
            offset += CALLS_PAGE_SIZE
    else:
        for run in runs_raw:
            full = _safe(store.get_run, run["id"], user_id)
            rows = (full or {}).get("calls") if isinstance(full, dict) else None
            by_run[str(run["id"])] = [c for c in (rows or []) if isinstance(c, dict)]
    runs: list[dict] = []
    calls: list[dict] = []
    for run in runs_raw:
        rows = sorted(by_run.get(str(run["id"]), []), key=lambda c: str(c.get("started_at") or ""))
        full = {**run, "calls": rows}
        runs.append(full)
        for call in rows:
            calls.append({**call, "run_id": call.get("run_id") or run["id"], "_run": full})
    events = [e for e in (_safe(store.list_events, user_id, limit=events_limit) or []) if isinstance(e, dict)]
    return runs, calls, events


CALLS_PAGE_SIZE = 500
CALLS_MAX_PAGES = 10

_CACHE: dict[tuple[str, int], tuple[float, dict]] = {}


def invalidate_insights(user_id: str | None = None) -> None:
    """Drop cached reports (all, or one user's)."""
    if user_id is None:
        _CACHE.clear()
        return
    for key in [k for k in _CACHE if k[0] == user_id]:
        _CACHE.pop(key, None)


def _prepare(calls: list[dict], zone: Any) -> list[dict]:
    """Attach parsed timestamps, parsed outcome and local hour to each call row (copies)."""
    out = []
    for call in calls:
        started = parse_ts(call.get("started_at"))
        ended = parse_ts(call.get("ended_at"))
        local_hour = started.astimezone(zone).hour if started else None
        out.append(
            {
                **call,
                "_started": started,
                "_ended": ended,
                "_outcome": _outcome(call),
                "_hour": local_hour,
                "_bucket": _bucket_for_hour(local_hour) if local_hour is not None else None,
                "_vendor": str(call.get("vendor_key") or call.get("target_name") or "unknown"),
                "_terminal": call.get("state") in TERMINAL_STATES,
                "_done": call.get("state") == "done",
                "_failed": call.get("state") == "failed",
                "_termination": _termination(call),
            }
        )
    out.sort(key=lambda c: (c["_started"] or datetime.min.replace(tzinfo=UTC), str(c.get("id") or "")))
    return out


# ---------------------------------------------------------------------------------------------
# per-vendor


def _cheapest_per_run(calls: list[dict]) -> tuple[Counter, Counter]:
    """(times a vendor was cheapest, times it was compared) over runs with at least two priced quotes."""
    by_run: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for call in calls:
        if call.get("track") != "quote" or not call["_done"]:
            continue
        amount = _num(call["_outcome"].get("amount_usd"))
        if amount is not None:
            by_run[str(call.get("run_id"))].append((call["_vendor"], amount))
    cheapest: Counter = Counter()
    compared: Counter = Counter()
    for priced in by_run.values():
        if len(priced) < 2:
            continue
        best = min(amount for _v, amount in priced)
        for vendor, amount in priced:
            compared[vendor] += 1
            if amount == best:
                cheapest[vendor] += 1
    return cheapest, compared


def _quote_trend(amounts: list[float]) -> str | None:
    if len(amounts) < 2:
        return None
    first, last = amounts[0], amounts[-1]
    if first <= 0:
        return None
    change = (last - first) / first
    if change <= -0.01:
        return "down"
    if change >= 0.01:
        return "up"
    return "flat"


def _last_outcome(call: dict) -> dict:
    outcome = call["_outcome"]
    base = {
        "at": _iso(call["_ended"] or call["_started"]),
        "track": outcome.get("track") or call.get("track"),
        "simulated": bool(call.get("simulated")),
    }
    for key in ("amount_usd", "unit", "slot_iso", "confirmed", "action", "saved_usd", "effective_date"):
        if key in outcome:
            base[key] = outcome.get(key)
    return base


def _takeaway(card: dict) -> str:
    name = card["name"]
    n_terminal = card["n_done"] + card["n_failed"]
    if n_terminal == 0:
        return f"{name}: no finished calls yet."
    answered = f"answered {card['n_done']} of {n_terminal}"
    if card["n_done"] == 0:
        modes = ", ".join(f"{FAILURE_LABELS.get(k, k)} {v}" for k, v in card["failure_breakdown"].items())
        return f"{name}: never answered in {n_terminal} calls ({modes})."
    track = card["track"]
    if track == "quote":
        tail = ""
        if card.get("n_compared"):
            tail = f", cheapest {card['times_cheapest']} of {card['n_compared']} times"
        best = card.get("best_quote_usd")
        best_text = f", best quote {_money(best)}" if best is not None else ""
        return f"{name}: {answered}{tail}{best_text}."
    if track == "schedule":
        return f"{name}: {answered}, confirmed {card['n_confirmed']} of {card['n_with_outcome']}."
    if track == "negotiate":
        avg = card.get("avg_saved_usd")
        avg_text = f", {_money(avg)}/mo on average" if avg is not None else ""
        return f"{name}: {answered}, reduced the bill {card['n_reduced']} of {card['n_with_outcome']} times{avg_text}."
    return f"{name}: {answered}."


def vendor_scorecards(calls: list[dict]) -> dict[str, dict]:
    """One card per vendor_key. `calls` must come from _prepare()."""
    cheapest, compared = _cheapest_per_run(calls)
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for call in calls:
        by_vendor[call["_vendor"]].append(call)

    cards: dict[str, dict] = {}
    for vendor, rows in by_vendor.items():
        done = [c for c in rows if c["_done"]]
        failed = [c for c in rows if c["_failed"]]
        with_outcome = [c for c in done if c["_outcome"]]
        tracks = Counter(str(c.get("track") or "") for c in rows if c.get("track"))
        track = tracks.most_common(1)[0][0] if tracks else None
        names = [str(c.get("target_name")) for c in reversed(rows) if c.get("target_name")]
        durations = [d for d in (_num(c.get("duration_seconds")) for c in done) if d is not None]
        failure_breakdown = Counter(c["_termination"] for c in failed)
        bucket_counts: dict[str, dict[str, int]] = {}
        for c in rows:
            if not c["_terminal"] or c["_bucket"] is None:
                continue
            slot = bucket_counts.setdefault(c["_bucket"], {"n_calls": 0, "n_answered": 0, "n_voicemail": 0})
            slot["n_calls"] += 1
            slot["n_answered"] += int(c["_done"])
            slot["n_voicemail"] += int(c["_termination"] == "voicemail")

        card: dict[str, Any] = {
            "vendor_key": vendor,
            "name": names[0] if names else vendor,
            "track": track,
            "n_calls": len(rows),
            "n_done": len(done),
            "n_failed": len(failed),
            "n_live": sum(1 for c in rows if not c["_terminal"]),
            "n_simulated": sum(1 for c in rows if c.get("simulated")),
            "n_with_prior_context": sum(1 for c in rows if c.get("prior_context")),
            "failure_breakdown": dict(failure_breakdown),
            "answer_rate": _rate(len(done), len(done) + len(failed)),
            "avg_duration_seconds": _mean(durations),
            "first_called_at": _iso(rows[0]["_started"]) if rows else None,
            "last_called_at": _iso(max((c["_started"] for c in rows if c["_started"]), default=None)),
            "last_outcome": _last_outcome(with_outcome[-1]) if with_outcome else None,
            "time_of_day": bucket_counts,
            "n_with_outcome": len(with_outcome),
        }
        if track == "quote":
            amounts = [
                a for a in (_num(c["_outcome"].get("amount_usd")) for c in with_outcome) if a is not None
            ]
            card.update(
                {
                    "n_quotes": len(amounts),
                    "best_quote_usd": _round(min(amounts)) if amounts else None,
                    "latest_quote_usd": _round(amounts[-1]) if amounts else None,
                    "first_quote_usd": _round(amounts[0]) if amounts else None,
                    "avg_quote_usd": _mean(amounts),
                    "quote_trend": _quote_trend(amounts),
                    "times_cheapest": cheapest.get(vendor, 0),
                    "n_compared": compared.get(vendor, 0),
                }
            )
        elif track == "schedule":
            confirmed = [c for c in with_outcome if c["_outcome"].get("confirmed")]
            card.update(
                {
                    "n_confirmed": len(confirmed),
                    "confirmed_rate": _rate(len(confirmed), len(with_outcome)),
                    "latest_slot_iso": next(
                        (
                            c["_outcome"].get("slot_iso")
                            for c in reversed(with_outcome)
                            if c["_outcome"].get("slot_iso")
                        ),
                        None,
                    ),
                }
            )
        elif track == "negotiate":
            actions = Counter(str(c["_outcome"].get("action") or "none") for c in with_outcome)
            saved = [
                s
                for s in (_num(c["_outcome"].get("saved_usd")) for c in with_outcome)
                if s is not None and s > 0
            ]
            card.update(
                {
                    "n_reduced": actions.get("reduced", 0),
                    "n_canceled": actions.get("canceled", 0),
                    "n_refused": actions.get("refused", 0),
                    "reduction_rate": _rate(
                        actions.get("reduced", 0) + actions.get("canceled", 0), len(with_outcome)
                    ),
                    "total_saved_usd_per_month": _round(sum(saved)) if saved else 0.0,
                    "avg_saved_usd": _mean(saved),
                    "latest_saved_usd": _round(saved[-1]) if saved else None,
                    "best_saved_usd": _round(max(saved)) if saved else None,
                }
            )
        card["takeaway"] = _takeaway(card)
        cards[vendor] = card
    return dict(sorted(cards.items(), key=lambda kv: (-kv[1]["n_calls"], kv[0])))


# ---------------------------------------------------------------------------------------------
# per-track, failures, time of day


def _run_window(rows: list[dict]) -> float | None:
    starts = [c["_started"] for c in rows if c["_started"]]
    ends = [c["_ended"] for c in rows if c["_ended"]]
    if not starts or not ends:
        return None
    wall = (max(ends) - min(starts)).total_seconds()
    return wall if wall >= 0 else None


def track_stats(calls: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for track in TRACKS:
        rows = [c for c in calls if c.get("track") == track]
        done = [c for c in rows if c["_done"]]
        failed = [c for c in rows if c["_failed"]]
        talk = [d for d in (_num(c.get("duration_seconds")) for c in done) if d is not None]
        by_run: dict[str, list[dict]] = defaultdict(list)
        for c in rows:
            by_run[str(c.get("run_id"))].append(c)
        walls: list[float] = []
        multipliers: list[float] = []
        for run_rows in by_run.values():
            run_done = [c for c in run_rows if c["_done"]]
            wall = _run_window(run_done)
            if wall is None:
                continue
            walls.append(wall)
            run_talk = sum(_num(c.get("duration_seconds")) or 0.0 for c in run_done)
            sequential = run_talk + len(run_done) * DIAL_OVERHEAD_SECONDS
            if wall > 0 and run_done:
                multipliers.append(sequential / wall)
        if track == "quote":
            values = [a for a in (_num(c["_outcome"].get("amount_usd")) for c in done) if a is not None]
            label = "median quote (usd)"
        elif track == "negotiate":
            values = [
                s for s in (_num(c["_outcome"].get("saved_usd")) for c in done) if s is not None and s > 0
            ]
            label = "median saved (usd per month)"
        else:
            values = []
            for c in done:
                slot = parse_ts(c["_outcome"].get("slot_iso"))
                anchor = c["_ended"] or c["_started"]
                if slot and anchor:
                    values.append((slot - anchor).total_seconds() / 86400.0)
            label = "median days until the booked slot"
        out[track] = {
            "n_calls": len(rows),
            "n_done": len(done),
            "n_failed": len(failed),
            "n_runs": len(by_run),
            "answer_rate": _rate(len(done), len(done) + len(failed)),
            "avg_calls_per_run": _mean([float(len(v)) for v in by_run.values()]),
            "avg_talk_seconds": _mean(talk),
            "total_talk_seconds": _round(sum(talk)) if talk else 0.0,
            "avg_wall_clock_seconds_per_run": _mean(walls),
            "avg_multiplier_est": _mean(multipliers),
            "multiplier_label": "est.",
            "dial_overhead_seconds": DIAL_OVERHEAD_SECONDS,
            "median_outcome": _median(values),
            "median_outcome_label": label,
        }
    return out


def failure_modes(calls: list[dict]) -> dict:
    failed = [c for c in calls if c["_failed"]]
    terminal = [c for c in calls if c["_terminal"]]
    counts = Counter(c["_termination"] for c in failed)
    by_track: dict[str, dict[str, int]] = {}
    for c in failed:
        by_track.setdefault(str(c.get("track") or "unknown"), Counter())[c["_termination"]] += 1
    return {
        "n_failed": len(failed),
        "n_terminal": len(terminal),
        "failure_rate": _rate(len(failed), len(terminal)),
        "counts": dict(counts.most_common()),
        "share": {k: _rate(v, len(failed)) for k, v in counts.items()},
        "by_track": {t: dict(c) for t, c in by_track.items()},
    }


def time_of_day(calls: list[dict], zone_name: str) -> dict:
    terminal = [c for c in calls if c["_terminal"] and c["_bucket"] is not None]
    buckets: list[dict] = []
    for key, start, end, label in HOUR_BUCKETS:
        rows = [c for c in terminal if c["_bucket"] == key]
        answered = sum(1 for c in rows if c["_done"])
        buckets.append(
            {
                "bucket": key,
                "label": label,
                "hours": [start, end],
                "n_calls": len(rows),
                "n_answered": answered,
                "n_voicemail": sum(1 for c in rows if c["_termination"] == "voicemail"),
                "answer_rate": _rate(answered, len(rows)),
            }
        )
    enough = (
        len(terminal) >= MIN_TIME_OF_DAY_CALLS
        and sum(1 for b in buckets if b["n_calls"] >= MIN_BUCKET_CALLS) >= 2
    )
    out: dict[str, Any] = {
        "timezone": zone_name,
        "n_calls": len(terminal),
        "enough_data": enough,
        "min_calls": MIN_TIME_OF_DAY_CALLS,
        "buckets": buckets if enough else [],
        "per_hour": {},
        "best_bucket": None,
        "worst_bucket": None,
    }
    if not enough:
        return out
    per_hour: dict[int, dict[str, int]] = defaultdict(lambda: {"n_calls": 0, "n_answered": 0})
    for c in terminal:
        per_hour[c["_hour"]]["n_calls"] += 1
        per_hour[c["_hour"]]["n_answered"] += int(c["_done"])
    out["per_hour"] = {
        str(h): {**v, "answer_rate": _rate(v["n_answered"], v["n_calls"])}
        for h, v in sorted(per_hour.items())
    }
    rated = [b for b in buckets if b["n_calls"] >= MIN_BUCKET_CALLS and b["answer_rate"] is not None]
    if rated:
        out["best_bucket"] = max(rated, key=lambda b: (b["answer_rate"], b["n_calls"]))["bucket"]
        out["worst_bucket"] = min(rated, key=lambda b: (b["answer_rate"], -b["n_calls"]))["bucket"]
    return out


# ---------------------------------------------------------------------------------------------
# context effect: first call to a vendor vs the calls after it


def context_effect(calls: list[dict]) -> dict:
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        if c["_terminal"]:
            by_vendor[c["_vendor"]].append(c)
    repeat = {v: rows for v, rows in by_vendor.items() if len(rows) > 1}

    later_all = [c for rows in repeat.values() for c in rows[1:]]
    first_all = [rows[0] for rows in repeat.values()]
    quote_first: list[float] = []
    quote_later: list[float] = []
    quote_vendors = 0
    neg_first: list[float] = []
    neg_later: list[float] = []
    neg_vendors = 0
    sched_first: list[int] = []
    sched_later: list[int] = []
    sched_vendors = 0
    for rows in repeat.values():
        done = [c for c in rows if c["_done"] and c["_outcome"]]
        if len(done) < 2:
            continue
        first, later = done[0], done[1:]
        track = first.get("track")
        if track == "quote":
            a0 = _num(first["_outcome"].get("amount_usd"))
            later_amounts = [
                a for a in (_num(c["_outcome"].get("amount_usd")) for c in later) if a is not None
            ]
            if a0 is not None and later_amounts:
                quote_vendors += 1
                quote_first.append(a0)
                quote_later.extend(later_amounts)
        elif track == "negotiate":
            s0 = _num(first["_outcome"].get("saved_usd")) or 0.0
            later_saved = [(_num(c["_outcome"].get("saved_usd")) or 0.0) for c in later]
            neg_vendors += 1
            neg_first.append(s0)
            neg_later.extend(later_saved)
        elif track == "schedule":
            sched_vendors += 1
            sched_first.append(int(bool(first["_outcome"].get("confirmed"))))
            sched_later.extend(int(bool(c["_outcome"].get("confirmed"))) for c in later)

    quote_delta = (
        _round(statistics.fmean(quote_first) - statistics.fmean(quote_later))
        if quote_first and quote_later
        else None
    )
    neg_delta = (
        _round(statistics.fmean(neg_later) - statistics.fmean(neg_first)) if neg_first and neg_later else None
    )
    return {
        "n_repeat_vendors": len(repeat),
        "n_first_calls": len(first_all),
        "n_later_calls": len(later_all),
        "n_later_with_prior_context": sum(1 for c in later_all if c.get("prior_context")),
        "share_later_with_prior_context": _rate(
            sum(1 for c in later_all if c.get("prior_context")), len(later_all)
        ),
        "first_answer_rate": _rate(sum(1 for c in first_all if c["_done"]), len(first_all)),
        "later_answer_rate": _rate(sum(1 for c in later_all if c["_done"]), len(later_all)),
        "quote": {
            "n_vendors": quote_vendors,
            "n_later_calls": len(quote_later),
            "avg_first_usd": _mean(quote_first),
            "avg_later_usd": _mean(quote_later),
            "later_lower_by_usd": quote_delta,
        },
        "negotiate": {
            "n_vendors": neg_vendors,
            "n_later_calls": len(neg_later),
            "avg_first_saved_usd": _mean(neg_first),
            "avg_later_saved_usd": _mean(neg_later),
            "later_saved_more_usd": neg_delta,
        },
        "schedule": {
            "n_vendors": sched_vendors,
            "n_later_calls": len(sched_later),
            "first_confirmed_rate": _rate(sum(sched_first), len(sched_first)),
            "later_confirmed_rate": _rate(sum(sched_later), len(sched_later)),
        },
    }


# ---------------------------------------------------------------------------------------------
# recommendations


def _rec(
    kind: str, text: str, score: float, *, vendor_key: str | None = None, track: str | None = None, **evidence
):
    return {
        "kind": kind,
        "text": text,
        "score": round(score, 3),
        "vendor_key": vendor_key,
        "track": track,
        "evidence": evidence,
    }


def recommendations(cards: dict[str, dict], effect: dict, tod: dict) -> list[dict]:
    recs: list[dict] = []
    for key, card in cards.items():
        name = card["name"]
        n_terminal = card["n_done"] + card["n_failed"]
        if n_terminal == 0:
            continue
        weight = math.log1p(n_terminal)
        track = card.get("track")
        if track == "quote" and card.get("n_compared", 0) >= 2 and card.get("times_cheapest", 0) >= 2:
            share = card["times_cheapest"] / card["n_compared"]
            if share >= 0.5 and (card["answer_rate"] or 0) >= 0.6:
                recs.append(
                    _rec(
                        "call_first",
                        f"Call {name} first: answered {card['n_done']} of {n_terminal}, cheapest quote "
                        f"{card['times_cheapest']} of {card['n_compared']} times.",
                        3.0 * share * weight,
                        vendor_key=key,
                        track=track,
                        n_done=card["n_done"],
                        n_terminal=n_terminal,
                        times_cheapest=card["times_cheapest"],
                        n_compared=card["n_compared"],
                        best_quote_usd=card.get("best_quote_usd"),
                    )
                )
        if (
            track == "schedule"
            and card.get("n_with_outcome", 0) >= 2
            and (card.get("confirmed_rate") or 0) >= 0.75
        ):
            recs.append(
                _rec(
                    "book_first",
                    f"Book with {name} first: confirmed {card['n_confirmed']} of {card['n_with_outcome']} appointments.",
                    2.0 * card["confirmed_rate"] * weight,
                    vendor_key=key,
                    track=track,
                    n_confirmed=card["n_confirmed"],
                    n_with_outcome=card["n_with_outcome"],
                )
            )
        if track == "negotiate" and card.get("n_with_outcome", 0) >= 2:
            avg = card.get("avg_saved_usd")
            rate = card.get("reduction_rate") or 0
            if rate >= 0.5 and avg:
                recs.append(
                    _rec(
                        "retention_works",
                        f"{name} concedes: reduced or canceled {card['n_reduced'] + card['n_canceled']} of "
                        f"{card['n_with_outcome']} times, {_money(avg)}/mo on average.",
                        2.5 * rate * weight,
                        vendor_key=key,
                        track=track,
                        n_reduced=card["n_reduced"],
                        n_canceled=card["n_canceled"],
                        n_with_outcome=card["n_with_outcome"],
                        avg_saved_usd=avg,
                    )
                )
            elif card.get("n_refused", 0) >= 2 and rate < 0.5:
                recs.append(
                    _rec(
                        "retention_resists",
                        f"{name} refused {card['n_refused']} of {card['n_with_outcome']} times; lead with the cancel "
                        "ask instead of the discount ask.",
                        1.5 * weight,
                        vendor_key=key,
                        track=track,
                        n_refused=card["n_refused"],
                        n_with_outcome=card["n_with_outcome"],
                    )
                )
        buckets = card.get("time_of_day") or {}
        if n_terminal >= MIN_VENDOR_CALLS_FOR_TIME_ADVICE and len(buckets) >= 2:
            worst_key, worst = min(
                buckets.items(), key=lambda kv: (kv[1]["n_answered"] / kv[1]["n_calls"], -kv[1]["n_calls"])
            )
            best_key, best = max(
                buckets.items(), key=lambda kv: (kv[1]["n_answered"] / kv[1]["n_calls"], kv[1]["n_calls"])
            )
            worst_rate = worst["n_answered"] / worst["n_calls"]
            best_rate = best["n_answered"] / best["n_calls"]
            if (
                worst["n_voicemail"] >= 2
                and worst["n_calls"] >= 2
                and best["n_calls"] >= 2
                and best_rate - worst_rate >= 0.4
            ):
                recs.append(
                    _rec(
                        "time_of_day",
                        f"{name}: voicemail {worst['n_voicemail']} of {worst['n_calls']} times {_bucket_label(worst_key)}; "
                        f"call {_bucket_label(best_key)} (answered {best['n_answered']} of {best['n_calls']}).",
                        2.0 * (best_rate - worst_rate) * weight,
                        vendor_key=key,
                        track=track,
                        worst_bucket=worst_key,
                        best_bucket=best_key,
                        n_voicemail=worst["n_voicemail"],
                        n_worst=worst["n_calls"],
                        n_best_answered=best["n_answered"],
                        n_best=best["n_calls"],
                    )
                )
        if n_terminal >= 3 and (card["answer_rate"] or 0) < 0.4:
            modes = ", ".join(f"{FAILURE_LABELS.get(k, k)} {v}" for k, v in card["failure_breakdown"].items())
            recs.append(
                _rec(
                    "low_answer_rate",
                    f"{name} answered {card['n_done']} of {n_terminal} calls ({modes}); do not count on them in a fan-out.",
                    1.5 * (1 - (card["answer_rate"] or 0)) * weight,
                    vendor_key=key,
                    track=track,
                    n_done=card["n_done"],
                    n_terminal=n_terminal,
                    failure_breakdown=card["failure_breakdown"],
                )
            )

    quote = effect.get("quote") or {}
    if (
        (quote.get("n_vendors") or 0) >= 2
        and quote.get("later_lower_by_usd") is not None
        and quote["later_lower_by_usd"] > 0
    ):
        recs.append(
            _rec(
                "context_effect",
                f"Prior context pays on quotes: repeat calls came in {_money(quote['later_lower_by_usd'])} lower than "
                f"first calls across {quote['n_vendors']} vendors ({quote['n_later_calls']} repeat calls).",
                2.5 * math.log1p(quote["n_later_calls"]),
                track="quote",
                **quote,
            )
        )
    neg = effect.get("negotiate") or {}
    if (
        (neg.get("n_vendors") or 0) >= 1
        and neg.get("later_saved_more_usd") is not None
        and neg["later_saved_more_usd"] > 0
    ):
        recs.append(
            _rec(
                "context_effect",
                f"Prior context pays on retention: repeat calls saved {_money(neg['later_saved_more_usd'])}/mo more than "
                f"first calls across {neg['n_vendors']} vendors ({neg['n_later_calls']} repeat calls).",
                2.5 * math.log1p(neg["n_later_calls"]),
                track="negotiate",
                **neg,
            )
        )
    if (
        tod.get("enough_data")
        and tod.get("best_bucket")
        and tod.get("worst_bucket")
        and tod["best_bucket"] != tod["worst_bucket"]
    ):
        by_key = {b["bucket"]: b for b in tod["buckets"]}
        best, worst = by_key[tod["best_bucket"]], by_key[tod["worst_bucket"]]
        if (best["answer_rate"] or 0) - (worst["answer_rate"] or 0) >= 0.2:
            recs.append(
                _rec(
                    "time_of_day_overall",
                    f"Across all vendors calls {best['label']} are answered {best['n_answered']} of {best['n_calls']} times "
                    f"versus {worst['n_answered']} of {worst['n_calls']} {worst['label']}; schedule fan-outs {best['label']}.",
                    1.5
                    * ((best["answer_rate"] or 0) - (worst["answer_rate"] or 0))
                    * math.log1p(tod["n_calls"]),
                    best_bucket=best["bucket"],
                    worst_bucket=worst["bucket"],
                    best=best,
                    worst=worst,
                )
            )
    recs.sort(key=lambda r: (-r["score"], r["kind"], r["vendor_key"] or ""))
    for i, rec in enumerate(recs, start=1):
        rec["rank"] = i
    return recs[:MAX_RECOMMENDATIONS]


# ---------------------------------------------------------------------------------------------
# coverage and the report


def coverage(
    runs: list[dict], calls: list[dict], events: list[dict], *, max_runs: int, events_limit: int
) -> dict:
    starts = [c["_started"] for c in calls if c["_started"]]
    n_calls = len(calls)
    n_simulated = sum(1 for c in calls if c.get("simulated"))
    n_seeded = sum(1 for c in calls if is_seeded_call(c))
    n_synthetic = sum(1 for c in calls if c.get("simulated") or is_seeded_call(c))
    share_synthetic = _rate(n_synthetic, n_calls)
    if n_calls == 0:
        label = "no calls yet"
    elif n_synthetic == n_calls:
        label = "all seeded or simulated demo data"
    elif n_synthetic == 0:
        label = "live calls only"
    else:
        label = f"{round(100 * (share_synthetic or 0))}% seeded or simulated"
    return {
        "n_runs": len(runs),
        "n_runs_seeded": sum(1 for r in runs if is_seeded_run(r)),
        "runs_truncated": len(runs) >= max_runs,
        "max_runs": max_runs,
        "n_calls": n_calls,
        "n_done": sum(1 for c in calls if c["_done"]),
        "n_failed": sum(1 for c in calls if c["_failed"]),
        "n_live": sum(1 for c in calls if not c["_terminal"]),
        "n_simulated": n_simulated,
        "n_seeded": n_seeded,
        "share_simulated": _rate(n_simulated, n_calls),
        "share_seeded": _rate(n_seeded, n_calls),
        "share_synthetic": share_synthetic,
        "label": label,
        "n_events": len(events),
        "n_events_seeded": sum(1 for e in events if is_seeded_event(e)),
        "events_truncated": len(events) >= events_limit,
        "first_call_at": _iso(min(starts)) if starts else None,
        "last_call_at": _iso(max(starts)) if starts else None,
        "vendors": sorted({c["_vendor"] for c in calls}),
    }


def compute_insights(
    store: Any,
    user_id: str,
    *,
    now: datetime | None = None,
    max_runs: int = DEFAULT_MAX_RUNS,
    events_limit: int = EVENTS_LIMIT,
    tz: str | None = None,
    cache_ttl: float | None = None,
) -> dict:
    """The insights report for one user. JSON-serializable, null-safe, never raises on bad rows.

    cache_ttl: when set, a report computed within that many seconds for the same (user, max_runs) is
    returned instead of recomputing. The dispatch path and the insights route share one computation."""
    if cache_ttl:
        key = (user_id, max_runs)
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < cache_ttl:
            return hit[1]
        report = compute_insights(
            store, user_id, now=now, max_runs=max_runs, events_limit=events_limit, tz=tz
        )
        _CACHE[key] = (time.monotonic(), report)
        return report
    now = now or datetime.now(UTC)
    zone = _zone(tz)
    runs, raw_calls, events = load_rows(store, user_id, max_runs=max_runs, events_limit=events_limit)
    calls = _prepare(raw_calls, zone)
    cards = vendor_scorecards(calls)
    effect = context_effect(calls)
    tod = time_of_day(calls, getattr(zone, "key", "UTC"))
    for card in cards.values():
        card.pop("time_of_day_private", None)
    return {
        "generated_at": now.isoformat(),
        "user_id": user_id,
        "coverage": coverage(runs, calls, events, max_runs=max_runs, events_limit=events_limit),
        "vendor_scorecards": cards,
        "track_stats": track_stats(calls),
        "failure_modes": failure_modes(calls),
        "time_of_day": tod,
        "context_effect": effect,
        "recommendations": recommendations(cards, effect, tod),
        "honesty": {
            "source": "postgres rows (runs, calls, events); Keewano is the secondary sink",
            "estimates": ["track_stats.*.avg_multiplier_est uses DIAL_OVERHEAD_SECONDS per done call"],
            "note": "Patterns and context only. Nothing here rewrites prompts or vendor templates.",
        },
    }
