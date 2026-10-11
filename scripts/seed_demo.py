"""Seed labeled demo history so the analysis layer has months of calls to read.

    uv run python scripts/seed_demo.py --user-id <uuid> [--runs 60] [--days 120] [--seed 7] [--roster] [--keewano]
    uv run python scripts/seed_demo.py --user-id <uuid> --wipe

Everything this script writes is tagged so it can never be mistaken for a real call and can be removed
exactly (see wipe()):

- runs.request starts with "[seeded] "
- calls.simulated is true and calls.error is "seeded"; every outcome note says so too
- events.data carries "seeded": true (and "simulated": true on call events)
- ledger.label starts with "[seeded] "
- vendors added by --roster (or when the user has fewer than 5) are is_demo with +1555010xxxx phones

The data is deterministic from --seed and is shaped to show what the analysis layer looks for: one
mover is consistently cheapest, one dentist always confirms, one retention line goes to voicemail in the
morning and concedes more on each repeat call, and later calls to a vendor carry prior_context. Calls go
through store.insert_calls, events through store.insert_event, ledger rows through aggregator.ledger.
Only created_at on runs and events is backdated afterwards (the store sets it to now), because the
timeline has to read as months of history.

Generate and wipe are importable (`generate`, `wipe`, `build_plan`) so tests can run them on a SqliteStore.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings import load_env

load_env()

from aggregator.aggregate import aggregate, parse_ts
from aggregator.ledger import ledger_rows
from analysis.insights import SEEDED_ERROR_TAG, SEEDED_REQUEST_PREFIX, compute_insights
from call.simulate import SIMULATED_NOTE, SPOKEN_DISCLOSURE, fixture_outcome, fixture_transcript
from call.state import STATE_RANK
from router import plan as router_plan
from server.store import SqliteStore

TRACKS = ("quote", "schedule", "negotiate")
DEMO_PHONE_PREFIX = "+1555010"
SEEDED_NOTE = f"{SIMULATED_NOTE}; seeded demo data"
SEEDED_LABEL_PREFIX = SEEDED_REQUEST_PREFIX
MIN_VENDORS_BEFORE_ROSTER = 5
CONCURRENT_CALLS = 3  # fan-out width the timeline assumes (simulated rows are not bound by the Dial cap)
LOCAL_TZ = "America/Los_Angeles"
HOUR_WEIGHTS = {8: 0.4, 9: 1.0, 10: 1.2, 11: 1.0, 12: 0.6, 13: 0.8, 14: 1.3, 15: 1.3, 16: 1.1, 17: 0.6, 18: 0.3}
TALK_RANGE = {"quote": (60, 150), "schedule": (45, 110), "negotiate": (90, 190)}
DIAL_AND_RING_SECONDS = 11
DEFAULT_FAIL_MIX = {"voicemail": 0.5, "no-answer": 0.3, "busy": 0.2}
KEEWANO_BATCH = 100
DEFAULT_SUPABASE_WORKERS = 1  # sequential by default: the shared PostgREST client drops connections under threads

ROSTER: list[dict[str, str]] = [
    {"key": "movers_a", "name": "BearHug Moving", "track": "quote"},
    {"key": "movers_b", "name": "Golden Gate Movers", "track": "quote"},
    {"key": "movers_c", "name": "Bay Haul Co.", "track": "quote"},
    {"key": "movers_d", "name": "Pack & Go Express", "track": "quote"},
    {"key": "storage_quote", "name": "SafeBox Storage", "track": "quote"},
    {"key": "storage_quote_b", "name": "CubeStash Storage", "track": "quote"},
    {"key": "insurance_quote", "name": "Beacon Auto Insurance", "track": "quote"},
    {"key": "dentist", "name": "Dr. Patel Dental", "track": "schedule"},
    {"key": "dermatologist", "name": "Mission Dermatology", "track": "schedule"},
    {"key": "internet_retention", "name": "Comcast Retention", "track": "negotiate"},
    {"key": "phone_retention", "name": "Verizon Retention", "track": "negotiate"},
    {"key": "gym_cancel", "name": "FitLife Gym Billing", "track": "negotiate"},
]
ROSTER_KEYS = [v["key"] for v in ROSTER]

# Per-vendor behaviour. price multiplies the fixture quote (900-2100); unit overrides the fixture unit;
# answer is the pickup probability (answer_morning before 11am local); fail_mix shapes the failures;
# confirm/reduce/cancel are outcome probabilities; repeat_bonus is extra $/mo per earlier done call.
BIAS: dict[str, dict[str, Any]] = {
    "movers_a": {"price": 0.80, "answer": 0.97},
    "movers_b": {"price": 1.00, "answer": 0.85},
    "movers_c": {"price": 1.10, "answer": 0.70, "fail_mix": {"busy": 0.5, "voicemail": 0.3, "no-answer": 0.2}},
    "movers_d": {"price": 0.95, "answer": 0.80},
    "storage_quote": {"price": 0.11, "unit": "per month", "answer": 0.90},
    "storage_quote_b": {"price": 0.13, "unit": "per month", "answer": 0.80},
    "insurance_quote": {"price": 0.12, "unit": "per month", "answer": 0.85},
    "dentist": {"answer": 0.95, "confirm": 1.0},
    "dermatologist": {"answer": 0.80, "confirm": 0.55},
    "internet_retention": {"answer": 0.92, "answer_morning": 0.30, "reduce": 0.78, "repeat_bonus": 7.0},
    "phone_retention": {"answer": 0.80, "reduce": 0.60},
    "gym_cancel": {"answer": 0.85, "cancel": 0.60, "reduce": 0.10, "membership_usd": 45.0},
}
POSITIONAL_BIAS: dict[str, list[dict[str, Any]]] = {
    "quote": [{"price": 0.82, "answer": 0.95}, {"price": 1.0, "answer": 0.85}, {"price": 1.08, "answer": 0.75}],
    "schedule": [{"answer": 0.95, "confirm": 1.0}, {"answer": 0.8, "confirm": 0.6}],
    "negotiate": [{"answer": 0.92, "answer_morning": 0.3, "reduce": 0.78, "repeat_bonus": 7.0}, {"answer": 0.8, "reduce": 0.6}],
}
DEFAULT_BIAS: dict[str, Any] = {"price": 1.0, "answer": 0.85, "confirm": 0.8, "reduce": 0.6}

SCENARIOS: list[dict[str, Any]] = [
    {
        "request": "I'm moving to SF next month, handle it",
        "weight": 4,
        "tracks": {"quote": (2, 4), "negotiate": (0, 1), "schedule": (0, 1)},
        "prefer": {"quote": ["movers_a", "movers_b", "movers_c", "movers_d"], "negotiate": ["internet_retention"]},
    },
    {"request": "Get my internet bill down", "weight": 3, "tracks": {"negotiate": (1, 2)}, "prefer": {"negotiate": ["internet_retention", "phone_retention"]}},
    {"request": "Book a dentist appointment next week", "weight": 3, "tracks": {"schedule": (1, 2)}, "prefer": {"schedule": ["dentist", "dermatologist"]}},
    {"request": "Cancel the gym membership", "weight": 1, "tracks": {"negotiate": (1, 1)}, "prefer": {"negotiate": ["gym_cancel"]}},
    {"request": "Quotes for a storage unit", "weight": 1, "tracks": {"quote": (2, 2)}, "prefer": {"quote": ["storage_quote", "storage_quote_b"]}},
    {"request": "Lower my phone bill", "weight": 1, "tracks": {"negotiate": (1, 1)}, "prefer": {"negotiate": ["phone_retention"]}},
    {
        "request": "Get me three moving quotes for a two-bedroom",
        "weight": 3,
        "tracks": {"quote": (3, 4)},
        "prefer": {"quote": ["movers_a", "movers_b", "movers_c", "movers_d"]},
    },
    {"request": "Shop my car insurance", "weight": 1, "tracks": {"quote": (1, 1)}, "prefer": {"quote": ["insurance_quote"]}},
    {"request": "Book a dermatologist visit", "weight": 1, "tracks": {"schedule": (1, 1)}, "prefer": {"schedule": ["dermatologist"]}},
]


# ---------------------------------------------------------------------------------------------
# helpers


def _money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _is_sqlite(store: Any) -> bool:
    return isinstance(store, SqliteStore) or getattr(store, "name", None) == "sqlite"


def _retry(fn: Any, *args: Any, attempts: int = 4, **kwargs: Any) -> Any:
    """Call fn, retrying transport hiccups (PostgREST keep-alive drops) with a short backoff."""
    import httpx

    delay = 0.5
    for attempt in range(1, attempts + 1):
        try:
            return fn(*args, **kwargs)
        except (httpx.TransportError, ConnectionError) as exc:
            if attempt == attempts:
                raise
            print(f"  retry {attempt}/{attempts - 1} after {type(exc).__name__}: {exc}")
            time.sleep(delay)
            delay *= 2
    return None


def _once(attempt: Any, verify: Any, *, attempts: int = 4) -> Any:
    """Run a non-idempotent write. On a transport drop, ask `verify` whether it landed before re-sending."""
    import httpx

    delay = 0.5
    for i in range(1, attempts + 1):
        try:
            return attempt()
        except (httpx.TransportError, ConnectionError) as exc:
            print(f"  retry {i}/{attempts - 1} after {type(exc).__name__}: {exc}")
            time.sleep(delay)
            delay *= 2
            try:
                existing = verify()
            except Exception:  # noqa: BLE001
                existing = None
            if existing:
                return existing
            if i == attempts:
                raise
    return None


def _find_recent_run(store: Any, user_id: str, request: str) -> dict | None:
    if _is_sqlite(store):
        return None
    rows = (
        store.client.table("runs")
        .select("*")
        .eq("user_id", user_id)
        .eq("request", request)
        .eq("status", "dispatching")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
        or []
    )
    if not rows:
        return None
    created = parse_ts(rows[0].get("created_at"))
    return rows[0] if created and (datetime.now(UTC) - created) < timedelta(minutes=5) else None


def _find_event(store: Any, user_id: str, run_id: str, type_: str, at_iso: str, call_id: str | None) -> dict | None:
    if _is_sqlite(store):
        return None
    q = store.client.table("events").select("*").eq("user_id", user_id).eq("run_id", run_id).eq("type", type_)
    q = q.eq("data->>at", at_iso)
    q = q.eq("call_id", call_id) if call_id else q.is_("call_id", "null")
    rows = q.limit(1).execute().data or []
    return rows[0] if rows else None


def _insert_event_once(store: Any, user_id: str, run_id: str, type_: str, data: dict, call_id: str | None) -> dict | None:
    at_iso = str(data.get("at") or "")
    return _once(
        lambda: store.insert_event(user_id, type_, data, run_id=run_id, call_id=call_id),
        lambda: _find_event(store, user_id, run_id, type_, at_iso, call_id),
    )


def _find_ledger(store: Any, call_ids: list[str], expected: int) -> list[dict] | None:
    if _is_sqlite(store) or not call_ids:
        return None
    rows = store.client.table("ledger").select("*").in_("source_call", call_ids).like("label", f"{SEEDED_LABEL_PREFIX}%").execute().data or []
    return rows if len(rows) == expected else None


def _bias_for(vendor: dict, index_in_track: int) -> dict[str, Any]:
    key = str(vendor.get("key") or "")
    if key in BIAS:
        return {**DEFAULT_BIAS, **BIAS[key]}
    positional = POSITIONAL_BIAS.get(str(vendor.get("track")), [])
    if index_in_track < len(positional):
        return {**DEFAULT_BIAS, **positional[index_in_track]}
    return dict(DEFAULT_BIAS)


def _weighted(rng: random.Random, weights: dict[Any, float]) -> Any:
    items = list(weights.items())
    total = sum(w for _k, w in items)
    roll = rng.random() * total
    acc = 0.0
    for key, weight in items:
        acc += weight
        if roll <= acc:
            return key
    return items[-1][0]


def _run_times(rng: random.Random, runs: int, days: int, now: datetime, zone: ZoneInfo) -> list[datetime]:
    """`runs` run start times over the last `days` days, business hours in the user's zone, ascending."""
    if runs <= 0:
        return []
    local_now = now.astimezone(zone)
    offsets = sorted(rng.uniform(0.3, max(1.0, float(days))) for _ in range(runs))
    times: list[datetime] = []
    for offset in offsets:
        day = (local_now - timedelta(days=offset)).date()
        hour = _weighted(rng, HOUR_WEIGHTS)
        minute, second = rng.randint(0, 59), rng.randint(0, 59)
        local = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=zone)
        if local >= local_now:
            local = local_now - timedelta(minutes=rng.randint(30, 180))
        times.append(local.astimezone(UTC))
    return sorted(times)


