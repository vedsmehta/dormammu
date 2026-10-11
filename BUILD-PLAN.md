# Dormammu: Build Plan

**For:** Fable, executing end-to-end across parallel worktree lanes.
**Written:** 12:45 PDT, Oct 11 2026. **Record at 15:15. Submit by 15:45. Hard cutoff 16:00.**
**Stack:** **Python core (already built, keep it) + Next.js/Supabase web layer.** See §0 first.
**Deliverable:** two things, not one. A recorded video of one plain-language request fanning out
into N simultaneous real phone calls. And a real product behind it: auth, a durable event
database, and data that survives the demo, because **the GitHub repo is being shared with judges.**

This plan has been through an engineering review. 21 findings were raised and resolved; each
resolution is marked `[review N<letter>]` at the point it applies. §9 is the review record.

**Read §0 before anything else.** §0 reconciles this plan with the code that already exists on
`origin/main`. Then §1 and §2 before writing code (§2 is API ground truth and is correct
regardless of language). §3 is the schema. §4 is the component contract. §5 is the lane
assignment. §6 is the timeline with abort gates. §12 is added scope.

---

## 0. Repo reality: read this first

**§1 through §11 were drafted against a greenfield TypeScript repo. That was wrong.** The local
checkout was stale; `origin/main` already has working, merged, tested Python. Nothing below
is a reason to delete that code.

### What already exists and must be kept

| Piece | File | State |
|---|---|---|
| **Concurrent fan-out** | `call/dispatch.py` → `run_calls()` | **Done.** `asyncio.gather` over an `asyncio.Semaphore`, bounded by `MAX_CONCURRENT_CALLS` (default 10), per-call timeout, exceptions captured into a failed `CallResult`. **Do not rebuild the fan-out. It is the thesis and it already works.** |
| **Frozen data contract** | `call/models.py` → `CallRequest` / `CallResult` | **Done and frozen.** `CallRequest{to, task, fields, caller_name}`, `CallResult{room, to, status, success, summary, details, transcript, duration_seconds, error}`. A field addition is a 2-line change proposed to the group, never a silent fork. |
| **Result persistence seam** | `call/models.py` → `write_result` / `read_result` / `result_path` | Done, file-based JSON in `.calls/` with a tmp-then-rename atomic write. **This is the seam Supabase goes behind.** |
| **SMS** | `sms/messenger.py` | Done, already on Dial. |
| **Voice call placement** | `call/agent.py`, `run_call()` in `dispatch.py` | Done **on LiveKit**, being migrated to Dial. This is the team's Risk #1. |
| **Tests** | `test/test_call.py`, `test/test_sms.py`, `test/conftest.py` | Exist. pytest + pytest-asyncio, `asyncio_mode = "auto"`, a `live` marker for real-API tests. |
| **Config** | `settings.py` | `load_env()`, `env()`, `results_dir()`. Python/uv project, ruff at line-length 110. |

Team context: `docs/epic-mvp-oct11.md`, tracking issue #4, child issues #5 router, #6 test-lines,
#7 aggregator+ledger, #8 dashboard, #9 Keewano. Four people are working these concurrently.

### The three decisions that reshape this plan

1. **Python core stays, Next.js + Supabase is the web layer only.** `call/`, `sms/`, `settings.py`
   and the `uv` project are untouched except for the Dial migration. The TypeScript file specs in
   §4 apply to the **web layer**; the call-placing and ingest specs map onto the Python modules
   per the table below.
2. **Supabase goes behind `write_result` / `read_result`.** Swap those two function bodies in
   `call/models.py` to hit Postgres instead of `.calls/*.json`. Every caller, including
   `dispatch.py`'s `wait_for_result` poll loop, is unchanged. The frozen contract holds. That is
   roughly a 20-line change at one seam and it buys durability, auth and Realtime. Keep
   `result_path` working so the file path remains a fallback.
3. **Risk #1 is already resolved. Do not spend 30-45 minutes on the DTMF spike.** From the
   OpenAPI spec (§2.1):
   - **The agent cannot send DTMF.** There is no outbound-keypress capability anywhere in the
     API. DTMF appears only in the self-hosted audio protocol, receive-only. Any demo built on
     pressing menu keys will fail.
   - **`transferTo` makes it unnecessary.** It makes the agent "wait for a real human (riding out
     hold music and IVR menus)" and then cold-transfer. Dial navigates menus itself.
   - So the epic's Definition of Done item 1, "successfully navigating at least one DTMF menu",
     **is not achievable as written** and should be restated in terms of `transferTo`.
   - Also: every path is `/api/v1/...` (docs pages showing `/v1/numbers` 404), and `voiceGender`
     is per-call despite a docs page claiming it is number-locked.

### How §3 and §4 map onto the real tree

| §4 spec | Where it actually lives |
|---|---|
| `lib/types.ts` | **Already exists** as `call/models.py`. Do not create a parallel contract. The web layer gets a thin TS mirror of `CallResult`, generated from the Supabase schema, never hand-maintained. |
| `lib/dial.ts`, `mapDialState` | `call/dial_client.py` + the `run_call()` rewrite. `mapDialState` becomes a Python function mapping Dial status onto the existing `CallStatus` literal (`pending/answered/completed/no_answer/failed/timeout`), which already covers it. |
| `lib/ingest.ts` | The result-collection path in `dispatch.py`. The **voicemail guard `[review 17A]` goes here** and is still required. |
| `lib/router.ts` | `router/` package, issue #5. Plan cache `[review 2A]` and never-throw `[review 6A]` both still apply. |
| `lib/extract.ts` | Fills `CallResult.details` / `.summary`. **`[review 13A]` still applies and is still the most dangerous bug: wait for the transcript to exist, do not extract on hangup.** |
| `lib/aggregate.ts` | `aggregator/` package, issue #7. Honest-multiplier rules (§4.9) still apply. |
| `lib/simulate.ts` | Still needed `[review 18A]`. Writes scripted `CallResult`s through `write_result`. |
| §3 schema | Still correct, with `calls` reshaped to hold the frozen `CallResult` fields. Add the `ledger` and `profiles.cohort` tables from §12. |
| `app/**`, auth, MCP | Net-new Next.js, issue #8. Unchanged from §4.10-§4.12. |

### Which review findings survive the stack change

**Still apply, unchanged in substance:** 2A plan cache, 4A MCP bearer auth, 6A router never
throws, 7A phone numbers out of git (now the `vendors` table), 8A one state-mapping function,
11A stop polling when terminal, **13A extraction waits for the transcript**, 14A monotonic state,
**17A voicemail guard**, 18A labeled simulated rows, and the §4.9 honest-metrics rules.

**Changed or moot:** 3A and 9A are now free, because one row per call and the atomic
`UPDATE … RETURNING` claim come with Postgres (§3.3) once decision 2 lands. 1A and 15A are
already true: the existing design polls (`wait_for_result`) and has no webhook. 5A is satisfied
by there being one `run_call` path. 10A is moot with no webhook. 12A and 21A are superseded by
§5 and the team's own issue split.

### Lane mapping to the team's issues

| §5 lane | Issue | Note |
|---|---|---|
| A, critical path | Risk #1 Dial migration | Now much smaller: `transferTo`, no DTMF spike |
| A | #5 router, #7 aggregator | Build against mocked `CallResult`, as the epic already says |
| B, auth + MCP | #8 dashboard | Next.js + Supabase + the Context MCP calendar (§12.4) |
| C, events | #9 Keewano | Unchanged, including the 15-minute abort |
| D, pure functions | #5/#7 internals + tests | pytest, not vitest. Same four test targets (§4.14) |

**Fable: apply §0 and §12 as deltas. Do not rewrite §1-§11 to change the language; read them for
the architecture, the review findings, and the API ground truth, and implement against the real
tree.**

---

## 1. What this is

