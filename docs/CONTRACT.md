# Dormammu build contract (Oct 11 2026, 13:25)

This is the shared contract for the parallel build lanes. BUILD-PLAN.md is the spec; this file
resolves its one ambiguity and pins names so lanes do not drift. Where this file and BUILD-PLAN.md
disagree, this file wins.

## Architecture decision

Python is the backend. Next.js is the UI and auth layer only.

- Python FastAPI service, all routes under `/api/py/...`. Locally: `uvicorn server.app:app --port 8000`.
  On Vercel: `api/index.py` exposes the same `app` as a Python serverless function, using Vercel's
  Next.js + FastAPI hybrid (next.config rewrites `/api/py/:path*` to `http://127.0.0.1:8000/api/py/:path*`
  in dev and to `/api/` in production).
- Next.js (root of repo, App Router, Tailwind v4) calls `/api/py/...` from the browser with
  `Authorization: Bearer <supabase access_token>`. Next.js does NOT proxy. Next.js uses Supabase
  directly only for auth (email + password, cookies) and Realtime subscriptions on `calls`/`runs`.
  Account creation goes through the Python service (`POST /api/py/auth/signup`), which calls the
  GoTrue admin API with `email_confirm` set, so nothing in the auth flow depends on email delivery.
- LLM: OpenAI (`openai` python package v3.x, `OPENAI_API_KEY`, model `OPENAI_MODEL`, default `gpt-4.1`).
  Structured output via `client.chat.completions.parse(..., response_format=<pydantic model>)` or
  the Responses API equivalent. Introspect the installed SDK before writing; do not assume 1.x API.
- Dial: hand-rolled async httpx client against `https://api.getdial.ai/api/v1`, bearer `DIAL_API_KEY`.
  `dial-sdk` stays only for `sms/messenger.py`.