def _scenario_fits(scenario: dict, by_track: dict[str, list[dict]]) -> bool:
    """Every required track has vendors, and where the scenario names vendors at least one is on the roster."""
    for track, (lo, _hi) in scenario["tracks"].items():
        if lo < 1:
            continue
        pool = by_track.get(track) or []
        if not pool:
            return False
        preferred = (scenario.get("prefer") or {}).get(track)
        if preferred and not any(v["key"] in preferred for v in pool):
            return False
    return True


def _pick_scenario(rng: random.Random, by_track: dict[str, list[dict]]) -> dict:
    usable = [s for s in SCENARIOS if _scenario_fits(s, by_track)]
    if not usable:
        usable = [s for s in SCENARIOS if any(by_track.get(t) for t in s["tracks"])] or [SCENARIOS[0]]
    return usable[_weighted(rng, {i: s["weight"] for i, s in enumerate(usable)})]


def _pick_vendors(rng: random.Random, scenario: dict, by_track: dict[str, list[dict]]) -> list[dict]:
    chosen: list[dict] = []
    for track, (lo, hi) in scenario["tracks"].items():
        pool = by_track.get(track) or []
        if not pool:
            continue
        preferred = [v for v in pool if v["key"] in (scenario.get("prefer") or {}).get(track, [])]
        candidates = preferred or pool
        k = min(len(candidates), rng.randint(lo, hi))
        chosen.extend(rng.sample(candidates, k))
    if len(chosen) < 2:
        rest = [v for t in TRACKS for v in by_track.get(t, []) if v not in chosen]
        rng.shuffle(rest)
        chosen.extend(rest[: 2 - len(chosen)])
    return chosen[:5]