One request in ("I'm moving to SF next month, handle it"), N phone calls out at the same time,
one combined result back. The fan-out is the entire thesis. Every competitor found is
single-purpose and sequential (PRD §7); nothing else dispatches N concurrent calls across
arbitrary phone-gated tasks and aggregates them.

```
                        ┌──────────────────────────────────────────┐
  "I'm moving to SF"    │              ROUTER                      │
  ───────────────────▶  │  Claude → CallPlan[], one per target     │
   (authenticated user) │  + prior context from the event log      │
                        └────────────────┬─────────────────────────┘
                                         │
                   ┌─────────────────────┼─────────────────────┐
                   ▼                     ▼                     ▼
            ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
            │  CALL 1     │      │  CALL 2     │      │  CALL N     │
            │  Dial       │      │  Dial       │      │  Dial       │  ← all at once
            └──────┬──────┘      └──────┬──────┘      └──────┬──────┘
                   │                     │                     │
                   │  Queued→Ringing→In-Progress→Terminated     │
                   │  client /tick drives ingest, Realtime      │
                   │  pushes row updates to the dashboard       │
                   └─────────────────────┼─────────────────────┘
                                         ▼
                        ┌──────────────────────────────────────────┐
                        │   EXTRACTOR   transcript → Outcome       │
                        │   (claimed atomically, §4.7)             │
                        └────────────────┬─────────────────────────┘
                                         ▼
                        ┌──────────────────────────────────────────┐
                        │   AGGREGATOR  normalize + rank           │
                        └────────────────┬─────────────────────────┘
                                         ▼
          ┌──────────────────────────────────────────────────────────┐
          │  EVENT LOG: Postgres `events`, the system of record      │
          │  every dispatch, turn, outcome, aggregate                │
          │        │                                                 │
          │        └──▶ Keewano (secondary sink, for MCP analysis)   │
          │  read back at dispatch time as prior context ────────────┤
          └──────────────────────────────────────────────────────────┘
```

### Tracks

| Track | Objective | Outcome shape |
|---|---|---|
| `quote` | Get a price for a service | `{ amountUsd, unit, caveats[] }` |
| `schedule` | Book a slot, calendar-aware | `{ slotIso, confirmed }` |
| `negotiate` | Lower or cancel a bill | `{ action, savedUsd, effectiveDate }` |

Vendor identity lives on the `calls` row (`target_name`), never inside `Outcome`. One
authoritative place, so the extractor is never invited to hallucinate a vendor name.

---

## 2. Ground truth on the APIs

Pulled from `https://docs.getdial.ai/openapi.json` directly. **The prose docs pages contradict
the spec in three places and the spec wins.** Trust this section over anything on the docs site.

### 2.1 Dial

Base `https://api.getdial.ai`. Auth `Authorization: Bearer sk_live_...`.

**Every path is prefixed `/api/v1`.** Docs pages showing `/v1/numbers` are wrong and will 404.

| Operation | Endpoint |
|---|---|
| Place call | `POST /api/v1/calls` |
| Get call (transcript) | `GET /api/v1/calls/{id}` |
| Stop call | `POST /api/v1/calls/{id}/stop` |
| List / provision numbers | `GET` / `POST /api/v1/numbers` |
| Connect Context MCP | `POST /api/v1/context-mcps` |
| Account + live limits | `GET /api/v1/account` |
| Send / reply SMS | `POST /api/v1/messages`, `.../{id}/reply` |

**There is no agent object.** No registry, no `agent_id`. The agent *is* the
`outboundInstruction` string on the call request. "N call scripts" means N strings.

```ts
POST /api/v1/calls {
  to: string,                        // E.164, required
  outboundInstruction: string,       // required. this IS the agent
  fromNumber?: string,               // E.164 | id | nickname (exactly one of this/fromNumberId)
  fromNumberId?: string,
  language?: string,                 // BCP-47; omit to auto-detect from country prefix
  voiceGender?: "male" | "female",   // PER CALL. default female
  maxCallDurationSeconds?: number,   // 60..3600. free accounts hard-capped at 300
  transferTo?: string
}
```

Set `maxCallDurationSeconds: 180` on every demo call, so a chatty answerer cannot run a call
past the take.

Status lifecycle, forward-only, **can skip states**:

```
Queued ──▶ Ringing ──▶ In-Progress ──▶ Terminated
   │          │             │              │
   └──────────┴─────────────┴──────────────┘  any state can jump straight to Terminated
                                              terminationType ∈ completed | busy
                                                | no-answer | failed | canceled
```

Spec findings that change the design:

1. **`transferTo` rides out hold music and IVR menus to reach a real human, then cold-transfers.**
   Dial solves IVR navigation. No DTMF needed. Hold in reserve for a real-vendor shot.
2. **`transcript` is null until the call ends**, and transcription is **async after hangup**.
   This is the subject of `[review 13A]` and §4.7. Getting it wrong silently empties every
   result column.
3. **No `recordingUrl` exists anywhere in the spec.** No API path to call audio. Demo audio must
   come from screen + system-audio capture, decided before filming.
4. **`voiceGender` is per-call**, contradicting the docs page that claims voice is number-locked.
   Different vendor personas off one number.

Webhook events, complete list: `message.received`, `call.status_changed`, `call.ended`,
`call.transcribed`, `number.status_changed`, `webhook.ping`. **There is no `call.started`,** and
**there is no documented `"*"` wildcard** despite examples using one.

**We are not using webhooks.** `[review 15A]` See §4.6.

**SMS is raw primitives, not an agent.** No conversation state, no SMS agent object. A multi-turn
SMS agent is ours to build. Out of scope today (§7).

### 2.2 Supabase: the persistence layer

One dependency for Postgres, Auth, and Realtime. It replaces Upstash Redis entirely: everything
the Redis design needed locks and `SET NX` for, Postgres does atomically in a single statement
(§3.3). Free tier, self-serve, and the data survives the demo, which is the point.

- `@supabase/supabase-js` and `@supabase/ssr` for Next 15 App Router cookie-based sessions.
- **Row Level Security on every table**, from the first migration. Not a later hardening pass:
  the repo is being shared with judges, and RLS-off is the first thing a technical judge checks.
- The **service-role key is server-only**. It bypasses RLS. It must never appear in a client
  component, a `NEXT_PUBLIC_` variable, or anything that reaches the browser.

### 2.3 Keewano: secondary sink

Ingest: `POST https://api.keewano.com/event/ingress/v1/json/in`, header `k-token: <API key>`.

```json
{
  "dataSessionId": "<uuid>",     // batch grouping, NOT a product session
  "batch": 0,                    // monotonic per dataSessionId; (session,batch) is idempotent
  "customEventsHash": 0,
  "userId": "<uuid>",
  "sdkVersion": "0.0.0",
  "events": [{ "eventTypeId": 2001, "timestamp": 1760000000, "data": "..." }]
}
```

`eventTypeId` < 2000 is built-in; >= 2000 is custom and **requires prior registration** via
`POST /json/custom` with a matching `customEventsHash`. The official route is a codegen step
(`@keewano/codegen`) whose CLI invocation is not publicly documented. **Time-boxed with a hard
abort** (§6, Lane C).

String values in event data are capped at **256 characters**. Truncate before sending; a
transcript blows this instantly.

Retention is undocumented. **This no longer matters:** Postgres `events` is the system of record
and Keewano is a sink we ship to for its MCP analysis surface. Nothing is lost if Keewano drops it.

Entity model: one entity per end user (`userId` = our `profiles.id`). Both stated goals fall out
of one sequence, long-term user context accumulates across runs and gaps in usage, and per-call
analysis comes from filtering that sequence on `callId` in the event data.

MCP: Keewano runs an "MCP for Agents" server whose tool list is not published; docs defer to live
`/mcp` discovery. Treat querying as interactive-only. Nothing in the product depends on it.

Free access: sign up at keewano.com, send `#SpeedHacks` in the in-app chat to unlock MCP and
remove limits, then Settings → Project → API Keys.

