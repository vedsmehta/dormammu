# Dormammu

One request in, N phone calls out at the same time, one combined result back.

Built at SpeedHacks (a16z speedrun, Oct 11 2026) on Dial. The use case is one specific week: a
Stanford student taking their first off-campus apartment for the fall, on a student budget. That
week is a pile of phone calls nobody wants to make: three movers to compare for a 1-bedroom on a
fixed date, an internet provider to set up at the new address, a dentist near campus to see as a
new patient. Each call is solvable by one AI caller that identifies itself as an AI. The leverage is
placing all of them at once and bringing the answers back into one table (docs/USE-CASE.md).

The student types one sentence: "I'm moving to Palo Alto on October 25 for the school year,
1-bedroom, handle the move, internet and a dentist." The router carries the date, the bedroom count
and the destination into every objective, appends the student asks per track (move discount and a
capped deposit; student rate, fee waived, no contract; new-patient slot and self-pay price), and
dispatches. On the negotiate track the agent picks its opening from the bill-negotiation playbook
(advertised anchor, loyalty, credible cancellation, audit, or new-service setup) and says why. Every
completed call writes rows that the next call opens with, so the second call to a vendor starts
from what the first one learned.

```
"I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist"
        │
        ▼
     ROUTER  (OpenAI, roster-constrained: emits vendor keys, never phone numbers)
        │
   ┌────┼────┬────────┬────────┐
   ▼    ▼    ▼        ▼        ▼
  Dial Dial Dial     Dial     Dial       concurrent, asyncio.gather
   │    │    │        │        │
   └────┴────┴────────┴────────┘
        │  client-driven /tick: ingest, voicemail guard, monotonic state
        ▼
    EXTRACT  (transcript → Outcome, only once the transcript exists)
        ▼
   AGGREGATE (ranked per track, honest "est." multiplier)
        ▼
  Postgres events + ledger (system of record)  →  Keewano (secondary sink)
```

## Stack

| Layer | Where | Notes |
|---|---|---|
| Calling core | `call/` | Dial REST client, state machine, ingest, simulated rows, fan-out |
| Router | `router/` | Plan cache, cohort asks, instruction builder, OpenAI structured output |
| Aggregator | `aggregator/` | Extractor, ranking, ledger rules |
| Events | `events/` | Postgres `events` first, Keewano fire-and-forget |
| Service | `server/` | FastAPI, routes under `/api/py`, Supabase or SQLite store |
| Web | `app/`, `lib/` | Next.js 16, Supabase email + password auth, Realtime dashboard |
| Schema | `supabase/migrations/` | RLS on every table from the first line |
| SMS | `sms/` | Dial SMS send and wait-for-reply (CLI) |

Python 3.12 with `uv`. Next.js at the repo root so Vercel builds the web layer and
`api/index.py` as one project (Next + FastAPI hybrid; `/api/py/*` is rewritten to the Python
function in production and to `127.0.0.1:8000` in dev).

## Run it locally

```
cp test/.env.example .env     # then fill in the keys listed in docs/CONTRACT.md
uv sync
npm install
bash scripts/dev.sh           # service on :8000 + Next dev on :3000, one terminal, Ctrl-C stops both
bash scripts/dev.sh --dry-run # same, but never dials: every row is a labeled simulated row
```

The local loop is the debug surface. Iterate here, then `git push` and `vercel --prod` for the
hosted copy. Both point at the same Supabase project, so a run made locally shows up in prod.

Smoke test one real call before any multi-call run:

```
uv run python scripts/smoke.py          # rings TEST_CALL_TO, prints the status transitions and transcript
uv run python scripts/preflight.py      # Dial limits, roster, warms the plan cache with the demo sentence
```

Create an account or sign in with email + password on `/login` (accounts are created server-side
with the email pre-confirmed, so no mail is ever sent), seed your vendor roster on `/onboarding`
(phone numbers live in the `vendors` table, never in git), type a request, watch `/runs/<id>`.

Without Supabase keys the service falls back to SQLite and `DEV_USER_ID`, with no auth. That mode
is for tests and local poking only.

## Tests

```
uv run pytest -q
uv run ruff check .
npm run build
```

Tests cover the parts whose failure is silent on camera: the Dial state mapping, rank
monotonicity, the voicemail guard, extraction never throwing, ranking with nulls, the multiplier
math, and that every instruction opens with the AI disclosure.

## Honesty rules

- Every call opens by identifying as an AI. It is a constant inside the instruction builder, not
  a toggle.
- Only numbers in the user's own `vendors` table get called. The router emits keys.
- The concurrency cap on this Dial account is 2. Calls past the cap become rows labeled
  **simulated** in the UI and in the data. They are never shown as real.
- A voicemail that the agent talked to is marked `failed / voicemail`, not extracted into a price.
- The "N× faster" figure is labeled "est." and the dial-overhead assumption it rests on is shown
  next to it.

## Docs

- `BUILD-PLAN.md`: the reviewed plan, API ground truth for Dial, Supabase, Keewano.
- `docs/CONTRACT.md`: module signatures, row shapes, the store interface, routes.
- `PRD.md`, `docs/epic-mvp-oct11.md`: product scope and the team's issue split.