# ---------------------------------------------------------------------------------------------
# outcome + transcript shaping (fixture values with vendor biases; the text always matches the outcome)


def _outcome(track: str, bias: dict, rng: random.Random, seed: str, today: date, n_prior_done: int, with_context: bool) -> dict:
    fx = fixture_outcome(track, seed, today=today)
    if track == "quote":
        amount = fx["amount_usd"] * bias.get("price", 1.0) * rng.uniform(0.97, 1.03)
        if with_context:
            amount *= bias.get("context_discount", 0.94)
        step = 5 if amount < 400 else 10
        amount = float(max(step, round(amount / step) * step))
        return {"track": "quote", "amount_usd": amount, "unit": bias.get("unit", fx["unit"]), "caveats": fx["caveats"], "note": SEEDED_NOTE}
    if track == "schedule":
        return {"track": "schedule", "slot_iso": fx["slot_iso"], "confirmed": rng.random() < bias.get("confirm", 0.8), "note": SEEDED_NOTE}
    roll = rng.random()
    cancel_p, reduce_p = bias.get("cancel", 0.0), bias.get("reduce", 0.6)
    if roll < cancel_p:
        action, saved = "canceled", float(bias.get("membership_usd", 45.0))
    elif roll < cancel_p + reduce_p:
        action = "reduced"
        saved = float(fx["saved_usd"]) + min(bias.get("repeat_bonus", 0.0) * n_prior_done, 25.0)
        if with_context:
            saved += bias.get("context_bonus", 3.0)
        saved = float(round(saved))
    else:
        action, saved = "refused", None
    return {
        "track": "negotiate",
        "action": action,
        "saved_usd": saved,
        "effective_date": fx["effective_date"] if action != "refused" else None,
        "note": SEEDED_NOTE,
    }