### 2.4 Anthropic: three corrections

These are wrong in most agent-written code and each costs ten minutes to discover:

1. **There is no "JSON mode."** No `response_format`. Use **tool use**, or structured outputs via
   `output_config: { format: {...} }`. The older top-level `output_format` is deprecated.
2. **A Zod schema is not a tool definition.** `input_schema` must be JSON Schema. Use
   `betaZodTool` from `@anthropic-ai/sdk/helpers/beta/zod`, or `zodToJsonSchema`. Pass the single
   matching union member for the track, never the whole discriminated union. Set `strict: true`
   and `additionalProperties: false`. Parse tool inputs with `JSON.parse`, never string-matching.
3. Model id is `claude-sonnet-5` for both the router and the extractor.

### 2.5 Next.js 15 and Vercel: four things that bite

1. **`params` is a Promise.** `app/api/runs/[runId]/route.ts` and `app/runs/[runId]/page.tsx`
   must `await params`. This is the most common Next 15 mistake and it fails at **build** on
   Vercel, not locally in dev, which is the worst place to find it.
2. **The polled route will be served from cache and the dashboard will freeze on camera.** Add
   `export const dynamic = "force-dynamic"`, return `Cache-Control: no-store`, and fetch with
   `{ cache: "no-store" }` on the client.
3. **`create-next-app@latest` scaffolds Tailwind v4.** No `tailwind.config.js`; the CSS entry is
   `@import "tailwindcss"`. Writing v3's `@tailwind base/components/utilities` or a v3 config
   gives a completely unstyled dashboard.
4. **Set `maxDuration = 60`** on the dispatch route. Dispatching 5 calls plus a router Claude
   call exceeds the 10s default and truncates the fan-out mid-flight.

Iterate with `next dev` locally and deploy to Vercel for the real thing. Deploying on every
change costs 60-120s per iteration. Also: **disable Vercel Deployment Protection**, which is on
by default for new projects and returns a 401 HTML challenge to anything that is not a logged-in
browser.

### 2.6 Concurrency: resolve this first

Account-level concurrency **is not in the OpenAPI spec**. The only published figure is a
docs-page claim of **2 concurrent calls on free accounts**, returning `429 call_limit_reached`.

Two signals say the account is on the free tier: the spec's note that free accounts are capped at
300s per call, and the absence of any billing history. **So the expected answer is 2, not N.**

**First action, before any other code:** `GET /api/v1/account`, print `limits`. Then:

- `limits` allows >= 5 → all five calls are real.
- `limits` shows 2 → **the simulated-row path covers it** `[review 18A]`, and it is built in
  Lane A regardless of what this returns. Adding credit may raise the ceiling; the billing
  endpoints exist in the spec but whether credits lift *concurrency* rather than just a minute
  balance is unverified. Try it, do not depend on it.

This is why 18A matters: the concurrency answer stops being load-bearing.

---

## 3. Database

Supabase Postgres. One migration file, `supabase/migrations/0001_init.sql`. **RLS on from the
first line.**

### 3.1 Schema

```sql
-- profiles: one row per authenticated user
create table profiles (
  id          uuid primary key references auth.users on delete cascade,
  display_name text,
  phone       text,                      -- the user's own number, for the agent to reference
  prefs       jsonb not null default '{}',  -- budget ceiling, preferred times (Context MCP)
  created_at  timestamptz not null default now()
);

-- vendors: the call roster. PHONE NUMBERS LIVE HERE, NEVER IN GIT.  [review 7A]
create table vendors (
  id         uuid primary key default gen_random_uuid(),
  owner_id   uuid not null references profiles(id) on delete cascade,
  key        text not null,              -- "movers_a", what the router emits
  name       text not null,              -- "Pack & Go Movers"
  track      text not null check (track in ('quote','schedule','negotiate')),
  phone      text not null,              -- E.164
  is_demo    boolean not null default false,
  unique (owner_id, key)
);

create table runs (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  request      text not null,
  status       text not null default 'dispatching'
                 check (status in ('dispatching','running','complete','failed')),
  created_at   timestamptz not null default now(),
  completed_at timestamptz
);

create table calls (
  id               uuid primary key default gen_random_uuid(),
  run_id           uuid not null references runs(id) on delete cascade,
  vendor_id        uuid references vendors(id) on delete set null,
  track            text not null,
  target_name      text not null,        -- authoritative vendor label
  target_phone     text not null,
  objective        text not null,
  prior_context    text,                 -- injected from the event log at dispatch
  dial_call_id     text unique,
  state            text not null default 'planned',
  state_rank       int  not null default 0,   -- monotonic guard  [review 14A]
  termination_type text,
  transcript       text,
  outcome          jsonb,
  simulated        boolean not null default false,  -- [review 18A]
  error            text,
  started_at       timestamptz not null default now(),
  ended_at         timestamptz
);
create index on calls (run_id);
create index on calls (dial_call_id);

-- events: THE SYSTEM OF RECORD. Durable, ours, outlives the demo.
create table events (
  id         bigserial primary key,
  user_id    uuid not null references profiles(id) on delete cascade,
  run_id     uuid references runs(id) on delete cascade,
  call_id    uuid references calls(id) on delete cascade,
  type       text not null,
  data       jsonb not null default '{}',
  created_at timestamptz not null default now()
);
create index on events (user_id, created_at desc);
create index on events (call_id);

-- plan_cache: keeps the demo's first beat instant  [review 2A]
create table plan_cache (
  request_hash text primary key,
  plans        jsonb not null,
  created_at   timestamptz not null default now()
);
```

### 3.2 Row Level Security

```sql
alter table profiles   enable row level security;
alter table vendors    enable row level security;
alter table runs       enable row level security;
alter table calls      enable row level security;
alter table events     enable row level security;
alter table plan_cache enable row level security;   -- no policy: server-role only

create policy "own profile" on profiles
  for all using (id = auth.uid());

create policy "own vendors" on vendors
  for all using (owner_id = auth.uid());

create policy "own runs" on runs
  for all using (user_id = auth.uid());

create policy "own calls" on calls
  for all using (exists (
    select 1 from runs where runs.id = calls.run_id and runs.user_id = auth.uid()));

create policy "own events" on events
  for select using (user_id = auth.uid());
```

Writes to `calls` and `events` happen from server routes using the **service-role** client, which
bypasses RLS. The policies above are what protect the browser-side reads the dashboard performs.

Enable Realtime on `calls` and `runs` so the dashboard receives pushed row updates:

```sql
alter publication supabase_realtime add table calls;
alter publication supabase_realtime add table runs;
```

### 3.3 The three concurrency problems Postgres solves for free

The Redis design needed a lock, a `SET NX` claim, and a rank comparison in application code.
Each is one statement here, and each is atomic.

**Monotonic state, never moves backwards** `[review 14A]`. A late or redelivered status update
cannot revert a finished call from `done` back to `talking` on camera:

```sql
update calls set state = $2, state_rank = $3, termination_type = $4, ended_at = $5
 where id = $1 and state_rank < $3;
-- 0 rows affected = a staler update arrived late. Correct outcome: ignore it.
```

Rank: `planned 0, dialing 1, ringing 2, talking 3, extracting 4, done 6, failed 6`. Terminal
states are absorbing.

**Atomic extraction claim** `[review 9A]`. Exactly one worker extracts a given call, no matter
how many ticks race:

```sql
update calls set state = 'extracting', state_rank = 4
 where id = $1 and outcome is null and state <> 'extracting'
 returning id;
-- no row returned = someone else already claimed it. Return without extracting.
```

**Per-call writes, no locks** `[review 3A]`. One row per call means concurrent updates to
different calls cannot interfere. The original single-JSON-blob design lost four of five
`dialCallId` writes on every dispatch, because all five read the same snapshot before any wrote.

---

## 4. File-by-file contract

