"""STUB. Lane D replaces this file. Signature is the contract (docs/CONTRACT.md)."""

from __future__ import annotations

import os

DIAL_OVERHEAD_SECONDS = int(os.environ.get("DIAL_OVERHEAD_SECONDS", "45"))


def aggregate(run: dict) -> dict:
    calls = run.get("calls", [])
    return {
        "tracks": {"quote": [], "schedule": [], "negotiate": []},
        "wall_clock_seconds": None,
        "talk_seconds": 0.0,
        "sequential_estimate_seconds": None,
        "multiplier": None,
        "dial_overhead_seconds": DIAL_OVERHEAD_SECONDS,
        "n_calls": len(calls),
        "n_completed": 0,
        "n_failed": 0,
        "n_simulated": sum(1 for c in calls if c.get("simulated")),
        "n_live": 0,
    }
