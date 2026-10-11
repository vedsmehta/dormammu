# Keewano in Dormammu

Status as of 2026-10-11, after the key swap: the integration is in the tree and wired into every
call lifecycle step. Read access through Keewano's MCP works. Ingest works with the Project API
Key now in `.env`: registration of our 16 custom event types was accepted and a two-event check
batch came back `HTTP 202 {"received":2,"accepted":2}`. Section 5 has the exact results and what
to do next; section 6 explains why Postgres stays the system of record.

## 1. Where it sits

```
log_event()  ->  store.insert_event()   Postgres `events`, system of record, always first
             ->  keewano.send_events()  fire-and-forget task, only when KEEWANO_ENABLED and
                                        KEEWANO_TOKEN are set and an event loop is running
```

`events/log.py` never raises and never blocks a call. Sync FastAPI handlers run in a threadpool
without a loop and skip the sink silently; async handlers schedule it. Inside the background task,
a call-scoped event whose data lacks `track`, `vendor_key` or `simulated` gets them from the call
row (`store.get_call`) before encoding; the Postgres row is stored exactly as the caller passed it.
`events/keewano.py` never raises either: any failure returns `False`, logs one warning per distinct
message, and leaves the reason in `keewano.last_error`.

## 2. What Dormammu sends

One Keewano entity per end user: `userId` is `profiles.id`. Every `events` row becomes one blob
event plus zero or more typed companions, all with the row's `created_at` as the timestamp, in one
batch, companions first.

### 2.1 Lifecycle events (blob, dataType string)

The `data` is the JSON encoding of `{"type", "run_id", "call_id", ...row.data}`. Every string
value at any depth is cut to 256 characters before encoding.

| event | wire id | emitted from |
|---|---|---|
| run_requested | 2001 | server/app.py |
| call_dispatched | 2002 | call/dispatch.py |
| call_simulated | 2003 | call/dispatch.py |
| call_connected | 2004 | call/ingest.py, call/simulate.py |
| call_terminated | 2005 | call/ingest.py, call/simulate.py |
| outcome_extracted | 2006 | server/app.py, call/simulate.py |
| run_aggregated | 2007 | server/app.py |
| ledger_written | 2008 | server/app.py |

Data fields per event, as the callers emit them today:

- `run_requested`: `request`, `n_calls`, `vendor_keys`. A retention run adds `kind: "retention"`,
  `as_of`, `ledger_ids`.
- `call_dispatched`: `track`, `vendor_key`, `simulated`, `dial_call_id`.
- `call_simulated`: `reason`, `limit` (Dial concurrency limit hit), `track`, `vendor_key`.
- `call_connected`: `dial_call_id`, `track`. Simulated path: `simulated: true`, `track`.
- `call_terminated`: `dial_call_id`, `track`, `state`, `termination_type`, `duration_seconds`,
  `voicemail`, `transcript_ready`. Simulated path: `simulated: true`, `track`,
  `termination_type: "completed"`, `duration_seconds`.