```
dormammu/
├── supabase/migrations/0001_init.sql      §3
├── middleware.ts                          auth session refresh
├── app/
│   ├── page.tsx                           landing: signed out → sign in; signed in → runs
│   ├── login/page.tsx                     magic-link form
│   ├── auth/callback/route.ts             code exchange
│   ├── onboarding/page.tsx                seed vendors + prefs on first login
│   ├── runs/[runId]/page.tsx              THE DEMO SCREEN
│   └── api/
│       ├── runs/route.ts                  POST dispatch · GET list
│       ├── runs/[runId]/route.ts          GET run state
│       ├── runs/[runId]/tick/route.ts     POST drive ingest + extraction
│       └── mcp/route.ts                   Context MCP, bearer-guarded
├── lib/
│   ├── types.ts                           Zod, the shared contract
│   ├── supabase/{server,client,service}.ts
│   ├── dial.ts                            typed client + mapDialState
│   ├── ingest.ts                          the single state-ingestion path
│   ├── router.ts                          request → CallPlan[]
│   ├── instructions.ts                    per-track templates
│   ├── extract.ts                         transcript → Outcome
│   ├── aggregate.ts                       Outcome[] → ranked view
│   ├── events.ts                          log() → Postgres, then Keewano
│   ├── keewano.ts                         secondary sink + priorContext
│   └── simulate.ts                        scripted rows  [review 18A]
├── scripts/
│   ├── preflight.ts                       limits, numbers, keys, seed plan cache
│   └── smoke.ts                           place ONE real call, print transcript
└── test/
    ├── extract.test.ts
    ├── aggregate.test.ts
    ├── instructions.test.ts
    └── state.test.ts                      mapDialState + rank monotonicity  [review 8A]
```

### 4.1 `lib/types.ts`: the shared contract

**Lane A writes this first and pushes it before anything else.** Lanes B, C and D all import
from it; nobody duplicates a shape.

```ts
export const Track = z.enum(["quote", "schedule", "negotiate"]);

export const CallState = z.enum([
  "planned","dialing","ringing","talking","extracting","done","failed",
]);
export const STATE_RANK: Record<z.infer<typeof CallState>, number> = {
  planned: 0, dialing: 1, ringing: 2, talking: 3, extracting: 4, done: 6, failed: 6,
};
export const isTerminal = (s) => s === "done" || s === "failed";

export const CallPlan = z.object({
  vendorKey: z.string(),          // from the vendors table. NEVER a raw phone number
  track: Track,
  objective: z.string(),
  voiceGender: z.enum(["male","female"]).default("female"),
});

export const Outcome = z.discriminatedUnion("track", [
  z.object({ track: z.literal("quote"),
    amountUsd: z.number().nullable(), unit: z.string().nullable(),
    caveats: z.array(z.string()).default([]) }),
  z.object({ track: z.literal("schedule"),
    slotIso: z.string().nullable(), confirmed: z.boolean() }),
  z.object({ track: z.literal("negotiate"),
    action: z.enum(["reduced","canceled","refused","none"]),
    savedUsd: z.number().nullable(), effectiveDate: z.string().nullable() }),
]);
```

### 4.2 `lib/dial.ts`

Hand-rolled REST client, no SDK. The surface is small and a version surprise at 15:00 is not
worth the import.

```ts
placeCall(p): Promise<{ id, status }>    // Idempotency-Key: <uuid> on EVERY call
getCall(id): Promise<DialCall>
listNumbers(): Promise<DialNumber[]>
getAccount(): Promise<{ limits: Record<string, number> }>
mapDialState(status): { state: CallState; rank: number }   // unit-tested
```

**`mapDialState` is one exported function** `[review 8A]`, because three places need it and a
prose table gets implemented twice. It takes the whole `status` object, since the decision needs
`state` and `terminationType` together:

```
Queued                        → dialing
Ringing                       → ringing
In-Progress                   → talking
Terminated + completed        → extracting   (then done, after extraction)
Terminated + busy|no-answer|failed|canceled → failed
Terminated + terminationType null → failed   ← status_changed sends Terminated bare
Unknown                       → leave unchanged
```

That second-to-last row is the hole the original table had: `call.status_changed` can deliver
`Terminated` with no `terminationType` at all.

### 4.3 `lib/ingest.ts`: the single state-ingestion path

**One function. Every caller is thin.** `[review 5A]`

```ts
ingestCallUpdate(dialCall: DialCall): Promise<void>
  1. find calls row by dial_call_id            (no-op if absent)
  2. { state, rank } = mapDialState(dialCall.status)
  3. voicemail guard  [review 17A]:
       if Terminated/completed AND (no transcriptTurns turn attributed to the callee
                                    OR dialCall.duration < 15)
         → state = "failed", termination_type = "voicemail"
  4. monotonic UPDATE (§3.3), a stale update affects 0 rows and is dropped
  5. store transcript if present and not already stored
  6. events.log(call_connected | call_terminated)
```

The **voicemail guard is a correctness requirement, not polish.** Without it: a phone on silent
sends the call to voicemail, the agent talks to the greeting, the call ends
`Terminated/completed`, the extractor reads a one-sided transcript and invents a plausible dollar
figure. A price appears on screen that no human ever said, in a submitted video, in the exact
regulatory category where DoNotPay took an FTC order (§8.4).

### 4.4 `lib/router.ts`

`plan(request, userId): Promise<CallPlan[]>`

One Claude call, `claude-sonnet-5`, **tool use** (§2.4, there is no JSON mode). The prompt gets
the user's `vendors` rows and may only emit `vendorKey` values from that list. Phone numbers are
resolved server-side at dispatch. The model never sees or emits a number, so it cannot invent
one, and an invented key fails a lookup instead of dialing a stranger.

Then `priorContext(userId, vendorKey, track)` fills `prior_context` from the event log. **This is
the closed loop:** what we learned last time shapes the instruction before the call is placed.

Hard cap at 5 calls per run regardless of what the model returns.

**Plan cache, pre-seeded** `[review 2A]`. The router's Claude call is 2-4s and it sits directly
between hitting enter and the phones ringing, the one beat the whole video rests on. Four
seconds of nothing reads as broken.

```ts
key = sha256(request.trim().toLowerCase())
hit = select plans from plan_cache where request_hash = key   // ~30ms
if (hit) return withPriorContext(hit)
plans = await callClaude(request)
upsert plan_cache
```

`scripts/preflight.ts` seeds the cache with the **exact** demo sentence. The router is real and
runs for any other input; the demo input is simply warm. Say it that way if asked.

**Never throws** `[review 6A]`. A throw, a Zod failure, or a zero-length result all fall back to
a default roster for the detected track. A run always dispatches at least one call. Failure modes
here are many: 429, 529 overload, malformed JSON inside a valid tool-use block, a mistyped key.

### 4.5 `app/api/runs/route.ts`: the dispatch

```
POST /api/runs  { request }
  export const maxDuration = 60;                      // §2.5

  1. require an authenticated user (service client for writes)
  2. plans = await router.plan(request, userId)       // cached on the demo path
  3. insert runs row; insert one calls row per plan, state "planned"
  4. events.log(run_requested)
  5. dispatch with Promise.allSettled. THE FAN-OUT
       per plan: resolve phone from vendors
                 placeCall({ ..., maxCallDurationSeconds: 180 })
                 UPDATE calls SET dial_call_id, state 'dialing'   ← own row, no contention
                 events.log(call_dispatched)
       on 429 call_limit_reached → hand to simulate.ts  [review 18A]
         NOT state "failed": a red row is worse than an honest labeled one
  6. AWAIT the allSettled, then return { runId }
```

**Step 6 must await.** `Promise.allSettled` over 5 `placeCall`s is 1-2s. Returning before it
settles lets Vercel freeze or reclaim the instance with requests unsent, and the fan-out silently
becomes one or two calls. `maxDuration` does not help: it caps a function that is awaiting, not
one that already responded. If you genuinely need to return earlier, use `waitUntil` from
`@vercel/functions`, but just await it.

