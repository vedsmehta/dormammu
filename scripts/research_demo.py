"""Run the research engine once, for real, and print the proposal as a table.

    uv run python scripts/research_demo.py
    uv run python scripts/research_demo.py --city "Menlo Park" --zip 94025 --date 2026-11-01 --bedrooms 2 \
        --budget 1200 --provider "Comcast:internet:89.99" --provider "Verizon:phone:70" --demo-roster \
        --request "Moving to Menlo Park on November 1, handle the move and internet"

Needs OPENAI_API_KEY in .env for web research; without it the proposal holds roster items and the static
regulations only, labeled as such. Phone numbers are masked in the output. Writes the full proposal to
.calls/research-demo.json (CALL_RESULTS_DIR). Does not touch the database and does not dial.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import phones  # noqa: E402
from research.engine import research  # noqa: E402
from settings import load_env, results_dir  # noqa: E402

DEFAULT_REQUEST = (
    "I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist"
)
DEMO_ROSTER = [
    {"id": "demo-1", "key": "movers_a", "name": "BearHug Moving", "track": "quote", "phone": "+15550100001", "is_demo": True},
    {"id": "demo-2", "key": "dentist", "name": "Dr. Patel Dental", "track": "schedule", "phone": "+15550100002", "is_demo": True},
]  # fmt: skip


def _provider(text: str) -> dict:
    name, _, rest = text.partition(":")
    category, _, monthly = rest.partition(":")
    try:
        amount = float(monthly) if monthly else None
    except ValueError:
        amount = None
    return {"category": category or "other", "name": name.strip(), "monthly_usd": amount, "contract_end": None}


def build_profile(args: argparse.Namespace) -> dict:
    providers = [_provider(p) for p in args.provider] if args.provider is not None else [
        {"category": "internet", "name": "Comcast", "monthly_usd": 89.99, "contract_end": None}
    ]
    return {
        "id": "demo-user",
        "display_name": args.name,
        "phone": None,
        "cohort": "student",
        "prefs": {
            "new_address": {"street": None, "city": args.city, "state": args.state, "zip": args.zip},
            "move_date": args.date,
            "bedrooms": args.bedrooms,
            "household_size": 1,
            "budget_ceiling_usd": args.budget,
            "current_providers": providers,
            "insurance": {"dental": args.dental, "health": None},
            "student_proof": "school.edu email on file",
            "preferred_times": ["weekday afternoons"],
            "caller_name": "Alex",
        },
    }


def _money(value) -> str:
    if value is None:
        return "-"
    return f"${value:,.0f}" if float(value) == int(value) else f"${value:,.2f}"


def print_proposal(proposal: dict) -> None:
    d = proposal["details"]
    print(f"\nRequest: {proposal['request']}")
    print(
        f"Details: destination {d.get('destination')} {d.get('state') or ''} {d.get('zip') or ''}; "
        f"date {d.get('date')}; {d.get('bedrooms')}; budget {d.get('budget')}"
    )
    print(f"Model {proposal['model']}; web search {'on' if proposal['web_search'] else 'OFF'}; "
          f"{proposal['search_count']} searches; {proposal.get('seconds')}s")  # fmt: skip
    print("\nCategories")
    for c in proposal["categories"]:
        print(f"  {c['key']:<18} {c['track']:<10} {c['why']}")
        if c.get("query"):
            print(f"  {'':<18} {'':<10} search: {c['query']}")

    print("\nItems")
    header = f"  {'category':<14} {'vendor':<30} {'phone':<16} {'ok':<4} {'conf':<5} {'rec':<4} {'cites':<5} why"
    print(header)
    for it in proposal["items"]:
        v = it["vendor"]
        print(
            f"  {it['category']:<14} {v['name'][:30]:<30} {phones.mask(v['phone']):<16} "
            f"{'yes' if it['phone_verified'] else 'no':<4} {it['confidence']:<5.2f} "
            f"{'yes' if it['recommended'] else 'no':<4} {len(it['citations']):<5} {it['why'][:60]}"
        )
        for p in it["plans"]:
            flag = "" if p.get("price_in_source") is None else (" (price seen)" if p["price_in_source"] else " (price not seen)")
            print(f"  {'':<14}   plan: {p['name']} {_money(p['price_usd'])} {p.get('unit') or ''}{flag} [cite {p['citation']}]")
        for disc in it["discounts"]:
            print(f"  {'':<14}   discount: {disc['label']} for {disc['who_qualifies']} [cite {disc['citation']}]")
        s = it["strategy"]
        print(f"  {'':<14}   strategy: {s['variant']} ({s.get('label')}); asks: {', '.join(s['asks'])}")
        print(f"  {'':<14}   opening: {s['opening']}")
        if it.get("skip_reason"):
            print(f"  {'':<14}   skip: {it['skip_reason']}")

    print("\nRegulations")
    for r in proposal["regulations"]:
        cite = r["citation"]["url"] if r.get("citation") else "(no verified citation)"
        print(f"  [{'applies' if r['applies'] else 'n/a    '}] {r['rule'][:90]}")
        print(f"            we: {r['what_we_do'][:100]}")
        print(f"            {cite}")

    print(f"\nSummary: {proposal['summary']}")
    if proposal["errors"]:
        print("\nErrors")
        for e in proposal["errors"]:
            print(f"  - {e}")

    items = proposal["items"]
    verified = sum(1 for it in items if it["phone_verified"])
    web = sum(1 for it in items if it["source"] == "web")
    print(
        f"\nHeadline: {len(items)} items ({web} from the web), {verified} phones verified, "
        f"{len(proposal['categories'])} categories, {len(proposal['regulations'])} regulations, "
        f"{proposal.get('seconds')}s"
    )


async def _main() -> None:
    load_env()
    parser = argparse.ArgumentParser(description="Run the Dormammu research engine once")
    parser.add_argument("--request", default=DEFAULT_REQUEST)
    parser.add_argument("--name", default="Vedant")
    parser.add_argument("--city", default="Palo Alto")
    parser.add_argument("--state", default="CA")
    parser.add_argument("--zip", default="94301")
    parser.add_argument("--date", default="2026-10-25", help="YYYY-MM-DD")
    parser.add_argument("--bedrooms", default="1", help="studio, 1, 2 or 3+")
    parser.add_argument("--budget", type=float, default=900.0)
    parser.add_argument("--dental", default=None, help="dental insurance carrier, if any")
    parser.add_argument("--provider", action="append", default=None, help="Name:category:monthly, repeatable")
    parser.add_argument("--demo-roster", action="store_true", help="include two labeled demo roster vendors")
    parser.add_argument("--max-items", type=int, default=3)
    args = parser.parse_args()

    profile = build_profile(args)
    roster = DEMO_ROSTER if args.demo_roster else []
    started = time.monotonic()
    proposal = await research(profile, args.request, roster, max_items_per_category=args.max_items)
    proposal.setdefault("seconds", round(time.monotonic() - started, 1))
    print_proposal(proposal)
    out = results_dir() / "research-demo.json"
    out.write_text(json.dumps(proposal, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    asyncio.run(_main())