def _transcript(track: str, name: str, seed: str, outcome: dict, today: date) -> list[dict]:
    turns = [dict(t) for t in fixture_transcript(track, name, seed, today=today)]
    fx = fixture_outcome(track, seed, today=today)
    if track == "quote":
        old, new = f"{_money(fx['amount_usd'])} {fx['unit']}", f"{_money(outcome['amount_usd'])} {outcome['unit']}"
        for t in turns:
            t["text"] = t["text"].replace(old, new)
    elif track == "negotiate":
        if outcome["action"] == "reduced":
            old, new = _money(fx["saved_usd"]), _money(outcome["saved_usd"])
            for t in turns:
                t["text"] = t["text"].replace(old, new)
        elif outcome["action"] == "canceled":
            effective = date.fromisoformat(outcome["effective_date"]).strftime("%B %d")
            saved = _money(outcome["saved_usd"])
            turns = turns[:5] + [
                {"speaker": "user", "text": f"I can cancel the membership effective {effective}; the {saved} monthly charge stops then."},
                {"speaker": "agent", "text": "Please do, and send a confirmation email."},
                {"speaker": "user", "text": "Done, the cancellation is processed."},
                {"speaker": "agent", "text": f"Just to confirm, {name} canceled the membership effective {effective}, saving {saved} a month. Thanks, bye."},
            ]
        else:
            turns = turns[:5] + [
                {"speaker": "user", "text": "I'm not seeing any promotions I can apply to this account right now."},
                {"speaker": "agent", "text": "Is there a retention department or a cheaper plan that would fit?"},
                {"speaker": "user", "text": "The plan you're on is the lowest tier we offer."},
                {"speaker": "agent", "text": f"Just to confirm, {name} is not able to lower the bill today. Thanks, bye."},
            ]
    elif track == "schedule" and not outcome.get("confirmed"):
        spoken = datetime.fromisoformat(outcome["slot_iso"]).strftime("%A %B %d at %-I:%M %p")
        turns = turns[:7] + [
            {"speaker": "user", "text": "I can pencil that in, but the front desk has to confirm tomorrow."},
            {"speaker": "agent", "text": f"Understood, please hold it. Just to confirm, {spoken} with {name}, pending confirmation. Thanks, bye."},
        ]
    return turns


def _voicemail_turns(name: str) -> list[dict]:
    return [
        {"speaker": "agent", "text": f"{SPOKEN_DISCLOSURE} {name or 'there'}?"},
        {"speaker": "agent", "text": "It sounds like I've reached voicemail. I'll try again later. This is a simulated call."},
    ]


def _timed(turns: list[dict], talk_seconds: float) -> list[dict]:
    step_ms = int(talk_seconds * 1000 / max(1, len(turns)))
    return [{**t, "startMs": i * step_ms, "endMs": (i + 1) * step_ms - 200} for i, t in enumerate(turns)]


# ---------------------------------------------------------------------------------------------
# plan (pure)