`Promise.allSettled`, never `Promise.all`: one bad number must not take down the other four.

### 4.6 `app/api/runs/[runId]/tick/route.ts`: ingest, client-driven

**No webhooks** `[review 15A]`. Deleting them removed HMAC verification over a raw body, the
5-minute timestamp window, the `whsec_` secret, event-ID dedupe, the reverse-lookup index,
registration, and a dispatch race: `placeCall` returns an id, and Dial can fire a status change
before our own write of that id lands, so the handler looks it up, finds nothing, and drops the
event. For a bad number whose only event is an immediate `Terminated/failed`, that row sits at
`dialing` for the entire take.

Serverless has no long-lived process to poll from, so the client drives the tick and the server
does the work:

```
POST /api/runs/{runId}/tick     (dashboard calls this every 2s while any call is live)
  await params                                        // §2.5
  1. select calls where run_id = $1 and state not terminal
  2. Promise.allSettled(getCall(dial_call_id)) for each
  3. ingestCallUpdate(each)
  4. for each call now awaiting extraction → §4.7
  5. if all calls terminal → runs.status = 'complete', events.log(run_aggregated)
  6. return the assembled run (so the UI has a render source even if Realtime is down)
```

### 4.7 `lib/extract.ts`, and what triggers it

**Extraction waits for the transcript to exist** `[review 13A]`. This was the single most
dangerous bug in the original plan. `call.ended` carries only `transcriptAvailable: boolean`;
transcription finalizes asynchronously *after* hangup. Extracting on hangup reads `transcript:
null`, the null-safe path writes an all-nulls outcome and marks the call `done`, and every result
column on the demo screen reads blank with no error anywhere. Discovered at playback.

So the trigger is data, not an event:

```
in the tick (§4.6), for each call with state 'extracting':
  1. dialCall = await getCall(dial_call_id)
  2. if (!dialCall.transcript) → leave it; the next tick retries   ← bounded by run lifetime
  3. atomic claim (§3.3). No row returned → someone else has it, return
  4. outcome = await extract(track, transcript)
  5. UPDATE calls SET outcome, state 'done', state_rank 6
  6. events.log(outcome_extracted)
```

`extract(track, transcript)`: one Claude call, `claude-sonnet-5`, **tool use with the track's
single Zod union member** converted to JSON Schema (§2.4), `strict: true`.

**Never throws.** On a parse failure, timeout, or empty transcript: write an all-nulls outcome
with a `note` and set `done`. A throw inside the tick would stall every other call in the run.
The dashboard must render a call whose outcome is entirely unknown, because on a real demo at
least one will be.

### 4.8 `lib/instructions.ts`

`build(plan, vendor, profile): string`, pure, unit-tested.

The AI disclosure is **prepended inside `build()` as a const**, so it is impossible to omit by
editing a template. Every template then ends with the same closer:

> Before you hang up, say exactly: "Summary: " followed by the key facts in one sentence, the
> price, the date, or the outcome we agreed. Then say goodbye.

That spoken summary is an extraction *hint*, not the mechanism. `extract.ts` reads the whole
transcript regardless, so a non-compliant agent degrades rather than fails.

Rules in every template: state the objective within two sentences, no small talk; never invent
user details (anything not in `profile` is "let me check and call back"); if `prior_context` is
set, open with it ("Last time you quoted $X..."). For the `schedule` track, the Context MCP tool
(§4.10) supplies availability, do not inline a slot list.

### 4.9 `lib/aggregate.ts`

`aggregate(run): AggregateView`, pure, no I/O, unit-tested.

Ranks within each track: `quote` ascending `amountUsd` (nulls last, flag cheapest `best`);
`schedule` ascending `slotIso` (unconfirmed last); `negotiate` descending `savedUsd`.

**The headline metric, and the honesty constraint on it.** The original mock showed "47s wall /
8m39s talk = 11×", which silently requires ~104s average calls. Real calls to friends in a loud
room run 30-60s, so the real number is 3-4×. And one `no-answer` row contributes ~0 talk time
while extending wall clock, dragging it down further.

```ts
wallClockSeconds        = max(ended_at) - min(started_at)   // completed calls only
talkSeconds             = sum(duration) for completed calls
sequentialEstimateSecs  = talkSeconds + n * DIAL_OVERHEAD_SECONDS
multiplier              = sequentialEstimateSecs / wallClockSeconds
```

`DIAL_OVERHEAD_SECONDS` is an exported, visible constant. **The UI must label the sequential
figure "est." and name the assumption.** §8.4 forbids fabricated numbers, which applies to our
own metrics first. Exclude failed calls from the wall-clock window. Brief each answerer to hold
a deliberate ~2-minute conversation, that is what makes the real multiplier good, not the math.

### 4.10 `app/api/mcp/route.ts`: Context MCP

Dial's sleeper feature: register our MCP server once via `POST /api/v1/context-mcps` and its
tools become callable **by the voice agent mid-call**. The agent checks a calendar while the
receptionist is talking, instead of booking blind.

**Bearer-guarded** `[review 4A]`. `POST /api/v1/context-mcps` accepts a `headers` object sent on
every connection; put a shared secret there and check it in the route, 401 otherwise. Dial also
injects `X-Dial-Direction`, `X-Dial-User-Number` and `X-Dial-Agent-Number`, which **read like
auth and are not**, any caller can set them by hand. The endpoint is public and serves user
data; it needs real auth, and the repo is going to judges.

Two tools, both reading from `profiles.prefs` and the `calls` context:

- `check_calendar_availability(dateRange)` → free slots.
- `get_user_preference(key)` → budget ceiling, preferred times. Lets the agent hold a line
  ("anything over $900 is out of range") without baking it into the prompt.

**Known cost, accepted:** this is a 40+ minute item, not the 10 minutes it looks like. Streamable
HTTP in a Next 15 route handler needs `mcp-handler` / `@vercel/mcp-adapter` or a hand-rolled
transport; **stateful SSE sessions cannot work across stateless invocations**, so the transport
must be stateless request/response. Then it has to be registered and actually invoked mid-call,
inside a cold-start budget that can put dead air in the shot. It runs in its own lane (§5) so it
cannot block the demo, and it has a hard abort (§6).

Back the calendar with `profiles.prefs` for today. Google Calendar OAuth is cut (§7).

### 4.11 `app/runs/[runId]/page.tsx`: the demo screen

This screen *is* the submission. Everything else feeds it.

```
┌───────────────────────────────────────────────────────────────────┐
│  "I'm moving to SF next month, handle it"      vedant@ ▾          │
│  5 calls · 4 connected · 1 no-answer        ⏱ 00:47               │
├───────────────────────────────────────────────────────────────────┤
│  Pack & Go Movers      ●●●●○  talking   02:14   ▓▓▓▓▓▓▓░░░        │
│  BearHug Moving        ●●●●●  done      01:52   $1,240  ◀ BEST    │
│  Golden Gate Movers    ●●●●●  done      02:31   $1,890            │
│  Dr. Chen (dentist)    ●●●●●  done      01:04   Thu 2:30pm ✓      │
│  Comcast retention     ●●○○○  ringing   00:08        [simulated]  │
├───────────────────────────────────────────────────────────────────┤
│  47s wall clock · 8m 39s talking · 3.8× faster (est.)             │
└───────────────────────────────────────────────────────────────────┘
```

- **Supabase Realtime** subscription on `calls` for pushed row updates, **plus** a `POST .../tick`
  every 2s to drive ingest (§4.6). The tick's response is the render fallback if Realtime drops.
- `await params` (§2.5). `force-dynamic` and `no-store` on the GET route.
- **Stop ticking when every call is terminal** `[review 11A]`, using `isTerminal` from
  `lib/types.ts`. The aggregate view then sits frozen and stable for exactly as long as you need
  to film it, with no retry loop spinning behind the final shot.
