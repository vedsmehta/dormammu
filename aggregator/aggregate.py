"""Aggregator: group, rank and time a run's calls. Pure, no I/O (BUILD-PLAN §4.9).

The honesty constraint on the headline number: wall clock is measured (max ended_at - min started_at over
done calls); the sequential figure is an estimate (talk time + DIAL_OVERHEAD_SECONDS per done call), so the
UI labels it "est." and shows dial_overhead_seconds next to it. Failed calls never enter the window.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

TRACKS = ("quote", "schedule", "negotiate")
TERMINAL_STATES = ("done", "failed")


def _overhead_from_env() -> int:
    try:
        return max(0, int(os.environ.get("DIAL_OVERHEAD_SECONDS") or 45))
    except ValueError:
        return 45


DIAL_OVERHEAD_SECONDS: int = _overhead_from_env()


def parse_ts(value: Any) -> datetime | None:
    """ISO string (with or without 'Z') -> aware datetime; naive values are taken as UTC. None if unreadable."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _outcome(call: dict) -> dict:
    outcome = call.get("outcome")
    if isinstance(outcome, str):
        try:
            outcome = json.loads(outcome)
        except ValueError:
            return {}
    return outcome if isinstance(outcome, dict) else {}


def rank_quote(calls: list[dict]) -> list[dict]:
    """Ascending amount_usd, nulls last; best on the cheapest non-null only."""

    def key(call: dict) -> tuple:
        amount = _num(_outcome(call).get("amount_usd"))
        return (amount is None, amount if amount is not None else 0.0)

    ranked = sorted(calls, key=key)
    rows = []
    for i, call in enumerate(ranked):
        best = i == 0 and _num(_outcome(call).get("amount_usd")) is not None
        rows.append({**call, "best": best})
    return rows


def rank_schedule(calls: list[dict]) -> list[dict]:
    """Ascending slot time; confirmed first, then unconfirmed with a slot, then no slot."""

    def parts(call: dict) -> tuple[datetime | None, bool]:
        outcome = _outcome(call)
        return parse_ts(outcome.get("slot_iso")), bool(outcome.get("confirmed"))

    def key(call: dict) -> tuple:
        slot, confirmed = parts(call)
        return (not (slot is not None and confirmed), slot is None, slot.timestamp() if slot else 0.0)

    ranked = sorted(calls, key=key)
    rows = []
    for i, call in enumerate(ranked):
        slot, confirmed = parts(call)
        rows.append({**call, "best": i == 0 and slot is not None and confirmed})
    return rows


def rank_negotiate(calls: list[dict]) -> list[dict]:
    """Descending saved_usd, nulls last; best on the largest positive saving only."""

    def key(call: dict) -> tuple:
        saved = _num(_outcome(call).get("saved_usd"))
        return (saved is None, -(saved if saved is not None else 0.0))

    ranked = sorted(calls, key=key)
    rows = []
    for i, call in enumerate(ranked):
        saved = _num(_outcome(call).get("saved_usd"))
        rows.append({**call, "best": i == 0 and saved is not None and saved > 0})
    return rows


RANKERS = {"quote": rank_quote, "schedule": rank_schedule, "negotiate": rank_negotiate}


def aggregate(run: dict) -> dict:
    """AggregateView for a run dict that carries "calls". Pure; the input is not mutated."""
    calls = [c for c in (run or {}).get("calls") or [] if isinstance(c, dict)]
    by_track: dict[str, list[dict]] = {track: [] for track in TRACKS}
    for call in calls:
        track = call.get("track")
        if track in by_track:
            by_track[track].append(call)
    tracks = {track: RANKERS[track](rows) for track, rows in by_track.items()}

    done = [c for c in calls if c.get("state") == "done"]
    failed = [c for c in calls if c.get("state") == "failed"]
    live = [c for c in calls if c.get("state") not in TERMINAL_STATES]

    starts = [ts for ts in (parse_ts(c.get("started_at")) for c in done) if ts is not None]
    ends = [ts for ts in (parse_ts(c.get("ended_at")) for c in done) if ts is not None]
    wall: float | None = None
    if starts and ends:
        wall = (max(ends) - min(starts)).total_seconds()
        if wall < 0:
            wall = None

    talk = float(sum(_num(c.get("duration_seconds")) or 0.0 for c in done))
    n_done = len(done)
    sequential: float | None = talk + n_done * DIAL_OVERHEAD_SECONDS if n_done else None
    multiplier = sequential / wall if (sequential is not None and wall) else None

    return {
        "tracks": tracks,
        "wall_clock_seconds": wall,
        "talk_seconds": talk,
        "sequential_estimate_seconds": sequential,
        "multiplier": multiplier,
        "dial_overhead_seconds": DIAL_OVERHEAD_SECONDS,
        "n_calls": len(calls),
        "n_completed": n_done,
        "n_failed": len(failed),
        "n_simulated": sum(1 for c in calls if c.get("simulated")),
        "n_live": len(live),
    }