- Storage: `server/store.py` exposes one `Store` interface with two backends. `SupabaseStore` uses
  `supabase-py` (PostgREST) with the secret key (server-only). `SqliteStore` is a stdlib sqlite3
  backend with identical semantics for tests and no-Supabase dev. `get_store()` picks Supabase when
  `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are set, else sqlite at `.calls/dormammu.db`.
- Concurrency cap on this Dial account is 2 (verified 13:06). Calls that hit 429 become labeled
  simulated rows, never red failures.

## Repo layout

```
api/index.py               Vercel entry: `from server.app import app`
server/app.py              FastAPI app, routes below
server/store.py            Store interface + SupabaseStore + SqliteStore + get_store()
server/auth.py             current_user_id(request) -> str   (Supabase JWT via GoTrue /auth/v1/user; dev fallback)
server/schemas.py          pydantic request/response bodies
call/models.py             KEEP. CallRequest/CallResult frozen. write_result/read_result stay file-based (CLI path).
call/dial_client.py        place_call, get_call, get_account, list_numbers, stop_call, DialError
call/state.py              CallState, STATE_RANK, is_terminal, map_dial_state
call/ingest.py             ingest_call_update (voicemail guard, monotonic write, events)
call/simulate.py           advance_simulated (scripted timeline + fixture outcome, same UPDATE path)
call/dispatch.py           run_calls (CLI, Dial-backed) + dispatch_run (THE FAN-OUT for the service)
call/agent.py              DELETE (LiveKit). sms/messenger.py untouched.
router/plan.py             plan(), prior_context(), plan cache, never throws, cap MAX_CALLS_PER_RUN
router/cohorts.py          COHORTS config, student only
router/instructions.py     build() with AI disclosure const + summary closer
aggregator/extract.py      extract(track, transcript) -> Outcome dict, never throws
aggregator/aggregate.py    aggregate(run) -> AggregateView dict, pure
aggregator/ledger.py       ledger_rows(call_row) -> list[dict], pure
events/log.py              log_event() -> store.insert_event then fire-and-forget keewano
events/keewano.py          send_events(), 15-minute abort rule on custom ids, truncate strings to 256
supabase/migrations/0001_init.sql
app/**, lib/**, middleware.ts, next.config.ts   Next.js (Lane B)
test/test_*.py             pytest (asyncio_mode=auto). test/ is Python only.
```

## Env (all in .env, gitignored; Next reads NEXT_PUBLIC_* at build)

DIAL_API_KEY, DIAL_NUMBER, KEEWANO_TOKEN, OPENAI_API_KEY, OPENAI_MODEL, SUPABASE_URL,
SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY, SUPABASE_JWKS_URL, NEXT_PUBLIC_SUPABASE_URL,
NEXT_PUBLIC_SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY (same value as SUPABASE_SECRET_KEY),
MCP_SHARED_SECRET, INTERNAL_API_SECRET, PYTHON_API_URL, NEXT_PUBLIC_BASE_URL, KEEWANO_ENABLED,
DIAL_OVERHEAD_SECONDS (45), MAX_CALLS_PER_RUN (5), DEV_USER_ID (optional: skip auth locally),
SIMULATED_TALK_SECONDS (optional, default 75), TEST_CALL_TO (smoke call target).

`settings.py` already has `load_env()`, `env(name, default)`, `results_dir()`. Use them. Never
read `.env` any other way.

## Data shapes (plain dicts, column names == JSON keys, snake_case everywhere in Python and SQL)

### vendors row
`{id, owner_id, key, name, track, phone, is_demo}`; track in quote|schedule|negotiate.

### CallPlan (router output)
`{"vendor_key": str, "track": str, "objective": str, "voice_gender": "female"|"male"}`

### runs row
`{id, user_id, request, status, created_at, completed_at}`; status in dispatching|running|complete|failed.

### calls row
```
{id, run_id, vendor_id, vendor_key, track, target_name, target_phone, objective, prior_context,
 dial_call_id, state, state_rank, termination_type, transcript, transcript_turns (json list),
 outcome (json), simulated (bool), error, duration_seconds, started_at, ended_at}
```
state in planned|dialing|ringing|talking|extracting|done|failed.
STATE_RANK = planned 0, dialing 1, ringing 2, talking 3, extracting 4, done 6, failed 6.

### Outcome (json on calls.outcome). Exactly one of:
```
{"track":"quote","amount_usd": float|null, "unit": str|null, "caveats": [str], "note": str|null}
{"track":"schedule","slot_iso": str|null, "confirmed": bool, "note": str|null}
{"track":"negotiate","action":"reduced"|"canceled"|"refused"|"none","saved_usd": float|null,
 "effective_date": str|null, "note": str|null}
```
`note` is set when extraction could not read the transcript (all-nulls path). Never throw.

### events row
`{id, user_id, run_id, call_id, type, data (json), created_at}`. type in: run_requested,
call_dispatched, call_simulated, call_connected, call_terminated, outcome_extracted,
run_aggregated, ledger_written.

### ledger row
`{id, user_id, vendor_id, kind, label, amount_usd, effective_on, expires_on, source_call, created_at}`;
kind in promo_expiry|renewal|appointment|quote.

### profiles row
`{id, display_name, phone, prefs (json), cohort (text, default 'student'), created_at}`.
prefs example: `{"budget_ceiling_usd": 900, "preferred_times": ["weekday afternoons"],
"availability": [{"start":"2026-10-15T14:00:00-07:00","end":"2026-10-15T17:00:00-07:00"}]}`

### AggregateView (returned by aggregate(), pure)
```
{"tracks": {"quote": [call rows ranked, each with "best": bool], "schedule": [...], "negotiate": [...]},
 "wall_clock_seconds": float|null, "talk_seconds": float, "sequential_estimate_seconds": float|null,
 "multiplier": float|null, "dial_overhead_seconds": int, "n_calls": int, "n_completed": int,
 "n_failed": int, "n_simulated": int, "n_live": int}
```

## Store interface (server/store.py). Every method is sync. Rows are dicts as above.

```python
class Store(Protocol):
    def get_profile(self, user_id: str) -> dict | None
    def upsert_profile(self, user_id: str, fields: dict) -> dict
    def list_vendors(self, user_id: str) -> list[dict]
    def upsert_vendors(self, user_id: str, vendors: list[dict]) -> list[dict]   # by (owner_id, key)
    def create_run(self, user_id: str, request: str) -> dict
    def get_run(self, run_id: str, user_id: str | None = None) -> dict | None   # row + "calls": [rows]
    def list_runs(self, user_id: str, limit: int = 20) -> list[dict]
    def set_run_status(self, run_id: str, status: str, completed_at: str | None = None) -> None
    def insert_calls(self, rows: list[dict]) -> list[dict]
    def list_calls(self, run_id: str, nonterminal_only: bool = False) -> list[dict]
    def get_call(self, call_id: str) -> dict | None
    def update_call(self, call_id: str, fields: dict) -> dict | None            # unconditional
    def update_call_state(self, call_id: str, state: str, rank: int, fields: dict | None = None) -> bool
        # UPDATE ... WHERE id=$1 AND state_rank < $rank. Returns True iff a row changed.
    def claim_extraction(self, call_id: str) -> bool
        # UPDATE calls SET state='extracting', state_rank=4 WHERE id=$1 AND outcome IS NULL AND state <> 'extracting'. True iff claimed.
    def insert_event(self, user_id: str, type: str, data: dict, run_id: str | None = None, call_id: str | None = None) -> dict
    def list_events(self, user_id: str, limit: int = 50) -> list[dict]
    def last_outcomes_for_vendor(self, user_id: str, vendor_key: str, limit: int = 3) -> list[dict]
        # done calls (outcome not null) for this user's runs with this vendor_key, newest first
    def get_plan_cache(self, request_hash: str) -> list[dict] | None
    def put_plan_cache(self, request_hash: str, plans: list[dict]) -> None
    def insert_ledger(self, rows: list[dict]) -> list[dict]
    def list_ledger(self, user_id: str) -> list[dict]
    def due_ledger(self, user_id: str, as_of: str) -> list[dict]   # expires_on <= as_of

def get_store() -> Store
```

Store is sync; FastAPI handlers call it directly (PostgREST calls are fast). Dial and OpenAI
calls are async.

## Python module signatures

```python
# call/dial_client.py
class DialError(Exception):            # .status_code: int, .code: str | None, .message: str
    @property
    def is_limit(self) -> bool         # status 429 or code == "call_limit_reached"
async def place_call(to: str, instruction: str, *, from_number: str | None = None,
                     voice_gender: str = "female", max_duration_seconds: int = 180,
                     transfer_to: str | None = None, idempotency_key: str | None = None) -> dict
async def get_call(call_id: str) -> dict
async def get_account() -> dict
async def list_numbers() -> list[dict]
async def stop_call(call_id: str) -> dict
# The returned dicts are the raw Dial JSON. Probe GET /api/v1/calls with the real key first and
# document the exact field names (camelCase vs snake_case) at the top of the module.

# call/state.py
CallState = Literal["planned","dialing","ringing","talking","extracting","done","failed"]
STATE_RANK: dict[str, int]
def is_terminal(state: str) -> bool
def map_dial_state(status: dict) -> tuple[str, int] | None
    # status = {"state": "Queued|Ringing|In-Progress|Terminated", "terminationType": ...|None}
    # Queued->dialing, Ringing->ringing, In-Progress->talking, Terminated+completed->extracting,
    # Terminated+(busy|no-answer|failed|canceled|None)->failed, unknown->None (leave unchanged)

# call/ingest.py
def ingest_call_update(store: Store, call_row: dict, dial_call: dict) -> dict | None
    # Voicemail guard: Terminated/completed AND (no callee turn in transcript turns OR duration < 15)
    # -> state failed, termination_type "voicemail". Then monotonic update_call_state. Store
    # transcript (+turns, duration_seconds, termination_type, ended_at) when present. log_event
    # call_connected on first talking, call_terminated on terminal. Returns refreshed row or None.

# call/simulate.py
def fixture_outcome(track: str, seed: str) -> dict
def fixture_transcript(track: str, target_name: str, seed: str) -> list[dict]   # [{speaker, text}]
def advance_simulated(store: Store, call_row: dict, now: datetime) -> dict | None
    # Deterministic timeline from started_at: 0-4s dialing, 4-11s ringing, then talking for
    # SIMULATED_TALK_SECONDS (+/- seeded jitter), then extracting->done with fixture outcome and
    # transcript, ended_at set. Uses update_call_state (monotonic) and update_call. Logs events.

# call/dispatch.py
async def dispatch_run(store: Store, run: dict, calls: list[dict], profile: dict | None) -> list[dict]
    # asyncio.gather(return_exceptions=True) over one coroutine per calls row: build instruction
    # (router.instructions.build), place_call(maxduration 180, idempotency_key=call id), then
    # update_call(dial_call_id) + update_call_state(dialing, 1). On DialError.is_limit or any
    # exception: update_call(simulated=True, error=None) + update_call_state(dialing,1), log
    # call_simulated. Always log call_dispatched. Returns refreshed rows. Never raises.
async def run_calls(requests: list[CallRequest], max_concurrent=None, timeout_seconds=900.0) -> list[CallResult]
    # CLI path on Dial: place_call, poll get_call until Terminated, fill CallResult, write_result.

# router/plan.py
def request_hash(request: str) -> str                      # sha256 of trim+lower
async def plan(store: Store, request: str, user_id: str) -> list[dict]   # CallPlan dicts, 1..MAX_CALLS_PER_RUN, never throws
def fallback_plans(vendors: list[dict], request: str) -> list[dict]      # default roster, never empty if vendors non-empty
def prior_context(store: Store, user_id: str, vendor_key: str, track: str) -> str | None

# router/cohorts.py
COHORTS = {"student": {"key": "student", "label": "full-time student",
           "asks": ["student rate", "waive activation fee", "no-contract term"],
           "proof": "university email on file"}}
def cohort_for(profile: dict | None) -> dict

# router/instructions.py
AI_DISCLOSURE: str   # spoken first, always
SUMMARY_CLOSER: str  # 'Before you hang up, say exactly: "Summary: " ...'
def build(plan: dict, vendor: dict, profile: dict | None, prior_context: str | None = None) -> str

# aggregator/extract.py
async def extract(track: str, transcript: str | list[dict]) -> dict   # Outcome dict, never throws
def empty_outcome(track: str, note: str) -> dict

# aggregator/aggregate.py
DIAL_OVERHEAD_SECONDS: int   # from env, default 45
def aggregate(run: dict) -> dict        # run has "calls"; pure; AggregateView

# aggregator/ledger.py
def ledger_rows(call_row: dict) -> list[dict]   # pure; dated facts from outcome -> ledger rows (no id/user_id)

# events/log.py
def log_event(store: Store, user_id: str, type: str, data: dict | None = None,
              run_id: str | None = None, call_id: str | None = None) -> dict
    # insert_event, then schedule keewano send if KEEWANO_ENABLED and an event loop is running
    # (asyncio.get_running_loop().create_task) else skip silently. Never raises.

# events/keewano.py
async def send_events(user_id: str, events: list[dict]) -> bool   # truncates strings to 256; never raises
```

## FastAPI routes (server/app.py), all prefixed /api/py, JSON in/out

```
GET  /health                      -> {"ok": true, "store": "supabase"|"sqlite"}
POST /auth/signup                 {email, password (>=8), display_name?} -> {"id", "email"}. No auth.
                                  GoTrue admin create_user with email_confirm=true (secret key, server-only),
                                  then ensure_profile (+ display_name). 400 when Supabase is not configured,
                                  409 "account exists, sign in instead", 502 on other GoTrue errors.
GET  /account                     -> Dial account limits (server-side, secret stays server-side)
GET  /profile                     -> profile row (creates default if absent)
POST /profile                     {display_name?, phone?, prefs?, cohort?} -> profile row
GET  /vendors                     -> [vendor rows]
POST /vendors                     {vendors: [{key,name,track,phone,is_demo?}]} -> [rows]   (upsert)
GET  /runs                        -> [run rows]
POST /runs                        {request: str} -> assembled run (see below). maxDuration-safe: awaits the fan-out.
GET  /runs/{run_id}               -> assembled run
POST /runs/{run_id}/tick          -> assembled run (drives ingest + simulate + extraction + completion)
GET  /ledger                      -> [ledger rows]
GET  /events?limit=50             -> [event rows]
POST /mcp                         Context MCP, stateless JSON-RPC 2.0 (initialize, tools/list, tools/call).
                                  Requires Authorization: Bearer MCP_SHARED_SECRET, else 401.
                                  Tools: check_calendar_availability(date_range) and get_user_preference(key),
                                  read from profiles.prefs of header X-Dormammu-User or env MCP_USER_ID.
POST /ledger/fire                 {as_of: str} -> dispatches a retention run for due ledger rows (act two)
```

Assembled run = `{...run row, "calls": [rows], "aggregate": AggregateView, "server_now": iso}`.
Every response sets `Cache-Control: no-store`.

Auth: `server/auth.py: current_user_id(request)`. Reads `Authorization: Bearer <jwt>`, calls
`GET {SUPABASE_URL}/auth/v1/user` with headers `apikey: SUPABASE_PUBLISHABLE_KEY` and the bearer,
caches by token for 60s, returns `user["id"]`. 401 on failure. If no bearer and `DEV_USER_ID` is
set, return it (local dev only). Also accept `X-Internal-Secret: INTERNAL_API_SECRET` plus
`X-Dormammu-User` for server-to-server calls.

## Next.js (Lane B) contract

- `next.config.ts`: remove `cacheComponents` and `partialPrefetching`; add the `/api/py/:path*`
  rewrite described above. Keep the Tailwind turbopack rule.
- `lib/supabase/client.ts` (browser, `createBrowserClient`), `lib/supabase/server.ts` (cookies),
  `proxy.ts` (Next 16's middleware) refreshes the session. Email + password login at `/login`:
  "Sign in" calls `supabase.auth.signInWithPassword` in the browser (the `@supabase/ssr` client
  writes the cookie session, so `proxy.ts` sees it); "Create account" first calls
  `POST /api/py/auth/signup` via `lib/api.ts: signup(email, password, display_name?)` (no Bearer),
  then signs in the same way. After sign-in: `GET /api/py/vendors` empty -> `/onboarding`, else `/`.
  There is no `/auth/callback` route and no email is sent at any point. Onboarding at `/onboarding`
  (profile + 5 vendors via `POST /api/py/vendors`).
- `lib/api.ts`: `pyFetch(path, init)` attaches the current session's access token as a Bearer and
  `cache: "no-store"`.
- `/` landing: signed out -> link to /login; signed in -> request form (POST /api/py/runs) + run list.
- `/runs/[runId]`: THE DEMO SCREEN per BUILD-PLAN §4.11. Realtime on `calls` filtered by run_id +
  POST tick every 2s until every call is terminal. Server-authoritative clocks via `server_now`.
  Masked phones. `simulated` badge. Null-safe rows. Aggregate footer with "est." label and the
  overhead assumption visible. Ledger timeline section (GET /api/py/ledger).
- `lib/types.ts`: TS mirror of the row shapes above (hand-written is fine today).
- Dark theme, monospace numerals, no component library.

## Non-negotiables (BUILD-PLAN §8)

AI disclosure first in every instruction. Only vendor rows are dialable; the router emits keys.
No fabricated numbers; multiplier labeled "est.". Simulated rows labeled. No phone numbers or
secrets in git. RLS on every table; secret key server-only.

## Commands

```
uv run pytest -q                 # Python tests
uv run ruff check .              # lint, line-length 110
npm run build                    # Next build must pass
uvicorn server.app:app --port 8000 --reload   (via: uv run uvicorn ...)
npm run dev
```