- **Server-authoritative clocks.** Return `serverNow` from the route and compute every timer as
  `serverNow - started_at`, interpolating locally between ticks. Using the browser's `Date.now()`
  against a server timestamp shows `-00:03` or a jump on the exact row that matters most.
- Rows must render correctly with a null outcome, a null amount, and a failed state.
- `simulated: true` rows carry a **visible badge** `[review 18A]`.
- **Mask phone numbers** in the UI: `+1 (415) ***-**67`. The video is public (§8.5).
- The `ringing → talking` transition is the money shot. Make it unmistakable.
- Tailwind **v4** (§2.5). Dark, monospace numerals, no component library.

### 4.12 Auth

Supabase Auth, **magic link**. Not Google OAuth: that needs a Cloud project and a consent screen,
and it carries the same `403: access_denied` untrusted-test-user failure mode that got Google
Calendar cut.

- `middleware.ts` refreshes the session on every request (`@supabase/ssr`).
- `app/login/page.tsx` → email form; `app/auth/callback/route.ts` → code exchange.
- `app/onboarding/page.tsx` → first login seeds `profiles` and the five `vendors` rows, so the
  phone roster is entered through the UI and **never committed** `[review 7A]`.
- A DB trigger on `auth.users` insert creates the `profiles` row.

**Auth must never stand between the camera and the fan-out.** Be signed in before filming. Show
the sign-in flow as a separate deliberate shot if you want it in the video, not inline.

### 4.13 `lib/simulate.ts`: labeled simulated rows

Built in Lane A unconditionally `[review 18A]`, because §2.6 says the expected concurrency answer
is 2 and discovering that at 15:00 means building this at 15:00.

Drives a `calls` row through a scripted timeline (`dialing → ringing → talking → done` with
plausible durations and a fixture outcome) through **the same UPDATE path and the same component**
as a real call. `simulated: true` on the row, badge in the UI, said out loud in the voiceover.
Under a cap of 2 the screen shows 2 live rows and 3 honest labeled ones, instead of 2 live rows
and 3 red failures.

### 4.14 Tests

Vitest. Pure functions only, these are precisely the parts whose failure is silent and only
shows up on camera.

- `extract.test.ts`, a clean transcript, a rambling one, an empty one, one with no "Summary:"
  line, and a voicemail-shaped one-sided transcript all produce a valid `Outcome` and never throw.
- `aggregate.test.ts`, ranking with nulls interleaved (nulls last, never `best`); multiplier
  math on a known set; failed calls excluded from wall clock.
- `instructions.test.ts`, every track yields a non-empty instruction containing the AI
  disclosure and the summary closer, and never interpolates `undefined`.
- `state.test.ts` `[review 8A, 14A]`, `mapDialState` over all five `terminationType` values plus
  the bare-`Terminated` case; and rank monotonicity, that a lower-rank write is refused.

No E2E and no mocked-Dial suite. `scripts/smoke.ts` placing one real call tests the thing that
actually breaks, for the same minutes.

---

## 5. Lane assignment: four agents, concurrently

`[review 21A]` 175+ minutes of work against 149 minutes of clock. The lanes are what close that
gap, so this is load-bearing, not an optimization.

**Lane A publishes `lib/types.ts` and `supabase/migrations/0001_init.sql` first, within the first
15 minutes, and pushes them. B, C and D start from that commit.** That shared contract is the
only coupling between lanes.

| Lane | Owns (no other lane writes these) | Depends on | Est. |
|---|---|---|---|
| **A** critical path | `lib/types.ts`, `supabase/`, `lib/supabase/`, `lib/dial.ts`, `lib/ingest.ts`, `lib/router.ts`, `lib/simulate.ts`, `app/api/runs/**`, `app/runs/**` | n/a | 115m |
| **B** auth + MCP | `middleware.ts`, `app/login/`, `app/auth/`, `app/onboarding/`, `app/api/mcp/` | types, schema | 70m |
| **C** events + Keewano | `lib/events.ts`, `lib/keewano.ts` | types, schema | 30m |
| **D** pure functions | `lib/extract.ts`, `lib/aggregate.ts`, `lib/instructions.ts`, `test/**` | types only | 55m |

```
  0:00  ┌─ A: types.ts + migration ──┐  (15m, BLOCKING, everyone waits)
        │                            │
  0:15  ├─ A: dial, ingest, dispatch, simulate, dashboard ────────────┐
        ├─ B: auth ──────────┬─ B: MCP server ──────────┐            │
        ├─ C: events+keewano ┘ (done 0:45)              │            │
        ├─ D: extract, aggregate, instructions, tests ───┘ (done 1:10)│
  1:55  └─────────────────────────── A lands ───────────────────────┘
```

Critical path is Lane A alone: 115 minutes, finishing around 14:40 with ~35 minutes of slack
before the 15:15 record slot.

**Conflict flags.** No two lanes share a directory, by construction. Three integration points
need care:

- `lib/types.ts`, **Lane A only.** B, C and D import and never edit. If another lane needs a
  type, it asks A.
- `app/page.tsx` and `app/layout.tsx`. A writes the run list, B writes the signed-out state.
  **Assign both files to B** and have A expose the run list as a component A owns.
- `lib/events.ts` is C's, but A calls it everywhere. **A codes against the §4 signature from
  minute 0** and C delivers to that signature. Do not let A wait on C.

Merge order: **C → D → B → A**. A merges last because it is the largest diff and the only one
that must be green at 15:15. If a lane is not done at 14:40, merge what exists and drop the rest:
C is fire-and-forget, D's tests are droppable, B's MCP is droppable. **Lane A is not droppable.**

---

## 6. Timeline with abort gates

Each phase ends in something recordable. If a gate fails, take the fallback and move on, do not
debug past a gate.

### T-0 · 12:45 → 13:05 · Setup, all lanes blocked on this

**Three of these are browser signups an agent cannot do. A human does them, now, in parallel
with Lane A writing the schema.**

1. `GET /api/v1/account` → **print `limits`** (§2.6). This is the concurrency answer.
2. `GET /api/v1/numbers` → note the id and E.164 to call from. Provision if none
   (`explicitProgrammaticConsent` required).
3. **Supabase project** → URL, anon key, service-role key.
4. **`ANTHROPIC_API_KEY`** → not currently in `.env`, and both the router and extractor are dead
   without it.
5. **Keewano** → sign up, send `#SpeedHacks` in chat, Settings → Project → API Keys.
6. `create-next-app@latest` (TS, Tailwind **v4**, App Router). Vercel project, **Deployment
   Protection off**.
7. Apply the migration. Sign in once. Seed five `vendors` rows through onboarding.

**GATE:** concurrency limit known, migration applied, all five keys in `.env.local`.

### T+20 · 13:05 → 13:25 · One real call

`scripts/smoke.ts`: hardcoded instruction, calls **your own cell**, polls until `Terminated`,
prints the transcript. Answer it. Talk to it. Read the transcript.

**GATE:** a transcript of a real conversation is on screen. **Needs a human to answer a phone.**
Nothing downstream is meaningful until this works.

### T+40 · 13:25 → 15:20 · Lanes run (§5)

Lane A's internal gate, and the one that matters:

**GATE at 14:30, one POST puts 3 phones ringing at once and all 3 rows move
ringing→talking→done on screen.** This is the demo. If this works at 14:30 you have a
submission, and everything after is improvement you can stop at any time.

Lane abort rules:
- **Lane C (Keewano):** 15 minutes maximum on custom-event registration (`customEventsHash` /
  `POST /json/custom` / codegen). This is the plan's one genuine unknown. If it fights back, log
  under built-in event ids with detail in `data` and stop. Postgres `events` is the system of
  record either way, so nothing is lost. `priorContext` reads from Postgres regardless, the loop
  is closed by our own store, and Keewano is the analysis surface on top.
