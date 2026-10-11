"""Run the real extractor over the labeled fixture transcripts and publish the accuracy evidence.

    uv run python scripts/eval_extract.py                       # all fixtures, 4 concurrent OpenAI calls
    uv run python scripts/eval_extract.py --only quote_01_flat_clean --only schedule_03_pencil_in
    uv run python scripts/eval_extract.py --concurrency 2 --out /tmp/eval.json --md /tmp/EVAL.md

Needs OPENAI_API_KEY in .env (exit code 2 with a message when it is missing). Every fixture carries a
call_time; the extractor's clock (aggregator.extract.datetime.now) is frozen to it per task through a
ContextVar, so relative phrases on the call ("Thursday at 2:30", "first of next month") resolve the way they
did that day. Scoring is aggregator.accuracy. Writes public/eval-report.json (Next serves public/ statically,
so the evidence page fetches /eval-report.json) and docs/EVAL.md. The fixtures contain no real phone numbers
and nothing secret is printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aggregator import accuracy
from aggregator import extract as ex
from router import llm
from settings import load_env

FIXTURES_DIR = ROOT / "test" / "fixtures" / "transcripts"
REPORT_PATH = ROOT / "public" / "eval-report.json"
MARKDOWN_PATH = ROOT / "docs" / "EVAL.md"
DEFAULT_CONCURRENCY = 4
REQUIRED_KEYS = ("id", "track", "scenario", "call_time", "turns", "expected")
TOLERANCES = {
    "amount_usd": "within $1",
    "unit": "same meaning (flat, per hour, per month)",
    "slot_iso": "same day and hour, any offset",
    "confirmed": "equal",
    "action": "equal",
    "saved_usd": "within $1",
    "effective_date": "same date",
    "caveats": "not scored",
    "note": "not scored",
}

CALL_TIME: ContextVar[datetime | None] = ContextVar("eval_call_time", default=None)


class FrozenClock(datetime):
    """Stand-in for aggregator.extract.datetime: now() is the fixture's call_time when one is set on this task."""

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        fixed = CALL_TIME.get()
        if fixed is None:
            return datetime.now(tz)
        moment = fixed if tz is None else fixed.astimezone(tz)
        return cls.fromtimestamp(moment.timestamp(), tz=moment.tzinfo)

    def astimezone(self, tz=None):  # type: ignore[override]
        # Keep the fixture's own offset so the prompt reads as the vendor's local time, not this machine's.
        if tz is None and self.tzinfo is not None:
            return self
        return super().astimezone(tz)


def install_frozen_clock() -> None:
    ex.datetime = FrozenClock


def parse_call_time(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return moment


def load_fixtures(directory: Path = FIXTURES_DIR, only: list[str] | None = None) -> list[dict]:
    """Every *.json in the directory, sorted by name, validated for the keys the eval needs."""
    fixtures = []
    for path in sorted(Path(directory).glob("*.json")):
        data = json.loads(path.read_text())
        missing = [key for key in REQUIRED_KEYS if key not in data]
        if missing:
            raise ValueError(f"{path.name}: missing {', '.join(missing)}")
        if data["track"] not in accuracy.SCORED_FIELDS:
            raise ValueError(f"{path.name}: unknown track {data['track']!r}")
        if only and data["id"] not in only:
            continue
        fixtures.append(data)
    return fixtures


def extraction_error(outcome: dict) -> str | None:
    """The note when the extractor itself failed (timeout, refusal, parse), as opposed to reading a value wrong."""
    note = str(outcome.get("note") or "")
    return note if note.startswith(("extraction failed", "extraction error")) else None


async def evaluate_one(fixture: dict, semaphore: asyncio.Semaphore) -> dict:
    CALL_TIME.set(parse_call_time(fixture["call_time"]))
    async with semaphore:
        started = time.perf_counter()
        actual = await ex.extract(fixture["track"], fixture["turns"])
        seconds = time.perf_counter() - started
    scored = accuracy.score(fixture["expected"], actual, fixture["track"])
    has_recap = accuracy.recap_line(fixture["turns"]) is not None
    checkable = has_recap and accuracy.recap_value(actual) is not None
    return {
        "id": fixture["id"],
        "track": fixture["track"],
        "scenario": fixture["scenario"],
        "exact": scored["exact"],
        "partial": scored["partial"],
        "fields": scored["fields"],
        "expected": fixture["expected"],
        "actual": actual,
        "has_recap": bool(fixture.get("has_recap", has_recap)),
        "recap_disagrees": bool(fixture.get("recap_disagrees", False)),
        "recap_agrees": accuracy.recap_agreement(fixture["turns"], actual) if checkable else None,
        "error": extraction_error(actual),
        "seconds": round(seconds, 2),
    }


async def evaluate(fixtures: list[dict], concurrency: int = DEFAULT_CONCURRENCY) -> list[dict]:
    """One extract() per fixture, at most `concurrency` in flight. gather() gives each coroutine its own task
    and therefore its own context copy, so CALL_TIME is per fixture even while they overlap."""
    semaphore = asyncio.Semaphore(max(1, concurrency))
    return list(await asyncio.gather(*(evaluate_one(fixture, semaphore) for fixture in fixtures)))


def build_report(cases: list[dict], model: str) -> dict:
    summary = accuracy.summarize([{"track": c["track"], "fields": c["fields"], "exact": c["exact"]} for c in cases])
    checked = [c for c in cases if c["recap_agrees"] is not None]
    agreed = sum(1 for c in checked if c["recap_agrees"])
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "n": summary["n"],
        "exact_rate": summary["exact_rate"],
        "field_rates": summary["field_rates"],
        "by_track": summary["by_track"],
        "recap": {"checked": len(checked), "agreed": agreed, "rate": accuracy.rate(agreed, len(checked))},
        "errors": sum(1 for c in cases if c["error"]),
        "tolerances": TOLERANCES,
        "fixtures": {
            "dir": "test/fixtures/transcripts",
            "no_recap": sum(1 for c in cases if not c["has_recap"]),
            "recap_disagrees": sum(1 for c in cases if c["recap_disagrees"]),
        },
        "cases": cases,
    }