def _call_spec(
    rng: random.Random,
    vendor: dict,
    bias: dict,
    start: datetime,
    zone: ZoneInfo,
    seed: str,
    history: list[dict],
) -> dict:
    track = vendor["track"]
    name = vendor["name"]
    prior_done = [c for c in history if c["state"] == "done" and c.get("outcome")]
    prior_context = None
    if prior_done:
        try:
            prior_context = router_plan._sentence(prior_done[-1], prior_done[-1]["outcome"], track)
        except Exception:  # noqa: BLE001
            prior_context = None
    hour_local = start.astimezone(zone).hour
    answer_p = bias.get("answer_morning", bias["answer"]) if hour_local < 11 else bias["answer"]
    objective = router_plan.default_objective(vendor, "seeded demo request")
    base = {
        "vendor_id": vendor.get("id"),
        "vendor_key": vendor["key"],
        "track": track,
        "target_name": name,
        "target_phone": vendor.get("phone") or f"{DEMO_PHONE_PREFIX}0000",
        "objective": objective,
        "prior_context": prior_context,
        "dial_call_id": None,
        "simulated": True,
        "error": SEEDED_ERROR_TAG,
        "started_at": _iso(start),
    }
    if rng.random() < answer_p:
        talk = rng.randint(*TALK_RANGE[track])
        ended = start + timedelta(seconds=DIAL_AND_RING_SECONDS + talk)
        outcome = _outcome(track, bias, rng, seed, ended.date(), len(prior_done), prior_context is not None)
        turns = _transcript(track, name, seed, outcome, ended.date())
        return {
            **base,
            "state": "done",
            "state_rank": STATE_RANK["done"],
            "termination_type": "completed",
            "transcript": "\n".join(f"{t['speaker']}: {t['text']}" for t in turns),
            "transcript_turns": _timed(turns, talk),
            "outcome": outcome,
            "duration_seconds": float(talk),
            "ended_at": _iso(ended),
        }
    kind = _weighted(rng, bias.get("fail_mix", DEFAULT_FAIL_MIX))
    if kind == "voicemail":
        duration: float | None = float(rng.randint(18, 38))
        ended = start + timedelta(seconds=DIAL_AND_RING_SECONDS + duration)
        turns = _voicemail_turns(name)
        transcript = "\n".join(f"{t['speaker']}: {t['text']}" for t in turns)
        timed = _timed(turns, duration)
    else:
        duration, transcript, timed = None, None, None
        ended = start + timedelta(seconds=32 if kind == "no-answer" else 6)
    return {
        **base,
        "state": "failed",
        "state_rank": STATE_RANK["failed"],
        "termination_type": kind,
        "transcript": transcript,
        "transcript_turns": timed,
        "outcome": None,
        "duration_seconds": duration,
        "ended_at": _iso(ended),
    }


def build_plan(vendors: list[dict], user_id: str, runs: int, days: int, seed: int, *, now: datetime | None = None) -> dict:
    """Deterministic in-memory plan: runs with fully shaped call rows. Pure; no store access."""
    now = now or datetime.now(UTC)
    zone = ZoneInfo(LOCAL_TZ)
    rng = random.Random(f"seed-demo:{seed}")
    by_track = {t: sorted([v for v in vendors if v.get("track") == t and v.get("key")], key=lambda v: v["key"]) for t in TRACKS}
    biases = {v["key"]: _bias_for(v, i) for t in TRACKS for i, v in enumerate(by_track[t])}
    history: dict[str, list[dict]] = {}
    run_specs: list[dict] = []
    if not any(by_track.values()):
        return {"user_id": user_id, "seed": seed, "runs": [], "n_calls": 0}
    for index, t0 in enumerate(_run_times(rng, runs, days, now, zone)):
        scenario = _pick_scenario(rng, by_track)
        chosen = _pick_vendors(rng, scenario, by_track)
        if not chosen:
            continue
        dispatch_at = t0 + timedelta(seconds=rng.uniform(1.5, 4.0))
        slots = [dispatch_at] * CONCURRENT_CALLS
        calls: list[dict] = []
        for call_index, vendor in enumerate(chosen):
            earliest = min(slots)
            start = max(dispatch_at + timedelta(seconds=rng.uniform(0.0, 3.0)), earliest)
            spec = _call_spec(rng, vendor, biases[vendor["key"]], start, zone, f"seed:{seed}:{index}:{call_index}", history.get(vendor["key"], []))
            slots[slots.index(earliest)] = parse_ts(spec["ended_at"]) + timedelta(seconds=2)  # type: ignore[operator]
            calls.append(spec)
            history.setdefault(vendor["key"], []).append(spec)
        completed = max(parse_ts(c["ended_at"]) for c in calls) + timedelta(seconds=3)  # type: ignore[type-var]
        run_specs.append(
            {
                "index": index,
                "request": f"{SEEDED_REQUEST_PREFIX}{scenario['request']}",
                "created_at": _iso(t0),
                "completed_at": _iso(completed),
                "calls": calls,
            }
        )
    return {"user_id": user_id, "seed": seed, "runs": run_specs, "n_calls": sum(len(r["calls"]) for r in run_specs)}


# ---------------------------------------------------------------------------------------------
# write