- `outcome_extracted`: `track`, `outcome` (the extractor's dict, see docs/CONTRACT.md), `simulated`.
- `run_aggregated`: `n_calls`, `n_completed`, `n_failed`, `n_simulated`, `n_live`,
  `wall_clock_seconds`, `talk_seconds`, `sequential_estimate_seconds`, `multiplier`. On an
  aggregation failure: `error`.
- `ledger_written`: `n`, `kinds`, `ledger_ids`. On an insert failure: `n: 0`, `error`.

`termination_type` values: `completed`, `voicemail` (our guard in call/ingest.py, under 15 seconds
or callee never answered), and Dial's `busy`, `no-answer`, `failed`, `canceled`.

### 2.2 Typed companions (dataType uint)

Keewano SQL cannot read inside the blob, so the fields it needs to group and aggregate by are also
sent as small uint events. A companion is emitted only when the source row's data carries the field
(after the call-row enrichment in section 1). Nothing is ever invented: an unknown string or a
non-numeric value produces no companion.

| companion | wire id | from field | value |
|---|---|---|---|
| call_track | 2009 | track | track code |
| call_vendor_key | 2010 | vendor_key | crc32 hash |
| call_simulated | 2011 | simulated | 0 or 1 |
| call_termination | 2012 | termination_type | termination code |
| call_duration_seconds | 2013 | duration_seconds | rounded seconds |
| outcome_amount_usd_cents | 2014 | outcome.amount_usd, else outcome.saved_usd | cents |
| run_wall_clock_seconds | 2015 | wall_clock_seconds | rounded seconds |
| run_multiplier_x100 | 2016 | multiplier | times 100 |

| track | code |
|---|---|
| quote | 1 |
| schedule | 2 |
| negotiate | 3 |

| termination | code |
|---|---|
| completed | 1 |
| voicemail | 2 |
| no-answer | 3 |
| busy | 4 |
| failed | 5 |
| canceled | 6 |

Which companions each lifecycle event produces, given the fields above and the call-row
enrichment: `call_dispatched` and `call_simulated` give track, vendor and simulated;
`call_connected` gives track, vendor and simulated; `call_terminated` gives track, vendor,
simulated, termination and duration; `outcome_extracted` gives track, vendor, simulated and the
amount (quote `amount_usd`, or negotiate `saved_usd`; schedule has no amount); `run_aggregated`
gives wall clock and multiplier. `run_requested` and `ledger_written` have no companions.

The vendor hash is `zlib.crc32(vendor_key) or 1`. Keewano cannot map it back; this repo can:
`uv run python -c "from events.keewano import vendor_key_hash; print(vendor_key_hash('comcast'))"`.
The vendor key string stays in the blob next to it.

Why companions ride right before each measure: Keewano columns are step functions, a value holds
until the next occurrence. One user runs three calls at once, so a duration or an amount must be
preceded, in the same batch, by that call's own track, vendor and simulated flag, or it would be
attributed to whichever call was dispatched last. That is what the enrichment in section 1 is for.

### 2.3 Batch, registration, fallback

`dataSessionId` is one UUID per process, generated at import. `batch` starts at 1 and increments on
every send; Keewano treats `(dataSessionId, batch)` as idempotent, so a restart starts a fresh
session and cannot collide. Today each send carries one row's events, because `log_event`
schedules one task per row. Before the first send of a process the module registers all 16 custom
types at `POST /custom` with `customEventsHash` (crc32 of the list). 200 or 202 means registered;
400, 404, 409 or 422 flips the module to the built-in string id 20 ("Button Click") with
`customEventsHash: 0` and no companions (the blob still carries every field), which is the Lane C
abort rule applied automatically; 401 or 403 stops the send and sets `last_error`.

Ids are append-only. Never reorder `EVENT_TYPES` or `COMPANION_TYPES`, never change a registered
id's type; add new ones at the end.

Catalog numbering: Keewano's `list_event_types` reports our types with `kdb_type_id` equal to the
wire id minus one (`run_requested` 2000, `call_track` 2008, `run_multiplier_x100` 2015). SQL goes
by column name and is unaffected. The report tools (`get_data_table`, filters) take numeric ids and
must use the catalog's numbers.

Truncation caveat: the 256-character cap is applied per string value, and the JSON blob Keewano
receives can be longer than 256 for `outcome_extracted`. Whether Keewano clips it is unverified;
the companions carry the numbers either way, so clipping would only cost blob detail.

## 3. What it gives back

### 3.1 The surface

Keewano's MCP server, connected in this repo's Claude sessions on the personal access token:
`list_projects`, `list_event_types`, `run_sql_query`, `run_script_query` (Lua), `get_data_table`,
and the fixed reports.

| check | result |
|---|---|
| list_projects | SpeedHacks26, id f21f5584-84af-4c5a-88bb-b4cbace12f54 |
| list_event_types | 67 built-in types plus our 16 custom types |
| registration (POST /custom) | accepted |
| ingress (POST /in), check batch | HTTP 202, received 2, accepted 2 |

The SQL model is a single `events` table with one column per event type and step-function
semantics: a column takes the value of its last occurrence and holds it forward. There are no
joins and no string functions. Bound every time window on both sides: the ingest API accepts
caller-set timestamps, so a one-sided window also counts events stamped in the future. Timestamps
come back as unix epoch seconds.

Tool call for every query below:
`run_sql_query(projectId: "f21f5584-84af-4c5a-88bb-b4cbace12f54", sqlQuery: ...)`.

### 3.2 Volumes and funnel

```sql
SELECT date_trunc('day', event_datetime) AS day,
       count(call_dispatched)  AS dispatched,
       count(call_connected)   AS connected,
       count(call_terminated)  AS terminated,
       count(outcome_extracted) AS extracted,
       count(DISTINCT user_id) AS users
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
GROUP BY day ORDER BY day
```

### 3.3 The three questions

Each has the Keewano query over the typed columns, and the Postgres query (Supabase SQL editor,
`calls` table) that cross-checks it from the system of record.

Cheapest vendor per track over time. `min` is the right aggregate for quotes (track 1); for
negotiate (track 3) the column holds savings, so read `max` there.

```sql
-- Keewano
SELECT date_trunc('week', event_datetime) AS week, call_track AS track,
       call_vendor_key AS vendor_hash,
       min(outcome_amount_usd_cents) AS cheapest_cents,
       count(outcome_amount_usd_cents) AS outcomes
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
  AND outcome_amount_usd_cents IS NOT NULL
GROUP BY week, track, vendor_hash ORDER BY week, track, cheapest_cents
```

```sql
-- Postgres cross-check
select date_trunc('week', started_at) as week, track, vendor_key,
       min((outcome->>'amount_usd')::numeric) as cheapest_usd
from calls
where track = 'quote' and outcome->>'amount_usd' is not null
group by 1, 2, 3 order by 1, 2, 4;
```

Outcome distribution per vendor, including voicemail and no-answer. Codes: 1 completed,
2 voicemail, 3 no-answer, 4 busy, 5 failed, 6 canceled.

```sql
-- Keewano
SELECT call_vendor_key AS vendor_hash, call_termination AS termination,
       count(call_termination) AS calls
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
GROUP BY vendor_hash, termination ORDER BY vendor_hash, calls DESC
```

```sql
-- Postgres cross-check
select vendor_key,
       coalesce(termination_type, case when state = 'done' then 'completed' else state end) as outcome,
       count(*) as calls
from calls
group by 1, 2 order by 1, 3 desc;
```

Talk seconds per track and per simulated flag.

```sql
-- Keewano
SELECT call_track AS track, call_simulated AS simulated,
       count(call_duration_seconds) AS calls,
       sum(call_duration_seconds) AS talk_seconds,
       avg(call_duration_seconds) AS avg_seconds
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
GROUP BY track, simulated ORDER BY track, simulated
```

```sql
-- Postgres cross-check
select track, simulated, count(*) as calls,
       round(sum(coalesce(duration_seconds, 0))) as talk_seconds
from calls
where state in ('done', 'failed')
group by 1, 2 order by 1, 2;
```

How the step-function model makes these correct: `GROUP BY call_track, call_vendor_key` groups
each occurrence of a measure under the last-known track and vendor at that instant, and the
companions for that call are emitted in the same batch immediately before the measure, so the
last-known values are that call's own. `count(<column>)` counts occurrences of that column, which
is exactly one per call for `call_termination` and `call_duration_seconds`.

A run-level bonus from the same data:

```sql
SELECT date_trunc('day', event_datetime) AS day,
       count(run_multiplier_x100) AS runs,
       avg(run_multiplier_x100) AS multiplier_x100,
       avg(run_wall_clock_seconds) AS wall_clock_seconds
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
GROUP BY day ORDER BY day
```

## 4. Where prior context comes from today

Keewano is not in the dispatch path. At run creation `server/app.py` fills each call row's
`prior_context` through `router.plan.prior_context(store, user_id, vendor_key, track)`, which calls
`store.last_outcomes_for_vendor(user_id, vendor_key, limit=3)`: the newest `calls` rows for this
user and vendor with a non-null outcome, joined to `runs` for the user filter. The first row that
yields a sentence wins, and that sentence goes into the agent's instructions. Retention runs build
the sentence from the ledger row instead ("Last year you quoted ..."). Both read Postgres.

Scope, from PRD section 6 and the epic's Keewano scope note: Keewano analyzes and surfaces patterns
and supplies context; it does not rewrite prompts. Per-call contextual retrieval into the agent's
working instructions is in scope and is served from Postgres. Nobody auto-edits the
`router/vendor_categories.json` templates. Say both halves plainly in the pitch.

## 5. Status and next steps

Verified today, in this order:

1. With the personal access token (now `KEEWANO_PAT`): MCP read access works, ingest returns
   `403 {"statusCode":403,"message":"Invalid Api Key"}` on both `/custom` and `/in`. The module
   now names that cause in one warning and in `last_error`.
2. With the Project API Key as `KEEWANO_TOKEN`: `uv run python scripts/keewano_check.py` printed
   `registration = ok (custom ids 2001-2016 accepted)`, `last response = HTTP 202
   {"received":2,"accepted":2}`, exit 0. `list_event_types` then listed all 16 custom types with
   the right names and data types.
3. Ingest is asynchronous: the two accepted events were not yet visible to `run_sql_query` right
   after the 202. Re-run the count below a few minutes later.

```sql
SELECT count(DISTINCT user_id) AS users, count(*) AS events,
       count(call_track) AS companions, count("Button Click") AS checks
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-10-31'
```

Next steps:

1. Restart the Python API so it reads the new `.env`. Each process registers once and starts a
   fresh `dataSessionId`.
2. Run one request. Then the funnel query (3.2) should return a row and the three queries (3.3)
   should return the run's vendors.
3. The MCP server keeps using the personal token from its own config. Do not put the project key
   there.

If `keewano_check.py` ever prints a 401 or 403 again, `KEEWANO_TOKEN` has been swapped back to a
personal token: Keewano console, project SpeedHacks26, Settings > Project > API Keys. If it prints
`registration = refused, using built-in id 20`, the Lane C abort rule has applied itself: blobs
still arrive under "Button Click" with the type in the data, companions are dropped, and the
Postgres queries in 3.3 answer the questions.

## 6. The abort rule and what is lost

BUILD-PLAN section 6, Lane C: at most 15 minutes on custom-event registration (`customEventsHash`,
`POST /custom`, codegen). If it fights back, log under built-in ids with the detail in `data` and
stop. The module does that fallback by itself on a 400, 404, 409 or 422. Registration succeeded on
the first try with the right key, so the rule did not fire.

Postgres `events` is the system of record. `GET /api/py/events?limit=50` reads it, the dashboard
reads it, and `prior_context` reads `calls`. Keewano receiving nothing, something late, or
something clipped loses nothing: it is the analysis surface on top, and every event it would have
received is already in Postgres.
