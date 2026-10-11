"""Labeled simulated rows (BUILD-PLAN §4.13, review 18A).

When the Dial concurrency cap (2 on this account) refuses a call, dispatch marks the row
`simulated: true` and the tick drives it here through the SAME monotonic UPDATE path a real call
uses: dialing (0-4s) -> ringing (4-11s) -> talking (11s .. 11s + talk) -> done with a fixture
transcript and outcome. Everything is deterministic from the call id, so two ticks racing agree.

Simulated rows are labeled in the row (`simulated`), in the transcript (first agent line), in the
outcome note and in every event they emit. Nothing here is ever presented as a real quote.
"""

from __future__ import annotations

import hashlib
import os
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from call.state import STATE_RANK
from events.log import log_event

DIALING_SECONDS = 4
RINGING_UNTIL_SECONDS = 11
DEFAULT_TALK_SECONDS = 75
TALK_JITTER_SECONDS = 20
SIMULATED_NOTE = "simulated call, fixture outcome, not a real vendor quote"
# The spoken opener, mirroring the first words router.instructions.build() requires on a real call, and the
# one-sentence AI line that follows it once the other side has answered.
SPOKEN_DISCLOSURE = "Hi, this is Alex, am I speaking with"
SPOKEN_AI_LINE = "I'm an AI assistant calling for a client. This is a simulated call."

_CAVEATS = [
    "excludes packing materials",
    "stairs or long carry billed extra",
    "deposit required to hold the date",
    "weekend dates add 10 percent",
    "price valid for 7 days",
    "fuel surcharge not included",
]


def _rng(seed: str, salt: str = "") -> random.Random:
    digest = hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def talk_seconds(seed: str) -> int:
    base = int(os.environ.get("SIMULATED_TALK_SECONDS") or DEFAULT_TALK_SECONDS)
    jitter = _rng(seed, "talk").randint(-TALK_JITTER_SECONDS, TALK_JITTER_SECONDS)
    return max(15, base + jitter)


def _next_week_slot(rng: random.Random, today: date) -> datetime:
    # Monday of next week, then a weekday and a business hour, in the local timezone.
    monday = today + timedelta(days=7 - today.weekday())
    day = monday + timedelta(days=rng.randint(0, 4))
    hour = rng.choice([9, 10, 11, 13, 14, 15, 16])
    minute = rng.choice([0, 30])
    # The product's one timezone, not the machine's: on Vercel the machine is UTC, and a slot built there
    # would read 7 hours off against the spoken transcript.
    try:
        tz = ZoneInfo(os.environ.get("DORMAMMU_TZ") or "America/Los_Angeles")
    except Exception:  # noqa: BLE001
        tz = datetime.now().astimezone().tzinfo
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def _first_of_next_month(today: date) -> date:
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, 1)


def fixture_outcome(track: str, seed: str, *, today: date | None = None) -> dict:
    """A plausible, clearly labeled fixture Outcome for the track. Deterministic from seed."""
    rng = _rng(seed, "outcome")
    today = today or date.today()
    if track == "quote":
        amount = float(rng.randrange(900, 2101, 10))
        unit = rng.choice(["flat", "per hour"])
        caveats = rng.sample(_CAVEATS, k=rng.randint(1, 2))
        return {
            "track": "quote",
            "amount_usd": amount,
            "unit": unit,
            "caveats": caveats,
            "note": SIMULATED_NOTE,
        }
    if track == "schedule":
        slot = _next_week_slot(rng, today)
        return {"track": "schedule", "slot_iso": slot.isoformat(), "confirmed": True, "note": SIMULATED_NOTE}
    saved = float(rng.randrange(15, 41))
    return {
        "track": "negotiate",
        "action": "reduced",
        "saved_usd": saved,
        "effective_date": _first_of_next_month(today).isoformat(),
        "note": SIMULATED_NOTE,
    }


def _money(amount: float) -> str:
    return f"${amount:,.0f}" if float(amount).is_integer() else f"${amount:,.2f}"