def misses(report: dict) -> list[tuple[str, str, object, object]]:
    found = []
    for case in report["cases"]:
        for field, ok in case["fields"].items():
            if not ok:
                found.append((case["id"], field, case["expected"].get(field), case["actual"].get(field)))
    return found


def misses_paragraph(report: dict) -> str:
    missed = [c for c in report["cases"] if not c["exact"]]
    if not missed:
        return f"Every one of the {report['n']} fixtures scored exact on every field within the tolerances below."
    by_field = Counter(field for c in missed for field, ok in c["fields"].items() if not ok)
    disagreeing = [c["id"] for c in missed if c["recap_disagrees"]]
    no_recap = [c["id"] for c in missed if not c["has_recap"]]
    errors = [c["id"] for c in missed if c["error"]]
    parts = [
        f"{len(missed)} of {report['n']} fixtures missed at least one field.",
        "Fields missed: " + ", ".join(f"{field} ({count})" for field, count in by_field.most_common()) + ".",
    ]
    if disagreeing:
        parts.append(
            f"{len(disagreeing)} of those ({', '.join(disagreeing)}) are fixtures where the spoken recap contradicts "
            "the body and the label follows the body; the extractor prompt tells the model to prefer the recap, so "
            "these are the known cost of that rule."
        )
    if no_recap:
        parts.append(f"{len(no_recap)} ({', '.join(no_recap)}) had no recap line, so the model read the body alone.")
    if errors:
        parts.append(
            f"{len(errors)} ({', '.join(errors)}) were extraction errors (timeout, refusal or parse failure), not "
            "wrong reads."
        )
    return " ".join(parts)


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _fields_ok(case: dict) -> str:
    fields = case["fields"]
    return f"{sum(1 for ok in fields.values() if ok)}/{len(fields)}"


def _recap_label(case: dict) -> str:
    if case["recap_agrees"] is None:
        return "n/a"
    return "yes" if case["recap_agrees"] else "no"


def print_table(report: dict) -> None:
    header = ("id", "track", "scenario", "exact", "fields", "recap", "time")
    rows = [
        (c["id"], c["track"], c["scenario"], "yes" if c["exact"] else "no", _fields_ok(c), _recap_label(c),
         f"{c['seconds']:.1f}s")
        for c in report["cases"]
    ]
    widths = [max(len(str(row[i])) for row in (header, *rows)) for i in range(len(header))]
    for row in (header, *rows):
        print("  ".join(str(value).ljust(width) for value, width in zip(row, widths)))
    print()
    print(f"n={report['n']}  exact={_pct(report['exact_rate'])}  model={report['model']}  errors={report['errors']}")
    for track, stats in report["by_track"].items():
        fields = ", ".join(f"{field} {_pct(value)}" for field, value in stats["field_rates"].items())
        print(f"  {track:<10} n={stats['n']:<3} exact={_pct(stats['exact_rate']):<5} {fields}")
    recap = report["recap"]
    print(f"  recap line names the extracted value on {recap['agreed']}/{recap['checked']} checkable calls")