- **Lane B (MCP):** hard abort at 14:50. If the agent is not calling the tool mid-call by then,
  ship the `schedule` instruction with an inline availability list and render a "checking
  calendar…" row. Indistinguishable on camera, 5 minutes.
- **Lane D:** if tests are not done by 14:40, ship the three library files and drop the tests.

### T+150 · 15:15 → 15:45 · Record and submit

Scripted takes, in order:

1. Type the request. Hit enter.
2. Phones ringing simultaneously, **all of them in one frame.** This shot is the product.
3. One call's audio, clean. The mid-call calendar check if Lane B landed.
4. The dashboard filling in: rows going green, the table ranking itself.
5. The multiplier, labeled "est." (§4.9).
6. A second run to the same target opening with context from the first, the improvement loop in
   15 seconds.
7. One shot of the schema or the events table, since the repo is going to judges.

**Before you hit record** (§8.5): close every terminal and editor pane, confirm phone numbers
render masked, and have each phone owner confirm **ringer on, DND off** (§4.3's voicemail guard
catches it in code, but a failed row still costs you a re-shoot).

Record screen + system audio together. §2.1 established there is no recording API, so there is no
second chance to capture call audio.

**Submission risk:** 30 minutes for six shots, five humans, an edit, and a form, against a hard
16:00. The common hackathon loss is a working demo that missed the form. If 15:35 arrives and the
edit is not done, **upload the raw continuous capture and submit it.** Resubmit a better cut only
if time remains.

---

## 7. Explicitly not in scope

Considered and cut, not forgotten.

| Cut | Why |
|---|---|
| **SMS fallback agent** | Dial has no SMS agent primitive (§2.1). A multi-turn loop is ours to build: inbound webhook, thread state, Claude per turn, reply. 45+ minutes for something that films poorly next to ringing phones, and the PRD's strongest case (a target that never answers) is a non-event on camera. |
| **Dial webhooks** | `[review 15A]` Polling via `/tick` is strictly more robust for a recorded demo and removes HMAC, dedupe, the reverse index, registration, and a dispatch race. ~25 minutes and ~60 lines saved. |
| **Google Calendar OAuth** | 30 minutes and a `403: access_denied` mode needing every demo account added as an OAuth test user. The Context MCP tool reads `profiles.prefs` and looks identical on video. |
| **Calling real vendors** | Two-party consent applies to the submitted video (§8). Our own phones are legally clean and rehearsable. `transferTo` (§2.1) makes real vendors viable after today. |
| **Retry / backoff on failed calls** | A `no-answer` row is honest and free. Retry serializes the fan-out, attacking the one thing we claim. |
| **Upstash Redis** | Supabase covers it. Postgres does the locking, claiming and ranking atomically (§3.3), and one store beats two. |
| **Google OAuth login** | Magic link has no consent screen and no test-user gate (§4.12). |

---

## 8. Non-negotiables

1. **Every call opens by identifying as an AI.** Prepended as a const inside `build()`, enforced
   by `instructions.test.ts`. Not a toggle.
2. **Only our own phones get called today.** The `vendors` table contains only numbers belonging
   to people in the room, and the router emits `vendorKey` values, never numbers.
3. **Consent on tape.** California is two-party consent. Everyone whose voice is in the
   submission says so on the recording.
4. **No fabricated numbers, including our own metrics.** Pine AI's "93% success rate" is their
   claim, not a benchmark (PRD §7); DoNotPay is under a Jan 2025 FTC final order for exactly this
   category of overclaim. Our multiplier is labeled "est." with the overhead assumption visible
   (§4.9), and the voicemail guard (§4.3) exists so no invented price ever reaches the screen.
5. **Simulated rows are labeled simulated**, in the UI and in the voiceover.
6. **Keewano's honest scope:** it surfaces patterns and supplies prior context at dispatch. It
   does not autonomously rewrite prompts. Say it that way (PRD §6 already commits to this).
7. **Nothing personal in git** `[review 7A]`. The repo has a GitHub remote and is being shared
   with judges. Phone numbers live in the `vendors` table, entered through onboarding. `.env*` is
   already gitignored; verify before the first push.
8. **RLS on every table, service-role key server-only** (§3.2). A judge reading the repo will
   check this first.

---

## 9. Review record

21 findings raised, 21 resolved. Independent cross-model challenge run (Claude; `codex` not
installed on this machine) and its findings folded in.

| # | Finding | Resolution |
|---|---|---|
| 1 | Extractor inside the webhook handler: double-extract or lose-event race | 1A → data-driven trigger in the tick (§4.7) |
| 2 | Router's Claude call blocks the fan-out, dead air on the key beat | 2A → pre-seeded plan cache (§4.4) |
| 3 | One-blob Redis run forced a lock; lost 4 of 5 dispatch writes every time | 3A → one row per call (§3.3) |
| 4 | Context MCP endpoint public with forgeable `X-Dial-*` auth | 4A → bearer header on registration (§4.10) |
| 5 | Polling fallback specced as a second ingest path | 5A → one `ingestCallUpdate` (§4.3) |
| 6 | `router.plan()` throwing 500s the run, blank screen | 6A → fixture fallback, never throws (§4.4) |
| 7 | Real personal phone numbers committed to a repo with a GitHub remote | 7A → `vendors` table, seeded via onboarding (§3.1) |
| 8 | Dial→CallState mapping as a prose table, implemented twice | 8A → one `mapDialState`, unit-tested (§4.2) |
| 9 | Lazy extract double-fires: extraction outlasts the poll interval | 9A → atomic `UPDATE…RETURNING` claim (§3.3) |
| 10 | Webhook signature verification untested | moot, webhooks cut (15A) |
| 11 | Dashboard polls forever, including behind the final shot | 11A → stop when all terminal (§4.11) |
| 12 | Phase 3 packed 8 files and 2 LLM integrations into 30 min | 12A → widened; superseded by lanes (§5) |
| 13 | **Extraction fired on `call.ended`, before the transcript exists** | 13A → wait for transcript data (§4.7) |
| 14 | Late/redelivered status reverts a finished call on camera | 14A → monotonic `state_rank` (§3.3) |
| 15 | Webhook surface not worth its cost for a recorded demo | 15A → deleted, `/tick` polling (§4.6) |
| 16 | Vercel in the critical path: cold starts, deploy loop, 401 protection | 16B → stay on Vercel; 15A removes most of the risk (§2.5) |
| 17 | Voicemail extracts as a real quote → fabricated price on screen | 17A → guard + pre-take checklist (§4.3) |
| 18 | Concurrency cap is probably already 2 | 18A → labeled simulated rows, built up front (§4.13) |
| 19 | Context MCP budgeted 10 min, realistically 40+ | 19B → build it properly, own lane, hard abort (§4.10, §6) |
| 20 | Phase 6: 6 shots, 5 humans, an edit and a form in 30 min | 20B → keep 15:15; raw-capture fallback added (§6) |
| 21 | 175 min of work against 149 min of clock | 21A → four parallel lanes (§5) |

Also folded in from the cross-model challenge, as plan corrections rather than decisions: no
Anthropic JSON mode and Zod≠tool-definition (§2.4); Next 15 `await params`, cache headers,
Tailwind v4, Deployment Protection (§2.5); the dispatch must await `allSettled` (§4.5); the
multiplier was overstated 11× vs a realistic 3-4× (§4.9); server-authoritative clocks and masked
phone numbers (§4.11); secrets-in-frame risk (§6); `maxCallDurationSeconds: 180`; `Outcome` lost
its `vendor` field (§1); no documented `"*"` webhook wildcard (§2.1).

**What already existed:** nothing reusable. The repo was five empty directories, placeholder
READMEs, a PRD, and a `.env` with `DIAL_API_KEY`, `DIAL_NUMBER` and `KEEWANO_TOKEN`. Nothing is
being rebuilt that already works; `ANTHROPIC_API_KEY` and the Supabase keys are net-new.