def fixture_transcript(track: str, target_name: str, seed: str, *, today: date | None = None) -> list[dict]:
    """[{speaker, text}] opening with "Hi, this is Alex, am I speaking with ...", the one-sentence AI line,
    and ending with a "Just to confirm, ..." recap. Speaker labels match Dial's: "agent" and "user"."""
    outcome = fixture_outcome(track, seed, today=today)
    name = target_name or "there"
    opener = f"{SPOKEN_DISCLOSURE} {name}?"
    turns: list[dict] = [
        {"speaker": "agent", "text": opener},
        {"speaker": "user", "text": f"Yes, this is {name}."},
        {"speaker": "agent", "text": SPOKEN_AI_LINE},
        {"speaker": "user", "text": "Okay, what can I do for you?"},
    ]
    if track == "quote":
        amount, unit = outcome["amount_usd"], outcome["unit"]
        price = f"{_money(amount)} {unit}"
        caveats = ", ".join(outcome["caveats"])
        turns += [
            {
                "speaker": "agent",
                "text": "I'm looking at a two-bedroom move on November first. What would that run all-in?",
            },
            {
                "speaker": "user",
                "text": f"For a two-bedroom on that date we would be at {price}. That {caveats}.",
            },
            {"speaker": "agent", "text": "Got it. Does that include the truck and two movers?"},
            {"speaker": "user", "text": "Yes, truck, two movers and basic liability coverage."},
            {
                "speaker": "agent",
                "text": f"Just to confirm, {name} is at {price} for the November first move, {caveats}. Thanks, bye.",
            },
        ]
    elif track == "schedule":
        slot = datetime.fromisoformat(outcome["slot_iso"])
        spoken = slot.strftime("%A %B %d at %-I:%M %p")
        turns += [
            {
                "speaker": "agent",
                "text": "I'd like to book something next week, weekday afternoons. What's the earliest you have?",
            },
            {"speaker": "user", "text": f"I have {spoken} open."},
            {"speaker": "agent", "text": "That works, go ahead and book it."},
            {"speaker": "user", "text": "Done, you are confirmed."},
            {
                "speaker": "agent",
                "text": f"Just to confirm, {spoken} with {name}. Thanks, bye.",
            },
        ]
    else:
        saved = _money(outcome["saved_usd"])
        effective = date.fromisoformat(outcome["effective_date"]).strftime("%B %d")
        turns += [
            {
                "speaker": "agent",
                "text": "I'm going over the monthly bill. What promotions or adjustments are available on it right now?",
            },
            {
                "speaker": "user",
                "text": f"I can apply a loyalty discount of {saved} per month, effective {effective}.",
            },
            {
                "speaker": "agent",
                "text": "Let's do that. Is that the best rate available, and does it change the contract?",
            },
            {
                "speaker": "user",
                "text": "No contract change, the discount stays as long as the account is current.",
            },
            {
                "speaker": "agent",
                "text": (
                    f"Just to confirm, {name} is taking {saved} a month off starting {effective}, no contract "
                    "change. Thanks, bye."
                ),
            },
        ]
    return turns


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _user_id_for(store: Any, call_row: dict) -> str | None:
    if call_row.get("user_id"):
        return str(call_row["user_id"])
    run = store.get_run(call_row["run_id"]) if call_row.get("run_id") else None
    return run.get("user_id") if run else None


def advance_simulated(store: Any, call_row: dict, now: datetime) -> dict | None:
    """Move a simulated row along its scripted timeline. Returns the refreshed row, or None when
    the row is not simulated or no longer exists. Safe to call every tick; monotonic writes make
    repeated and concurrent calls idempotent."""
    if not call_row or not call_row.get("simulated") or not call_row.get("id"):
        return None
    call_id = call_row["id"]
    now = _as_utc(now)
    started = _parse_ts(call_row.get("started_at"))
    if started is None:
        started = now
        store.update_call(call_id, {"started_at": started.isoformat()})
    elapsed = (now - started).total_seconds()
    talk = talk_seconds(call_id)
    previous_rank = int(call_row.get("state_rank") or 0)
    user_id = _user_id_for(store, call_row)
    run_id = call_row.get("run_id")
    track = call_row.get("track") or "quote"

    def emit(type: str, data: dict) -> None:
        if user_id:
            log_event(
                store,
                user_id,
                type,
                {"simulated": True, "track": track, **data},
                run_id=run_id,
                call_id=call_id,
            )

    if elapsed < DIALING_SECONDS:
        store.update_call_state(call_id, "dialing", STATE_RANK["dialing"])
    elif elapsed < RINGING_UNTIL_SECONDS:
        store.update_call_state(call_id, "ringing", STATE_RANK["ringing"])
    elif elapsed < RINGING_UNTIL_SECONDS + talk:
        if store.update_call_state(call_id, "talking", STATE_RANK["talking"]):
            emit("call_connected", {})
    else:
        ended = started + timedelta(seconds=RINGING_UNTIL_SECONDS + talk)
        turns = fixture_transcript(track, call_row.get("target_name") or "", call_id, today=ended.date())
        step_ms = int(talk * 1000 / max(1, len(turns)))
        timed = [{**t, "startMs": i * step_ms, "endMs": (i + 1) * step_ms - 200} for i, t in enumerate(turns)]
        outcome = fixture_outcome(track, call_id, today=ended.date())
        fields = {
            "transcript": "\n".join(f"{t['speaker']}: {t['text']}" for t in turns),
            "transcript_turns": timed,
            "duration_seconds": float(talk),
            "outcome": outcome,
            "termination_type": "completed",
            "ended_at": ended.isoformat(),
            "error": None,
        }
        if store.update_call_state(call_id, "done", STATE_RANK["done"], fields):
            if previous_rank < STATE_RANK["talking"]:
                emit("call_connected", {})
            emit("call_terminated", {"termination_type": "completed", "duration_seconds": float(talk)})
            emit("outcome_extracted", {"outcome": outcome})
    return store.get_call(call_id)