def _call_events(row: dict) -> list[tuple[str, dict, datetime]]:
    start = parse_ts(row.get("started_at")) or datetime.now(UTC)
    end = parse_ts(row.get("ended_at")) or start
    base = {"seeded": True, "simulated": True, "track": row.get("track"), "vendor_key": row.get("vendor_key")}
    events: list[tuple[str, dict, datetime]] = [
        ("call_dispatched", {**base, "dial_call_id": None}, start - timedelta(seconds=1)),
        ("call_simulated", {**base, "reason": "seeded demo data", "limit": False}, start),
    ]
    voicemail = row.get("termination_type") == "voicemail"
    if row.get("state") == "done" or voicemail:
        events.append(("call_connected", {**base, "dial_call_id": None}, start + timedelta(seconds=DIAL_AND_RING_SECONDS)))
    events.append(
        (
            "call_terminated",
            {
                **base,
                "dial_call_id": None,
                "state": row.get("state"),
                "termination_type": row.get("termination_type"),
                "duration_seconds": row.get("duration_seconds"),
                "voicemail": voicemail,
            },
            end,
        )
    )
    if row.get("state") == "done":
        events.append(("outcome_extracted", {**base, "outcome": row.get("outcome")}, end + timedelta(seconds=2)))
    return events


def _match_rows(specs: list[dict], inserted: list[dict]) -> list[dict]:
    """Pair inserted call rows with their specs (Supabase assigns ids). vendor_key is unique within a run."""
    by_key = {r.get("vendor_key"): r for r in inserted if isinstance(r, dict)}
    out = []
    for i, spec in enumerate(specs):
        row = by_key.get(spec["vendor_key"]) or (inserted[i] if i < len(inserted) else None)
        if row is None:
            continue
        out.append({**spec, **row})
    return out


def _finalize_run(store: Any, run_id: str, created_at: str, completed_at: str) -> None:
    fields = {"status": "complete", "created_at": created_at, "completed_at": completed_at}
    if _is_sqlite(store):
        store._update("runs", fields, "id = ?", [run_id])
        return
    _retry(lambda: store.client.table("runs").update(fields).eq("id", run_id).execute())


def _backdate_events(store: Any, rows: list[dict]) -> None:
    """Set events.created_at to the row's `created_at` (already replaced with the believable time)."""
    if not rows:
        return
    if _is_sqlite(store):
        with store._lock, store._conn:
            store._conn.executemany(
                "update events set created_at = ? where id = ?", [(r["created_at"], r["id"]) for r in rows]
            )
        return
    payload = [
        {k: r.get(k) for k in ("id", "user_id", "run_id", "call_id", "type", "data", "created_at")} for r in rows
    ]
    _retry(lambda: store.client.table("events").upsert(payload, on_conflict="id").execute())


def _write_run(store: Any, user_id: str, spec: dict) -> dict:
    run = _once(lambda: store.create_run(user_id, spec["request"]), lambda: _find_recent_run(store, user_id, spec["request"]))
    run_id = run["id"]
    payload = [{k: v for k, v in c.items() if not k.startswith("_")} | {"run_id": run_id} for c in spec["calls"]]

    def calls_landed() -> list[dict] | None:
        rows = store.list_calls(run_id)
        return rows if len(rows) == len(payload) else None

    inserted = _once(lambda: store.insert_calls(payload), calls_landed)
    rows = _match_rows(spec["calls"], inserted)
    created_at, completed_at = spec["created_at"], spec["completed_at"]
    completed_dt = parse_ts(completed_at) or datetime.now(UTC)

    timeline: list[tuple[str, dict, datetime, str | None]] = [
        (
            "run_requested",
            {"seeded": True, "request": spec["request"], "n_calls": len(rows), "vendor_keys": [r["vendor_key"] for r in rows]},
            parse_ts(created_at) or completed_dt,
            None,
        )
    ]
    for row in rows:
        timeline.extend((t, d, at, row["id"]) for t, d, at in _call_events(row))
    agg = aggregate({"calls": rows})
    summary_keys = (
        "n_calls",
        "n_completed",
        "n_failed",
        "n_simulated",
        "n_live",
        "wall_clock_seconds",
        "talk_seconds",
        "sequential_estimate_seconds",
        "multiplier",
    )
    timeline.append(("run_aggregated", {"seeded": True, **{k: agg.get(k) for k in summary_keys}}, completed_dt, None))

    ledger: list[dict] = []
    for row in rows:
        if row.get("state") != "done" or not row.get("outcome"):
            continue
        ended = parse_ts(row.get("ended_at"))
        for item in ledger_rows(row, today=ended.date() if ended else None):
            ledger.append({**item, "user_id": user_id, "label": f"{SEEDED_LABEL_PREFIX}{item['label']}"})
    call_ids = [r["id"] for r in rows]
    inserted_ledger = (
        _once(lambda: store.insert_ledger(ledger), lambda: _find_ledger(store, call_ids, len(ledger))) if ledger else []
    )
    timeline.append(
        (
            "ledger_written",
            {"seeded": True, "n": len(inserted_ledger), "kinds": [r.get("kind") for r in inserted_ledger], "ledger_ids": [r.get("id") for r in inserted_ledger]},
            completed_dt + timedelta(seconds=1),
            None,
        )
    )

    timeline.sort(key=lambda item: item[2])
    event_rows: list[dict] = []
    for type_, data, at, call_id in timeline:
        row = _insert_event_once(store, user_id, run_id, type_, {**data, "at": _iso(at)}, call_id)
        if isinstance(row, dict) and row.get("id") is not None:
            event_rows.append({**row, "created_at": _iso(at)})
    _backdate_events(store, event_rows)
    _finalize_run(store, run_id, created_at, completed_at)
    return {
        "run_id": run_id,
        "n_calls": len(rows),
        "n_done": sum(1 for r in rows if r.get("state") == "done"),
        "n_failed": sum(1 for r in rows if r.get("state") == "failed"),
        "n_events": len(event_rows),
        "n_ledger": len(inserted_ledger),
        "events": event_rows,
    }