---

## 10. Env

```
DIAL_API_KEY=sk_live_...              # present
DIAL_NUMBER=+1...                     # present
KEEWANO_TOKEN=...                     # present
ANTHROPIC_API_KEY=sk-ant-...          # MISSING, router + extractor dead without it
NEXT_PUBLIC_SUPABASE_URL=...
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...         # SERVER ONLY. bypasses RLS. never NEXT_PUBLIC_
MCP_SHARED_SECRET=...                 # §4.10 bearer guard
NEXT_PUBLIC_BASE_URL=https://...
KEEWANO_ENABLED=true                  # kill switch
DIAL_OVERHEAD_SECONDS=45              # §4.9, visible assumption
```

Never import `lib/dial.ts`, `lib/keewano.ts`, `lib/extract.ts`, `lib/router.ts`, or
`lib/supabase/service.ts` from a client component. All five hold secrets.

---

## 11. The seven ways this dies

Ranked by probability, each with its mitigation already placed.

1. **Nobody answers the phones during the take.** Assign one person per phone, by name, before
   recording. Ringer on, DND off. The most common hackathon demo failure is social, not technical.
2. **Concurrency cap is 2.** §2.6 answers it at 13:05; §4.13's labeled simulated path makes the
   answer non-load-bearing either way.
3. **Lane A slips past 14:30.** Merge whatever B, C and D have and ship the Lane A gate alone.
   A is the only undroppable lane.
4. **Extraction returns nulls on real speech.** §4.7 waits for the transcript, never throws, and
   the spoken "Summary:" closer makes it very likely to work. The dashboard renders nulls.
5. **The MCP server eats its lane and returns nothing.** Hard abort at 14:50 (§6), fall back to
   an inline availability list.
6. **Keewano custom events fight back.** 15-minute abort; Postgres `events` is the system of
   record regardless.
7. **A working demo misses the submission form.** §6's raw-capture fallback: upload and submit
   the continuous capture at 15:35 if the edit is not done.

The honest read: **Lane A's 14:30 gate is the real deadline.** Hit it and you have a submission
no matter what else happens. B, C and D each make the product and the video better, and none of
them are load-bearing.

---

## 12. Added scope: notes for Fable

Four items the epic doc commits to (or cut) that §1-§11 does not cover. **These are notes, not
full specs.** Fable designs the details; the constraints below are the parts that matter.

### 12.1 Ledger (issue #7)

Persists dated obligations so the product has a reason to exist between runs. Without it there is
no second act.

```sql
create table ledger (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references profiles(id) on delete cascade,
  vendor_id    uuid references vendors(id) on delete set null,
  kind         text not null check (kind in ('promo_expiry','renewal','appointment','quote')),
  label        text not null,
  amount_usd   numeric,
  effective_on date,
  expires_on   date,                    -- the trigger date for act two
  source_call  uuid references calls(id) on delete set null,
  created_at   timestamptz not null default now()
);
create index on ledger (user_id, expires_on);
```

- Written by the aggregator when a `CallResult` yields a dated fact (a promo that ends, a renewal
  date, a booked slot).
- `source_call` is what makes it defensible: every ledger row traces to a transcript.
- The dashboard renders this as a timeline, which is the "nobody tracks all of this against
  actual spend" line in the PRD appendix made concrete.
- Date-based triggers only. Bill ingestion from email or bank is cut (epic doc).

### 12.2 Eligibility / cohort profiles (issue #5)

A config object, not a system. Ships with `student` only, built so a second cohort is a one-line
swap. Lives on `profiles.cohort` plus a `cohorts` config in the router package.

```python
{"key": "student", "label": "full-time student",
 "asks": ["student rate", "waive activation fee", "no-contract term"],
 "proof": "university email on file"}
```

The router injects the cohort's `asks` into each `CallRequest.task`, so the agent asks for the
student rate without the user thinking to. Keep the asks in config, not in the prompt template,
so they are inspectable and so a judge can see what the agent is instructed to claim.

**Honesty constraint (§8.4):** the agent may only assert eligibility the user actually has. Never
let the model infer or embellish a cohort claim. Senior and military cohorts are cut today.

### 12.3 Two-act demo structure (§6, recording)

The epic's two-act framing is a better video than §6's single act, and it is the Keewano context
loop made visible rather than asserted.

- **Act one:** "I'm moving to SF next month, handle it" → the N-call fan-out, the ranked table,
  one ledger row written with an `expires_on` eleven months out.
- **Act two:** "11 months later." Advance the clock (a seeded `expires_on` in the past, or a date
  override, not a fabricated result). The ledger fires, Dormammu places a retention call on its
  own, and the agent opens with context from act one: "last year you quoted $89 a month."

That opening line is the entire Keewano and prior-context claim, demonstrated in one sentence
instead of described. Shoot act two even if it is the only scripted beat you get.

**Do not fake the eleven months.** Seed the date and say you seeded it. §8.4 applies.

### 12.4 Context MCP calendar (un-cuts the scheduling track)

The epic cut the Google Calendar track for lack of an owner. §4.10's Context MCP tool gives the
mid-call calendar check **without Google OAuth**, backed by `profiles.prefs`, so the track comes
back cheaply. Keep §4.10's bearer guard `[review 4A]` and its 14:50 hard abort.

Fallback if the abort fires: put the availability list inline in the `schedule` instruction and
render a "checking calendar…" row when that call reaches `talking`. Indistinguishable on camera.

### 12.5 One repo-hygiene note

`README.md` still says "Barebones template only. No implementation yet," and the top-level
`router/`, `aggregator/`, `dashboard/`, `test-lines/` directories still contain only placeholder
READMEs. The repo is being shared with judges. Updating the README is a five-minute job worth
doing before the submission, and it is the first file anyone opens.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | NOT RUN | n/a |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | SKIPPED | codex not installed |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 21 issues, 21 resolved, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | NOT RUN | n/a |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | NOT RUN | n/a |

- **CROSS-MODEL:** independent Claude challenge run in place of codex. It converged
  independently on finding 3 (shard Redis state per call), which raises confidence in that fix.
  It contributed findings 13, 14, 17 and 21, which the first pass missed; 13 was the most
  dangerous defect in the plan. Its two strongest recommendations were partially declined by the
  user: webhooks were cut (15A, accepted) but Vercel was retained (16B) and the Context MCP
  server was kept at full scope (19B).
- **VERDICT:** ENG CLEARED, ready to implement. Scope expanded twice mid-review at user
  direction: first to a full product (Supabase, auth, durable event log), then reconciled against
  the working Python code discovered on `origin/main`. §0 carries that reconciliation and §12 the
  added scope; §1-§11 keep the architecture, the review findings and the API ground truth.

**UNRESOLVED DECISIONS:**
- Keewano custom-event registration (`customEventsHash` / `POST /json/custom` / codegen CLI) is
  undocumented and unverified. 15-minute abort with a built-in-event fallback; Postgres is the
  system of record either way.
- Whether Dial credits raise the *concurrency* ceiling or only a minute balance is unverified.
  The billing endpoints exist in the spec; their effect on `limits` does not.
- Scope was expanded to a full product (auth, Supabase, persistence) after the time budget was
  already 26 minutes over. The four-lane split (§5) is what closes that gap, so parallel
  execution is load-bearing rather than optional, and Lane A slipping has no fallback but to cut
  the other three lanes.
- §1-§11 were drafted against a greenfield TypeScript repo before the working Python on
  `origin/main` was visible. They were reconciled by §0 as deltas rather than rewritten, at the
  user's explicit direction to save clock. **Fable must read §0 first or it will rebuild the
  fan-out that already works in `call/dispatch.py`.**
- The Supabase-behind-`write_result` swap (§0, decision 2) has not been tried against
  `wait_for_result`'s poll loop. The seam is clean on paper; nobody has run it.
