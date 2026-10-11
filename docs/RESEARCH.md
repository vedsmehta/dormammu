# Research engine (`research/`)

The calls are the last five minutes. The hours are in finding who to call, what they charge, what the
student can claim, and what the rules are. `research/` does that part: it reads everything onboarding
captured about the user, goes out with web search, verifies what it finds, and returns one proposal the
user approves item by item. Nothing in this package dials.

## Entry point

```python
from research.engine import research

proposal = await research(profile, request, roster, max_items_per_category=3)
```

- `profile`: the profiles row (`{id, display_name, phone, prefs, cohort}`) or None. Reads
  `prefs.new_address {street, city, state, zip}`, `current_address`, `move_date` (YYYY-MM-DD), `bedrooms`
  (`studio | 1 | 2 | 3+`), `household_size`, `current_providers [{category, name, monthly_usd,
  contract_end}]`, `insurance {dental, health}`, `student_proof`, `budget_ceiling_usd`. Every key is
  optional.
- `request`: the plain-language sentence.
- `roster`: the user's vendor rows (`{id, key, name, track, phone, is_demo}`).
- Async, never raises. Overall budget `OVERALL_BUDGET_SECONDS` (55) via `asyncio.wait_for`; each category
  search `QUERY_TIMEOUT_SECONDS` (25); at most `MAX_CONCURRENT_SEARCHES` (5) model calls in flight. On a
  timeout the proposal holds what finished plus an entry in `errors`.

## The proposal (snake_case, JSON-serializable; `research/schemas.py: Proposal`)

```
{
  "request": str,
  "details": parse_request_details(request) + profile prefs:
             {date, bedrooms, origin, destination, budget, state, zip, new_address, move_date,
              budget_ceiling_usd, household_size, insurance, student_proof, is_move},
  "categories": [{"key", "label", "track": quote|schedule|negotiate, "why", "source": request|profile|roster,
                  "query": str|null, "provider": {category, name, monthly_usd, contract_end}|null}],
  "items": [{
      "id": uuid4, "category", "track", "source": roster|web,
      "vendor": {"key", "name", "phone": E.164|null, "website", "address", "is_demo", "roster_vendor_id"},
      "why": one sentence,
      "plans": [{"name", "price_usd", "unit", "notes", "citation": index into item.citations,
                 "price_in_source": bool|null}],
      "discounts": [{"label", "who_qualifies", "how_to_claim", "citation": index}],
      "strategy": {"variant", "label", "reason", "asks": [str], "opening", "walk_away", "objective"},
      "confidence": 0..1, "citations": [{"title", "url", "verified": api|fetched}],
      "recommended": bool, "phone_verified": bool, "skip_reason": str|null
  }],
  "regulations": [{"key", "rule", "jurisdiction", "applies", "what_we_do",
                   "citation": {"title", "url", "verified"}|null, "note": str|null}],
  "summary": one paragraph, "generated_at": iso, "model": str,
  "web_search": bool, "search_count": int, "errors": [str], "seconds": float
}
```

`details` and `categories` are built without a model (`engine.merge_details`, `engine.select_categories`),
so they are the same with or without an API key. The request wins over the profile where both speak.

### Categories

Keyword families in `engine.CATEGORY_FAMILIES`: `movers` (quote), `internet` (negotiate), `dentist`
(schedule), `doctor` (schedule), `phone` (negotiate), `gym` (quote), `insurance` (quote), `utilities`
(schedule), `storage` (quote). A move plus "handle it" / "everything" adds movers, internet and utilities.
Each `current_providers` entry becomes `lower_<slug>` ("Lower Comcast bill", negotiate), or `cancel_<slug>`
when the request says cancel and names the provider or its category.

Searches are concrete, with the destination, zip, date and bedroom count substituted:
"licensed movers serving Palo Alto CA phone number 1-bedroom apartment move October 25",
"internet providers available at Palo Alto, CA 94301 student plan price phone number",
"dentist near Palo Alto CA accepting new patients phone number new patient cleaning price",
"Comcast retention department phone number current promotion internet".

### Items

- Roster vendors that fit a category are items with `source: roster`, `why: "already on your roster"`,
  `phone_verified: true`, no plans or discounts, confidence 1.0. They sit on top of the
  `max_items_per_category` web items. If the request names no category at all, each roster vendor is
  filed under its track (`quote`, `schedule`, `negotiate`).
- Web items come from one Responses API call per category (`engine.search_structured`) with the hosted
  `web_search` tool and a strict pydantic `text_format` (`schemas.CategoryFindings`). The model is told to
  copy phone numbers exactly as a source writes them and to attach a `source_url` to every plan and
  discount.