def ensure_roster(store: Any, user_id: str, *, force: bool = False) -> tuple[list[dict], list[str]]:
    """Add the demo roster when the user has fewer than 5 vendors (or `force`). Existing keys are never touched."""
    existing = [v for v in (store.list_vendors(user_id) or []) if v.get("key")]
    if len(existing) >= MIN_VENDORS_BEFORE_ROSTER and not force:
        return existing, []
    have = {v["key"] for v in existing}
    to_add = [
        {**v, "phone": f"{DEMO_PHONE_PREFIX}{i + 1:04d}", "is_demo": True}
        for i, v in enumerate(ROSTER)
        if v["key"] not in have
    ]
    if to_add:
        store.upsert_vendors(user_id, to_add)
    return [v for v in (store.list_vendors(user_id) or []) if v.get("key")], [v["key"] for v in to_add]


def generate(
    store: Any,
    user_id: str,
    runs: int,
    days: int,
    seed: int,
    *,
    add_roster: bool = False,
    now: datetime | None = None,
    workers: int | None = None,
    progress: Any = None,
) -> dict:
    """Seed `runs` labeled runs over the last `days` days for `user_id`. Returns a summary with counts."""
    if store.get_profile(user_id) is None:
        if _is_sqlite(store):
            store.upsert_profile(user_id, {"display_name": "demo"})
        else:
            raise SystemExit(f"no profile for {user_id}; create the user first (profiles.id references auth.users)")
    vendors, added = ensure_roster(store, user_id, force=add_roster)
    plan = build_plan(vendors, user_id, runs, days, seed, now=now)
    workers = workers or (1 if _is_sqlite(store) else DEFAULT_SUPABASE_WORKERS)
    results: list[dict] = []
    if workers <= 1:
        for i, spec in enumerate(plan["runs"], start=1):
            results.append(_write_run(store, user_id, spec))
            if progress and i % 10 == 0:
                progress(f"  wrote {i}/{len(plan['runs'])} runs")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i, result in enumerate(pool.map(lambda s: _write_run(store, user_id, s), plan["runs"]), start=1):
                results.append(result)
                if progress and i % 10 == 0:
                    progress(f"  wrote {i}/{len(plan['runs'])} runs")
    events = [e for r in results for e in r["events"]]
    created = [r["created_at"] for r in plan["runs"]]
    return {
        "store": getattr(store, "name", type(store).__name__),
        "user_id": user_id,
        "seed": seed,
        "vendors_total": len(vendors),
        "vendors_added": added,
        "n_runs": len(results),
        "n_calls": sum(r["n_calls"] for r in results),
        "n_done": sum(r["n_done"] for r in results),
        "n_failed": sum(r["n_failed"] for r in results),
        "n_events": len(events),
        "n_ledger": sum(r["n_ledger"] for r in results),
        "first_run_at": min(created) if created else None,
        "last_run_at": max(created) if created else None,
        "run_ids": [r["run_id"] for r in results],
        "events": events,
    }


# ---------------------------------------------------------------------------------------------
# wipe


def wipe(store: Any, user_id: str) -> dict[str, int]:
    """Delete only rows this script tagged. Returns per-table counts."""
    counts: dict[str, int] = {}
    like = f"{SEEDED_REQUEST_PREFIX}%"
    if _is_sqlite(store):
        marks = ",".join("?" for _ in ROSTER_KEYS)
        statements = [
            ("ledger", f"delete from ledger where user_id = ? and label like '{SEEDED_LABEL_PREFIX}%'", [user_id]),
            ("events", "delete from events where user_id = ? and json_extract(data, '$.seeded') = 1", [user_id]),
            (
                "calls",
                (
                    "delete from calls where simulated = 1 and error = ? and run_id in "
                    "(select id from runs where user_id = ? and request like ?)"
                ),
                [SEEDED_ERROR_TAG, user_id, like],
            ),
            ("runs", "delete from runs where user_id = ? and request like ?", [user_id, like]),
            (
                "vendors",
                (
                    f"delete from vendors where owner_id = ? and is_demo = 1 and phone like '{DEMO_PHONE_PREFIX}%' "
                    f"and key in ({marks})"
                ),
                [user_id, *ROSTER_KEYS],
            ),
        ]
        with store._lock, store._conn:
            for table, sql, params in statements:
                counts[table] = store._conn.execute(sql, params).rowcount
        return counts

    table = store.client.table

    def n(resp: Any) -> int:
        return len(resp.data) if isinstance(getattr(resp, "data", None), list) else 0

    counts["ledger"] = n(table("ledger").delete().eq("user_id", user_id).like("label", f"{SEEDED_LABEL_PREFIX}%").execute())
    counts["events"] = n(table("events").delete().eq("user_id", user_id).eq("data->>seeded", "true").execute())
    run_ids = [r["id"] for r in table("runs").select("id").eq("user_id", user_id).like("request", like).execute().data or []]
    counts["calls"] = 0
    for i in range(0, len(run_ids), 50):
        chunk = run_ids[i : i + 50]
        counts["calls"] += n(
            table("calls").delete().in_("run_id", chunk).eq("simulated", True).eq("error", SEEDED_ERROR_TAG).execute()
        )
    counts["runs"] = n(table("runs").delete().eq("user_id", user_id).like("request", like).execute())
    counts["vendors"] = n(
        table("vendors")
        .delete()
        .eq("owner_id", user_id)
        .eq("is_demo", True)
        .like("phone", f"{DEMO_PHONE_PREFIX}%")
        .in_("key", ROSTER_KEYS)
        .execute()
    )
    return counts