def render_markdown(report: dict) -> str:
    recap = report["recap"]
    intro = (
        f"Generated {report['generated_at']} by `scripts/eval_extract.py` against model `{report['model']}`. "
        "Regenerate with `uv run python scripts/eval_extract.py`; do not edit by hand. The UI reads the same "
        "numbers from `public/eval-report.json`."
    )
    recap_sentence = (
        f"Recap agreement: the agent's \"Just to confirm\" line named the extracted value on {recap['agreed']} of "
        f"{recap['checked']} calls where both existed ({_pct(recap['rate'])}). Extraction errors (timeout, refusal, "
        f"parse): {report['errors']}."
    )
    lines = [
        "# Extraction accuracy",
        "",
        intro,
        "",
        "## Summary",
        "",
        "| track | n | exact |",
        "|---|---|---|",
        f"| all | {report['n']} | {_pct(report['exact_rate'])} |",
    ]
    for track, stats in report["by_track"].items():
        lines.append(f"| {track} | {stats['n']} | {_pct(stats['exact_rate'])} |")
    lines += ["", "| track | field | rate |", "|---|---|---|"]
    for track, stats in report["by_track"].items():
        for field, value in stats["field_rates"].items():
            lines.append(f"| {track} | {field} | {_pct(value)} |")
    lines += [
        "",
        recap_sentence,
        "",
        "## Cases",
        "",
        "| id | track | exact | fields | recap |",
        "|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        exact = "yes" if c["exact"] else "no"
        lines.append(f"| {c['id']} | {c['track']} | {exact} | {_fields_ok(c)} | {_recap_label(c)} |")
    lines += ["", "## Misses", "", misses_paragraph(report)]
    if misses(report):
        lines.append("")
        for case_id, field, expected, actual in misses(report):
            lines.append(f"- {case_id} {field}: expected `{expected!r}`, got `{actual!r}`")
    fixtures = report["fixtures"]
    corpus = (
        f"{report['n']} labeled transcripts live in `{fixtures['dir']}`, eight per track. Each is written the way a "
        "real call runs: the agent opens with \"Hi, this is Alex, am I speaking with <vendor>?\", says once that it is "
        "an AI assistant calling for the client, and usually closes with a \"Just to confirm\" recap. "
        f"{fixtures['no_recap']} have no recap line at all. {fixtures['recap_disagrees']} have a recap that "
        "contradicts the body (the rep corrects it right after); their labels follow the body, since the correction "
        "is what was actually agreed."
    )
    clock = (
        "Each fixture carries a `call_time`. The script freezes the extractor's clock to it per task, so relative "
        "phrases such as \"Thursday at 2:30\" or \"the first of next month\" resolve against the day of the call, not "
        "the day the eval ran."
    )
    scope = (
        "What this does not measure: speech-to-text quality on the live Dial transcript, and the phone agent's own "
        "conduct. These transcripts are typed, so they test the extractor's reading, not the microphone."
    )
    lines += [
        "",
        "## Method",
        "",
        corpus,
        "",
        clock,
        "",
        "Scoring is `aggregator/accuracy.py`. Tolerances:",
        "",
        "| field | tolerance |",
        "|---|---|",
    ]
    for field, tolerance in report["tolerances"].items():
        lines.append(f"| {field} | {tolerance} |")
    lines += ["", scope, ""]
    return "\n".join(lines)


def _show(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score aggregator.extract against the labeled fixtures.")
    parser.add_argument("--fixtures", default=str(FIXTURES_DIR), help="directory of fixture *.json")
    parser.add_argument("--only", action="append", help="fixture id to include (repeatable)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--out", default=str(REPORT_PATH), help="where to write the JSON report")
    parser.add_argument("--md", default=str(MARKDOWN_PATH), help="where to write the markdown report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "OPENAI_API_KEY is not set. Add it to .env (settings.load_env reads .env and test/.env) and rerun.",
            file=sys.stderr,
        )
        return 2
    fixtures = load_fixtures(Path(args.fixtures), args.only or None)
    if not fixtures:
        print(f"no fixtures matched in {args.fixtures}", file=sys.stderr)
        return 1
    install_frozen_clock()
    cases = asyncio.run(evaluate(fixtures, args.concurrency))
    report = build_report(cases, llm.model_name())
    print_table(report)
    out, md = Path(args.out), Path(args.md)
    out.parent.mkdir(parents=True, exist_ok=True)
    md.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    md.write_text(render_markdown(report))
    print(f"\nwrote {_show(out)} and {_show(md)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
