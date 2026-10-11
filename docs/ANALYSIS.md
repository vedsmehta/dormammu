# Analysis layer (Keewano side of Dormammu)

Status Oct 11 2026. Phase 1: the `analysis/` package, `scripts/seed_demo.py`, tests. Phase 2 wires a
route and a page (owned elsewhere).

## What it is for

Every event is written to Postgres first (`events` table, `events/log.py`) and then shipped to Keewano as a
secondary sink (`events/keewano.py`), one Keewano entity per end user (`userId` = `profiles.id`). Keewano
is the analysis surface: patterns across many calls and runs over time, which feed prior context at
dispatch. The honesty line from PRD §6 stands: this layer surfaces patterns and supplies context to the
agent's instructions. It does not rewrite prompts or vendor templates.

The package answers the same questions locally over store rows (so the UI works today, with or without
Keewano) and expresses them for Keewano's MCP tools (so the two can be compared once ingest accepts a
project key).

## Modules

| Module | Entry point | Purpose |
|---|---|---|
| `analysis/insights.py` | `compute_insights(store, user_id, *, now=None, max_runs=200, events_limit=5000, tz=None)` | JSON report over store rows |
| `analysis/context.py` | `prior_context_from_insights(insights, vendor_key, track)`, `prior_context_for(store, user_id, vendor_key, track, *, insights=None)` | one or two sentences for dispatch |
| `analysis/keewano_queries.py` | `KEEWANO_QUERIES`, `render_arguments(...)`, `async keewano_insights(user_id)` | the same questions for Keewano, plus the MCP client |
| `scripts/seed_demo.py` | `generate(store, user_id, runs, days, seed, ...)`, `wipe(store, user_id)`, CLI | labeled demo history |

### What `compute_insights` returns

All keys are always present; empty history gives zeros and `None`, never an error.

- `coverage`: `n_runs`, `n_calls`, `n_done`, `n_failed`, `n_live`, `n_simulated`, `n_seeded`,
  `share_simulated`, `share_seeded`, `share_synthetic`, `label` ("all seeded or simulated demo data",
  "live calls only", "42% seeded or simulated", "no calls yet"), `n_events`, `n_events_seeded`,
  `first_call_at`, `last_call_at`, `runs_truncated`, `events_truncated`, `vendors`. The UI labels the
  report with `label`.
- `vendor_scorecards[vendor_key]`: `name`, `track`, `n_calls`, `n_done`, `n_failed`, `failure_breakdown`
  (by termination_type: voicemail, no-answer, busy, unknown), `answer_rate`, `avg_duration_seconds`,
  `last_called_at`, `last_outcome`, `n_with_prior_context`, `time_of_day` (per bucket: n_calls,
  n_answered, n_voicemail), `takeaway` (one sentence). Quote vendors add `best_quote_usd`,
  `latest_quote_usd`, `first_quote_usd`, `avg_quote_usd`, `quote_trend` (down/up/flat), `times_cheapest`
  and `n_compared` (runs with at least two priced quotes). Negotiate vendors add `n_reduced`,
  `n_canceled`, `n_refused`, `reduction_rate`, `total_saved_usd_per_month`, `avg_saved_usd`. Schedule
  vendors add `n_confirmed`, `confirmed_rate`, `latest_slot_iso`.
- `track_stats[track]`: `n_calls`, `n_done`, `n_failed`, `n_runs`, `answer_rate`, `avg_calls_per_run`,
  `avg_talk_seconds`, `avg_wall_clock_seconds_per_run`, `avg_multiplier_est` (labeled "est.", uses
  `DIAL_OVERHEAD_SECONDS` per done call exactly like `aggregator.aggregate`), `median_outcome` with
  `median_outcome_label` (median quote, median saved per month, median days until the booked slot).
- `failure_modes`: `n_failed`, `n_terminal`, `failure_rate`, `counts`, `share`, `by_track`.
- `time_of_day`: hour buckets in the user's zone (`DORMAMMU_TZ`, default America/Los_Angeles): before 11am,
  11am to 2pm, 2pm to 5pm, after 5pm. `enough_data` is true only with 20 or more finished calls and two
  buckets of 5 or more; otherwise `buckets` is empty and the UI should hide the section.
- `context_effect`: for vendors called more than once, the first call versus the later calls:
  `quote.later_lower_by_usd`, `negotiate.later_saved_more_usd`, `schedule.first_confirmed_rate` versus
  `later_confirmed_rate`, `first_answer_rate` versus `later_answer_rate`, and how many later calls carried
  `prior_context` (`n_later_with_prior_context`, `share_later_with_prior_context`).