# ---------------------------------------------------------------------------------------------
# keewano side


async def send_to_keewano(user_id: str, events: list[dict], *, batch_size: int = KEEWANO_BATCH, log: Any = print) -> dict:
    """Ship the seeded event rows through events.keewano.send_events in batches. Reports, never raises."""
    from events import keewano

    ordered = sorted(events, key=lambda e: str(e.get("created_at") or ""))
    ok = 0
    batches = 0
    for i in range(0, len(ordered), batch_size):
        batch = ordered[i : i + batch_size]
        batches += 1
        try:
            sent = await keewano.send_events(user_id, batch)
        except Exception as exc:  # noqa: BLE001
            sent = False
            log(f"  keewano batch {batches}: {type(exc).__name__}: {exc}")
        ok += int(bool(sent))
        log(f"  keewano batch {batches}: {len(batch)} events -> {'ok' if sent else 'rejected'}")
    return {"batches": batches, "ok": ok, "events": len(ordered), "fallback_builtin": keewano._use_builtin}


# ---------------------------------------------------------------------------------------------
# cli


def _print_summary(store: Any, summary: dict, keewano_result: dict | None) -> None:
    print("== seed_demo summary ==")
    print(f"store: {summary['store']}   user: {summary['user_id']}   seed: {summary['seed']}")
    added = ", ".join(summary["vendors_added"]) or "none"
    print(f"vendors: {summary['vendors_total']} total, added: {added}")
    first = (summary["first_run_at"] or "")[:10]
    last = (summary["last_run_at"] or "")[:10]
    print(
        f"runs: {summary['n_runs']} ({first} .. {last})   calls: {summary['n_calls']} "
        f"(done {summary['n_done']}, failed {summary['n_failed']})   events: {summary['n_events']}   "
        f"ledger rows: {summary['n_ledger']}"
    )
    if keewano_result is None:
        print("keewano: not sent (pass --keewano)")
    else:
        tail = " via built-in fallback id" if keewano_result.get("fallback_builtin") else ""
        print(f"keewano: {keewano_result['ok']} of {keewano_result['batches']} batches accepted ({keewano_result['events']} events){tail}")
    try:
        insights = compute_insights(store, summary["user_id"])
    except Exception as exc:  # noqa: BLE001
        print(f"insights: failed ({type(exc).__name__}: {exc})")
        return
    cov = insights["coverage"]
    print(f"coverage: {cov['n_runs']} runs, {cov['n_calls']} calls, label: {cov['label']}")
    print("top recommendations:")
    for rec in insights["recommendations"][:3]:
        print(f"  {rec['rank']}. {rec['text']}")
    if not insights["recommendations"]:
        print("  (none yet)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed labeled demo runs, calls, events and ledger rows.")
    parser.add_argument("--user-id", required=True, help="profiles.id to seed (must exist on Supabase)")
    parser.add_argument("--runs", type=int, default=60)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--roster", action="store_true", help="add the 12-vendor demo roster even if the user has 5+ vendors")
    parser.add_argument("--wipe", action="store_true", help="delete rows this script wrote earlier, then exit")
    parser.add_argument("--keewano", action="store_true", help="also send the seeded events to Keewano")
    parser.add_argument("--workers", type=int, default=None, help="parallel runs while writing (default 1 sqlite, 3 supabase)")
    args = parser.parse_args(argv)

    from server.store import get_store

    store = get_store()
    if args.wipe:
        counts = wipe(store, args.user_id)
        print("== seed_demo wipe ==")
        for table, count in counts.items():
            print(f"  {table}: {count} deleted")
        return 0

    print(f"seeding {args.runs} runs over {args.days} days for {args.user_id} on {getattr(store, 'name', 'store')} ...")
    summary = generate(
        store, args.user_id, args.runs, args.days, args.seed, add_roster=args.roster, workers=args.workers, progress=print
    )
    keewano_result = None
    if args.keewano:
        print("sending to keewano ...")
        keewano_result = asyncio.run(send_to_keewano(args.user_id, summary["events"]))
    _print_summary(store, summary, keewano_result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