- Verification does not trust the model. `citations` are URLs the API itself cited (`verified: "api"`,
  from `url_citation` annotations and `web_search_call.action.sources`) plus URLs we fetched successfully
  with httpx (`verified: "fetched"`, 10 second timeout, text types only, at most `MAX_FETCH_PER_ITEM`
  fetches per item). A plan or discount whose `source_url` resolves to neither is dropped. A phone is kept
  only when the same ten digits appear in a fetched page (`phones.verify_phone`, matching formatted
  numbers and `tel:` links); otherwise `phone` is null, `phone_verified` false and `skip_reason` says why
  ("no phone number found in sources", "phone not confirmed in fetched sources", "phone in sources is not
  a US number"). `price_in_source` says whether the plan's dollar amount was seen on its cited page.
- `confidence` is the model's 0..1 scaled by 0.6 when the phone is unverified. `recommended` is
  `phone_verified and confidence >= 0.5`.

### Strategy

- `variant`: for negotiate items the key from `router.negotiation.choose_strategy(objective, None, vendor)`
  (`new_service`, `S1_anchor`, `S2_loyalty`, `S3_cancel`, `S4_audit`), with its `label` and `reason`; for
  quote and schedule items the track name.
- `asks`: `router.cohorts.cohort_asks(cohort, track)` first, then the model's item-specific asks, deduped.
- `opening`: one sentence the agent says after the fixed opener and the AI line. The model's sentence is
  used when it is one plain line; otherwise a template built from the details.
- `walk_away`: one sentence with the budget ceiling, the current monthly bill or the move date.
- `objective`: the sentence a CallPlan would carry, written like `router.plan.default_objective` (the
  bedroom count, destination and date, "date to be confirmed" when there is none). It does not include
  the "Ask for:" line; `router.plan.with_cohort_asks` appends it when a plan is created.

### Regulations (`research/regulations.py`)

`await check(state, context)` with `context = {"canceling": bool, "categories": [...], "destination",
"recording": False, "cohort"}`. Six baseline rules: recording consent (California Penal Code 632, we
disclose and do not record), bot disclosure (B&P Code 17940-17943, the AI line is the second sentence),
TCPA and the FCC's February 2024 ruling on AI voices (we call businesses, 9am to 6pm local, and identify
ourselves), FTC honesty (the agent claims only the eligibility on the profile), California's automatic
renewal law when a provider is being canceled, and contract-extension authority (the agent may not extend,
add or cancel without written authorization). With a key, the model confirms each rule for the state and a
citation is kept only when the API cited it or we fetched it. Without a key or on any failure,
`regulations.baseline(state, context, note)` comes back: citation null, `applies` true where it is a legal
certainty for California, a `note` on every row. Cached by state for the process; `applies` for the
cancellation rule is recomputed per call. `regulations.clear_cache()` resets it.

## What the next lane consumes

- `research.engine.research(profile, request, roster, *, max_items_per_category=3) -> dict` (above).
- Approving an item: upsert a vendor row from `item.vendor` (`key`, `name`, `track`, `phone`) and build the
  call from `item.strategy.objective` (then `with_cohort_asks`) and `item.strategy.variant`. Items with
  `phone_verified: false` have `vendor.phone: null` and cannot be dialed. Roster items carry
  `vendor.roster_vendor_id`, so they map back to the existing row instead of a new one.
- `research.phones.mask(e164)` for display, `research.phones.normalize(raw)` for input.
- `research.schemas.Proposal.model_validate(proposal)` to validate a stored proposal.

## OpenAI call shape (verified against openai 3.29.0 on Oct 11 2026)

```python
response = await client.responses.parse(
    model=OPENAI_MODEL, instructions=system, input=user,
    tools=[{"type": "web_search", "search_context_size": "low",
            "user_location": {"type": "approximate", "country": "US", "city": ..., "region": ...}}],
    text_format=CategoryFindings, include=["web_search_call.action.sources"], max_tool_calls=4,
)
response.output_parsed                 # the CategoryFindings instance
response.output                        # web_search_call items (action.query, action.sources[].url) and a
                                       # message whose output_text content has .annotations of type
                                       # url_citation {title, url, start_index, end_index}
```

If the account rejects the tool, the engine retries once without it, sets `web_search: false` on the
proposal and keeps no citations, so the UI can say the run was not web-grounded.

## Run it

```
uv run python scripts/research_demo.py            # prints the table, writes .calls/research-demo.json
uv run pytest test/test_research.py -q            # no network; one @pytest.mark.live test needs OPENAI_API_KEY
uv run ruff check research scripts/research_demo.py test/test_research.py
```

Seams tests monkeypatch: `research.engine.search_structured` (the model) and `research.phones.fetch_text`
(httpx).