- `recommendations`: ranked list of `{rank, kind, text, score, vendor_key, track, evidence}`. Kinds:
  `call_first` (cheapest most often, answers), `book_first`, `retention_works`, `retention_resists`,
  `time_of_day` (per vendor), `low_answer_rate`, `context_effect`, `time_of_day_overall`. Every sentence
  carries its numbers ("Call BearHug Moving first: answered 25 of 25, cheapest quote 12 of 20 times.").
- `honesty`: where the numbers come from and which one is an estimate.

### Data it reads

`store.list_runs(user_id, limit=max_runs)` then `store.get_run(run_id, user_id)` per run (calls come
with the run), and `store.list_events(user_id, limit=events_limit)`. Timing comes from `calls.started_at`
and `calls.ended_at`, outcomes from `calls.outcome` (dict or JSON string), context from
`calls.prior_context`. Events are used for coverage counts only, so the PostgREST page cap (1000 rows)
only affects `n_events`. On Supabase a 60-run report is about 61 PostgREST requests.

### Prior context

`router.plan.prior_context` says what happened last time with the vendor. `prior_context_from_insights`
says that and what the history says, in at most two sentences, numbers only from the report:

> Last time (Oct 3) BearHug Moving quoted $1,240 flat. Across 6 calls they answered every time and were
> cheapest 4 of 6 times.

For a vendor with no outcome it still reports the pattern ("Across 3 calls they never answered
(voicemail 3). They went to voicemail 3 of 3 times before 11am."). For an unknown vendor it returns
`None`. The router can adopt `prior_context_for(store, user_id, vendor_key, track, insights=report)` in
phase 2 and compute the report once per run instead of once per call.

## Keewano queries

`analysis/keewano_queries.py` holds `KEEWANO_QUERIES`, one entry per question with the tool
(`run_sql_query` or `run_script_query`), the SQL or Lua, the event types and fields it needs, the matching
key in the local report, and a status:

| Status | Meaning |
|---|---|
| `runnable_today` | built-in columns only; answers now (zero rows until ingest works) |
| `runnable_once_ingested` | needs our custom types (ids 2001-2008) registered by the first accepted `send_events` |
| `needs_scalar_events` | needs the scalar event types in `PROPOSED_EVENT_TYPES` (phase 2 schema) |

Queries: `project_activity`, `activity_by_day`, `call_funnel`, `runs_and_gaps` (Lua: runs per user,
fan-out per run, gaps between runs), `failure_modes`, `answer_rate_by_hour`, `quotes_by_vendor`,
`first_quotes_by_vendor`, `savings_by_vendor`, `prior_context_share`.

Findings from Keewano's MCP tool guide (Oct 11), load-bearing for the queries:

- One table `events`, one column per event type, step-function semantics (a value holds until the next
  occurrence), `count(col)` counts occurrences, `occurrence(col) = 1` pins a user's first value, no joins,
  no window functions, no string functions. `user_id` and `event_datetime` are the only non-event columns
  the queries use.
- A string-typed event is a label. `=`, `IN`, `LIKE`, `GROUP BY` and `count(DISTINCT)` work; nothing can
  parse the label. `events/keewano.py` sends each event as one JSON string, so Keewano can count and
  sequence our events but cannot read a quote amount out of them. That is why value questions need
  `PROPOSED_EVENT_TYPES` (vendor_key, track, termination_type, call_duration_seconds, quote_amount_usd,
  saved_usd, confirmed, prior_context_used; ids 2009-2016, additive to the registration list, emitted
  right after `call_terminated`).
- Lua scripts resolve names at upload; an unknown name is an upload error, so the Lua query also waits
  for registration.
- The project visible to the PAT is SpeedHacks26 (`f21f5584-84af-4c5a-88bb-b4cbace12f54`). At probe time
  it held zero events and only built-in types.

`keewano_insights(user_id)` calls the MCP endpoint directly from Python: `POST https://api.keewano.com/mcp`
with header `k-token` (env `KEEWANO_PAT`, fallback `KEEWANO_TOKEN`), JSON-RPC 2.0 `tools/call`, plain JSON
replies, no session handshake required; parsed results come from `result.structuredContent`. It runs the
`runnable_today` and `runnable_once_ingested` queries, records per-query errors instead of raising, stops
if a reply mentions the plan limit, and returns `None` when there is no token, no visible project or the
transport fails. Whole call bounded to 10 seconds.

## Seed data

```
uv run python scripts/seed_demo.py --user-id <uuid> [--runs 60] [--days 120] [--seed 7] [--roster] [--keewano] [--workers N]
uv run python scripts/seed_demo.py --user-id <uuid> --wipe
```

The seeder resolves the store with `server.store.get_store()` (Supabase when `.env` has the keys, else
SQLite). It is deterministic from `--seed`. It generates N runs over the last D days in business hours
(user's zone), 2 to 5 calls per run, realistic requests ("I'm moving to SF next month, handle it", "Get my
internet bill down", "Book a dentist appointment next week", "Cancel the gym membership", "Quotes for a
storage unit"), durations 45 to 190 seconds, a fan-out width of 3 for believable wall clock, failures as
voicemail, no-answer or busy, outcomes from `call.simulate.fixture_outcome` with vendor biases (one mover
consistently cheapest, one dentist always confirmed, one retention line that goes to voicemail in the
morning and concedes more on each repeat call, quotes about 6% lower when the call carried prior
context), transcripts from `fixture_transcript` patched to match the shaped outcome, `prior_context` on
later calls to a vendor (the same sentence `router.plan.prior_context` writes), the full event sequence
per call (`run_requested`, `call_dispatched`, `call_simulated`, `call_connected`, `call_terminated`,
`outcome_extracted`, `run_aggregated`, `ledger_written`) with service-shaped payloads, and ledger rows
through `aggregator.ledger.ledger_rows`.

Roster: if the user has fewer than 5 vendors, or with `--roster`, the 12-vendor demo roster is added
(movers_a..d, storage_quote, storage_quote_b, insurance_quote, dentist, dermatologist,
internet_retention, phone_retention, gym_cancel; `is_demo` true; phones +15550100001 and up). Existing
vendor keys are never modified. Scenarios only pick vendors that exist, so a 5-vendor roster gets moving,
internet and dentist runs.

### Labels

| Table | Tag |
|---|---|
| runs | `request` starts with `[seeded] ` |
| calls | `simulated` true and `error` = `seeded`; outcome `note` names it |
| events | `data.seeded` true (`data.simulated` true on call events; `data.at` is the believable time) |
| ledger | `label` starts with `[seeded] ` |
| vendors | `is_demo` true, key in the roster, phone starts with +1555010 |

`compute_insights` counts these as `n_seeded` and sets `coverage.label`, so the UI can say the report is
demo data.

Only `created_at` on runs and events is backdated after the write (the store stamps now); on SQLite by
UPDATE, on Supabase by an upsert of the same rows. Everything else goes through the Store methods.

### Wipe

`--wipe` deletes only tagged rows for that user, in the order ledger, events, calls, runs, vendors.
SQLite uses direct SQL on the tags; Supabase uses PostgREST deletes filtered on the same tags
(`data->>seeded = true`, `request like '[seeded] %'`, `simulated = true and error = 'seeded'` within the
user's seeded runs, roster keys with +1555010 phones). Untagged rows for the same user, and any other
user's rows, are untouched (covered by `test/test_seed_demo.py`).

### Keewano send

With `--keewano` the seeded event rows (with their backdated `created_at`, which `encode_event` uses as
the Keewano timestamp) go through `events.keewano.send_events` in batches of 100. Each batch prints
accepted or rejected; a rejection never stops the seed. Supabase writes retry on transport drops and check
whether the write landed before re-sending, so a flaky connection cannot duplicate rows.

## Phase 2 wiring

- Route: `GET /api/py/insights` -> `compute_insights(store, user_id)`; optional
  `GET /api/py/insights/keewano` -> `await keewano_insights(user_id)` (may be `None`; return 204 or
  `{"keewano": null}`).
- Page: `/insights`. Show `coverage.label` first, then recommendations, scorecards grouped by track,
  context effect, failure modes, time of day only when `enough_data`.
- Router: replace or extend `router.plan.prior_context` with `analysis.context.prior_context_for`,
  computing the report once per run.
- Keewano schema: add `PROPOSED_EVENT_TYPES` to `events/keewano.py` registration (append-only) and emit
  them from ingest and simulate after `call_terminated`.

## Blocked or partial

- Keewano ingest needs a Project API key (`KEEWANO_TOKEN` now holds it; `KEEWANO_PAT` holds the personal
  token for the MCP server). Until the first accepted registration, every query except `project_activity`
  returns an unknown-column error, which `keewano_insights` records rather than raises.
- Value questions on Keewano (quotes, savings, failure modes) need the scalar event types above. With the
  JSON-string encoding Keewano answers volume, funnel, fan-out and gap questions only.
- `store.list_events` is capped by PostgREST at 1000 rows; the report marks `events_truncated`.
