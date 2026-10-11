# How we demo this well

Record at 15:15. Submit by 15:45. Hard cutoff 16:00. Four people, five phones, one screen.

## Status update, 15:35 PDT

Since this report was written at 14:35, the pending items resolved as follows. Treat this block as
the override where the body below says "pending".

| item | state at 15:35 |
|---|---|
| /evidence page | live |
| /insights page | live |
| /calendar page | live, in the nav |
| /calls on GET /calls | live |
| Restyle to Aarav's design | complete, all screens |
| Research stage API | live: POST /research, GET /proposals, POST /proposals/{id}/approve |
| Research proposal screen | not built (hard stop); use the API or the roster path |
| Onboarding intake fields | present on /onboarding; /profile preserves but does not edit them |
| Seeded history (demo user) | 121 runs, 332 calls, 128 ledger rows, 954 events in Keewano |
| Extraction eval | 24 of 24 exact, public/eval-report.json |
| Insights report time | 1.2 s cold, cached 60 s |

Demo sign-in: demo@dormammu.dev. The ask box on the dashboard still goes straight to the roster
fan-out (act one). The research stage can be shown from the API or skipped in the video.

Everything below was checked against this tree at 14:35 PDT on Oct 11 2026. Where a page is still
being built by another lane it says "pending" and names the route it will read from, so nobody
has to guess on camera.

| checked at 14:35 | result |
|---|---|
| Dial maxConcurrentCalls | 2 |
| Dial maxCallDurationSeconds | 300 |
| Keewano project | SpeedHacks26 |
| Keewano custom types registered | 16 |
| Keewano check batch | HTTP 202, 2 accepted |
| Keewano SQL visibility right after | 0 rows (lag) |
| Demo roster (vendors, is_demo) | 5 |
| Ledger rows for the demo user | 0 |
| Postgres events rows | 25 |
| pytest | 208 passed, 9 skipped |

## 1. The pitch and the use case

One sentence: **a Stanford student types one line about moving to Palo Alto, and five phone
calls happen at once: three mover quotes, internet at the student rate, and a dentist booked,
ranked and written into a ledger that calls the vendor back when the promo ends.**

The use case is specific on purpose. Not "handle my move". A full-time student, on a student
budget, moving into a first apartment near campus on November 1, who has never asked a vendor for
a student rate and does not know it exists. Every call the agent makes opens the same way, asks
for the three things the student cohort is entitled to ask for (student rate, waived activation
fee, no-contract term), and closes with a spoken recap the extractor reads back.

What makes it a product and not a party trick is act two: eleven months later the ledger fires
on its own and the agent calls Comcast retention opening with what Comcast conceded last time.

The roster the demo user already has, all five flagged `is_demo`, all five phones in the room:

| key | name | track |
|---|---|---|
| movers_a | Pack & Go Movers | quote |
| movers_b | BearHug Moving | quote |
| movers_c | Golden Gate Movers | quote |
| dentist | Dr. Chen Dental | schedule |
| internet_retention | Comcast Retention | negotiate |

Optional, only if done before 15:00: rename Golden Gate Movers to something Peninsula-flavored on
`/onboarding` (upsert by key keeps the vendor id). After any rename re-run the cache warm in
section 6. Do not touch the roster after 15:00.

## 2. The two-act script

Target length 3:30 to 4:15. If the cut runs long, drop the evidence tail first, never act two.

The exact line to type, character for character (warm it in the plan cache first, section 6):

```
I'm a Stanford student moving to Palo Alto on November 1 on a student budget. Get three mover quotes, internet at the student rate, and a dentist appointment.
```

### Act one: the fan-out

| time | screen | beat |
|---|---|---|
| 0:00 | / ask box | type the line |
| 0:12 | phones, one frame | two ring |
| 0:25 | /runs/[id] | 2 live, 3 simulated |
| 0:35 | phone + board | mover call audio |
| 1:40 | /runs/[id] | rows go done |
| 1:55 | /runs/[id] results | ranked table, Best |
| 2:05 | /runs/[id] footer | est. multiplier |
| 2:15 | /runs/[id] aside | ledger rows |
| 2:25 | /ledger | row 11 months out |

Beat by beat, with the words.

**0:00, the ask box.** Signed in, `/` shows the dashboard with the ask box ("What do you need
handled?"). Driver says: "Moving to Palo Alto as a student is five phone calls during business
hours. Here is one sentence instead." Type the line. Press Enter. The POST awaits the router and
Dial accepting the calls, so the page sits for 3 to 10 seconds before it jumps to the run board.
The phones start ringing during that wait. Camera two is already on the phones.

**0:12, the phones.** Two phones ring. Driver says: "Two lines are live on Dial right now. This
account allows two concurrent calls, so the other three rows are labeled simulated and run a
scripted timeline. Nothing simulated is ever shown as a real quote." Say the word "simulated" out
loud here, once, clearly.

**0:25, the board.** `/runs/[id]`: five rows, masked phones, two moving dialing to ringing to
talking, three carrying the orange `simulated` chip. Point at the chip.

**0:35, one call's audio, clean.** Whichever mover phone rang, that owner answers in persona.
The agent's first words are fixed by the instruction builder:

> Agent: "Hi, this is Alex, am I speaking with BearHug Moving?"
> Answerer: "Yes, this is BearHug."
> Agent: "I'm an AI assistant calling for Vedant."

Then the ask. The answerer's lines (keep it to 60 to 90 seconds):

> "A two-bedroom on November first, Palo Alto, we'd be at twelve forty flat. Truck, two movers,
> basic coverage. Packing materials are extra."
> (when asked about student rate) "Ten percent off with a dot-edu email, so eleven sixteen."
> (when asked about dates) "The first and the second are open."

The agent closes with "Just to confirm, ..." and repeats the price. That recap is what the
extractor reads and what the accuracy check compares against.

The second live phone is answered at the same time by its owner, quietly, off the main mic.
If it is Comcast: "The student plan is forty-five a month. I can take twenty dollars a month off
for twelve months, no contract, and I'll waive the activation fee." The words "twenty dollars a
month" and "twelve months" matter: the extractor needs the amount and the ledger reads the
duration off the call. If it is the dentist: "Thursday October fifteenth at two thirty is open."
That is inside the profile's weekday-afternoon availability.

**1:40, rows go done.** Live rows go extracting then done as Dial's transcript lands (up to 90 s
after hangup). Simulated rows finish on their own timeline (about 75 s talk plus 11 s of dialing
and ringing). Do not touch the page. The board stops ticking when every row is terminal.

**1:55, the ranked table.** The results block ranks quotes ascending, cheapest gets `Best`,
simulated rows keep their chip in the table. Driver says: "Ranked, cheapest first, and each row
says whether it was a real call."

**2:05, the footer.** Read it exactly as written on screen: "N seconds wall clock, M minutes
talking, X times faster, est." Then: "Est. because the sequential number assumes forty-five
seconds of dialing and hold per call. The assumption is printed next to the number." Expect
roughly 4 to 6 times with two real calls and three simulated. Whatever it says is what you say.

**2:15, landing in your ledger.** The aside lists the dated facts this run wrote: three quotes
with 30-day validity, the appointment on its day, and the Comcast promo row. Click "Open the
ledger".

**2:25, the ledger.** `/ledger` shows upcoming rows with their dates. Point at the Comcast row:
"promo rate $20/mo off (12-month promo stated on call)", expiring eleven months after it takes
effect. Driver says: "This is the asset. The move is the acquisition moment; the ledger is why
the student comes back."

### Act two: eleven months later

| time | screen | beat |
|---|---|---|
| 2:40 | /ledger | due row, seeded |
| 2:50 | /ledger | Fire due ledger |
| 2:55 | phone + board | one call, Comcast |
| 3:05 | phone audio | opens with last year |
| 3:30 | /calls/[id] | prior context block |

**2:40, the due row.** The ledger header reads "as of · seeded to today". A seeded Comcast row
(section 5 says how it gets there) is already due: "Due · Oct 1, 2026". Driver says: "We are not
faking eleven months. This row was seeded with a past expiry before filming and the screen says
so. The clock is set and labeled."

If the seeded row is absent, change the as-of date instead. The label flips to "overridden" and
the page prints "Seeded date override: the ledger is evaluated as of ..., not today. No elapsed
time is being faked." Read that sentence out loud. Note the cost of this path in section 5.

**2:50, fire.** Click "Fire due ledger (1 row)". The POST awaits the fan-out and returns the new
run. Open it from the dashboard if the page does not jump.

**2:55, one phone rings.** The Comcast phone. One row on the new board, request text
"Retention: Comcast Retention promo rate ...", track negotiate.

**3:05, the opening.** Fixed by the instruction builder plus the ledger row:

> Agent: "Hi, this is Alex, am I speaking with Comcast Retention?"
> Answerer: "Yes."
> Agent: "I'm an AI assistant calling for Vedant."
> Agent: "Last year you quoted $20 for Comcast Retention promo rate $20/mo off ..."

The context string is built verbatim from the ledger row by `POST /ledger/fire`
("Last year you quoted <amount> for <label>") and handed to the agent as "bring up last time in
one sentence", so expect a paraphrase of it, not the label read out. The objective that follows
is "Our promotional rate ends <expires_on>; keep the rate or get a better one". Answerer: "I can
extend the twenty off for another twelve months." Agent recaps. Driver says: "That one sentence
is the retention claim. It remembered what this vendor conceded and led with it."

**3:30, the call page.** `/calls/[callId]` for the retention call: the "prior context" line under
the objective, the transcript with the "Just to confirm" turn, the outcome cell reading reduced
$20/mo, and "In your ledger" showing the row that fired it. Hold for three seconds.

### Evidence tail (3:40 to 4:15, cut first if long)

Quick cuts, two to four seconds each, in this order: preflight terminal (limits), Supabase
`calls` table with the `dial_call_id` column, Keewano event catalog with our 16 names, one
Keewano MCP query result, `pytest` summary, the RLS lines of the migration. Section 3 says where
each lives.

## 3. Evidence beats

The judges care that Dial and Keewano are used for real. Every beat below is something on a
screen that exists now, or a route that exists now with its page pending.

### Dial

| evidence | where | point at |
|---|---|---|
| account limits | preflight terminal | maxConcurrentCalls 2 |
| account limits | GET /api/py/account | limits |
| real dial_call_id | Supabase, calls table | dial_call_id column |
| real dial_call_id | DevTools, tick response | dial_call_id |
| real calls list | GET /api/py/evidence | dial.real_calls |
| smoke transcript | smoke.py terminal | transcript lines |
| 429 became simulated | Supabase, events table | call_simulated, limit true |

Lines to say: "Those are Dial call ids on the two live rows. The three without one are the rows
Dial refused with a 429 at the concurrency cap, and the system turned that refusal into a labeled
simulated row instead of a red failure." The `/evidence` page is pending; until it lands, the
Supabase Table Editor or the DevTools Network tab (any `tick` response, JSON view) shows the same
field. The smoke transcript is the terminal output of `scripts/smoke.py`, phones masked; keep that
terminal open on a second desktop.

### Keewano

| evidence | where | point at |
|---|---|---|
| ingest accepted | keewano_check terminal | HTTP 202, accepted 2 |
| 16 typed ids registered | Keewano console, event catalog | run_requested ... run_multiplier_x100 |
| 16 typed ids registered | MCP list_event_types | same names |
| live query | MCP run_sql_query | funnel row |
| sink state | GET /api/py/evidence | keewano |
| system of record | GET /api/py/events | type, data |

Lines to say: "Every lifecycle event goes to Postgres first, then to Keewano as a typed event:
run requested, call dispatched, connected, terminated, outcome extracted, run aggregated, ledger
written, plus eight numeric companions so Keewano can group by track, vendor, duration and
amount. Keewano is where we ask what the data says across runs. The prior-context sentence the
agent spoke in act two came from our store, built from the same events."

The live query, run from the Keewano MCP in a Claude Code terminal or from the Keewano console
(project id `f21f5584-84af-4c5a-88bb-b4cbace12f54`):

```sql
SELECT count(call_dispatched) AS dispatched, count(call_connected) AS connected,
       count(call_terminated) AS terminated, count(outcome_extracted) AS extracted,
       count(DISTINCT user_id) AS users
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
```

And the one that uses the typed companions:

```sql
SELECT call_track AS track, call_simulated AS simulated,
       count(call_duration_seconds) AS calls, sum(call_duration_seconds) AS talk_seconds
FROM events
WHERE event_datetime BETWEEN '2026-10-01' AND '2026-12-31'
GROUP BY track, simulated ORDER BY track, simulated
```

Track codes: 1 quote, 2 schedule, 3 negotiate. Ingest is asynchronous: at 14:35 the accepted check
batch was not yet visible to SQL. Run the funnel query at 15:05; if it still returns zeros, show
the 202 from `keewano_check.py` and the catalog instead and say "ingest is accepted and queued."
Keewano's catalog numbers our types one lower than the wire id (run_requested shows 2000); the
names are what to point at.

### Accuracy

| evidence | where | point at |
|---|---|---|
| tests | pytest terminal | 208 passed |
| success per track | GET /api/py/accuracy | tracks.*.success_rate |
| real vs simulated split | GET /api/py/accuracy | split.real, split.simulated |
| recap agreement | GET /api/py/accuracy | recap_agreement.rate |
| recap agreement by eye | /calls/[callId] | "Just to confirm" turn vs outcome |
| eval report | public/eval-report.json | pending |

The `/accuracy` page is pending; the route exists and returns the numbers above. Until the page
lands, the honest on-camera version is the call page: the agent's closing recap turn and the
extracted outcome side by side. Say what the number is: "Recap agreement is a consistency check
between two readings of the same call, the extractor and the agent's spoken summary. It is not
ground truth, and seeded and simulated rows are reported in their own bucket." Never quote a
success rate without saying which bucket it is from.

### Schema, for the technical judge

`supabase/migrations/0001_init.sql`, lines 100 to 125: `enable row level security` on all seven
tables, one `own ...` policy per user table, `plan_cache` with no policy (service role only). Line
to say: "RLS from the first line, secret key server-side only, Next.js never proxies the Python
service, it calls it with the user's own Supabase token."

## 4. Roles, phones, consent

| role | person | holds |
|---|---|---|
| driver | P1 | laptop, own phone |
| phone camera | P2 | 1 phone |
| answerer A | P3 | 1 phone |
| answerer B | P4 | 2 phones |

Five vendor rows, four people: one person holds two phones. Confirm which phone is which vendor
from the masked roster that `preflight.py` prints (last digits) before the take. Only two of the
five will ring, in roughly plan order (movers first), but nobody knows which two, so every phone
owner has the persona card for each phone they hold.

Every phone: ringer on, Do Not Disturb off, Focus modes off, volume up, not on silent switch.
Say it out loud as a roll call before recording. A phone that goes to voicemail is caught by the
voicemail guard (row shows `failed / voicemail`, no price invented), which is honest and free,
but it costs the shot.

Consent, California two-party. Each answerer says this on camera, once, before the first take:

> "I'm <name>. I know this call is being recorded for a hackathon demo video and I consent."

The agent's own disclosure is separate and automatic: the second thing it says on every call is
that it is an AI assistant calling for the client.

What "simulated" means, said out loud: "Simulated means Dial refused the call at this account's
limit of two concurrent calls, so the row ran a scripted timeline with a fixture transcript. It
is labeled simulated in the row, in the transcript, in the outcome note and in every event."

What "seeded" means, said out loud: "Seeded means we inserted that history before filming. The
run text starts with [seeded] and the ledger label says seeded. The clock was set, not elapsed."

## 5. Failure fallbacks

| beat | failure | do this |
|---|---|---|
| ask box | router error, no plan | retype, Enter |
| ask box | 400 no vendors | sign in as demo user |
| phones | nobody rings | check uvicorn log, retake |
| phones | one rings, one 429 | fine, say 1 live 4 simulated |
| phones | voicemail | row says voicemail, keep going |
| board | row stuck talking | wait 90 s, transcript lag |
| board | outcome null | row shows note, transcript still there |
| footer | no estimate | say it, needs 1 done call |
| ledger | no 11-month row | Comcast call lacked amount, use seeded row |
| act two | no seeded row | fallback B or C below |
| act two | Comcast simulated | call page still shows prior context |
| evidence | Keewano SQL zeros | show 202 and catalog |
| anything | 15:35 and no edit | upload raw capture |

**Act two, how the due row gets there.** Primary: `scripts/seed_demo.py` (pending, analysis
lane) seeds history for the demo user including one `promo_expiry` ledger row for
`internet_retention` with `expires_on` already past and a visible seeded label. With the as-of
date left at today, that is the only row due: act one's quotes expire in 30 days, its appointment
is next week, its promo is eleven months out. One call fires. That is the clean act two.

Fallback B, two minutes, no code: insert the row by hand in the Supabase Table Editor, `ledger`
table: `user_id` the demo profile id, `vendor_id` the Comcast vendor id, `kind` `promo_expiry`,
`label` `Comcast Retention promo rate $20/mo off (12-month promo stated on call) (seeded)`,
`amount_usd` 20, `effective_on` 2025-11-01, `expires_on` 2026-10-01. Reload `/ledger`.

Fallback C, no seeded row at all: move the as-of date past act one's Comcast expiry. Every row
from act one is then due (quotes, appointment, promo), and the fire dispatches one retention call
per vendor, up to five, two live and three simulated, ordered by expiry, so Comcast lands last and
is likely simulated. The Comcast call page still shows the prior-context line, but no phone rings
for it. Say so. Prefer B.

**Stale run from 13:53.** The demo user has a run "I'm moving to SF next month, handle it" from
earlier today with four simulated rows parked in talking, because ticks are client-driven and
nobody kept its page open. Before recording, open its `/runs/[id]` page and leave it for two
minutes so it finishes, or it shows as "running" on the dashboard during act one. Its ledger rows
will be labeled (simulated) and are all in the future, so they do not interfere with act two.

**The 15:35 rule.** If the edit is not done at 15:35, upload the raw continuous screen capture
with system audio and submit it. Resubmit a better cut only if time remains. The form matters more
than the cut.

## 6. Pre-record checklist, in command form

Run from the repo root, in this order, 14:45 to 15:05. Replace `<profile id>` with the demo
user's id (Supabase Table Editor, `profiles`, display name Vedant, id begins 25711209).

```
git status --short
bash scripts/dev.sh
uv run python scripts/preflight.py --user-id <profile id>
uv run python scripts/keewano_check.py
uv run python scripts/smoke.py
uv run python scripts/seed_demo.py
uv run pytest -q
```

What each must print:

| command | expect |
|---|---|
| dev.sh | health ok, store supabase |
| preflight | maxConcurrentCalls 2, 5 vendors, cache hit |
| keewano_check | registration ok 2001-2016, HTTP 202, exit 0 |
| smoke | transcript appeared, Terminated completed |
| seed_demo | pending; one due ledger row, [seeded] runs |
| pytest | 208 passed |

`dev.sh` restarts the Python service. That matters today: the running process may still hold the
old personal token in its environment, and a fresh process reads the swapped `KEEWANO_TOKEN`
from `.env`. Each process registers the custom types once and starts a fresh Keewano session.

Warm the plan cache for the exact demo sentence without creating a run (preflight only warms the
SF sentence). Paste the sentence exactly as in section 2:

```
uv run python -c "import asyncio; from settings import load_env; load_env(); from server.store import get_store; from router.plan import plan; print(asyncio.run(plan(get_store(), \"<the demo sentence>\", \"<profile id>\")))"
```

Expect five plans: three quote, one schedule, one negotiate. If the router returns fewer, run it
again with the sentence unchanged; it caches on the hash of the trimmed, lowercased text, so any
edit to the sentence at record time is a cache miss and a live LLM call.

Then, in the browser, signed in as the demo user:

1. `/profile`: display name Vedant, cohort student, budget ceiling set, availability includes
   Thursday Oct 15 afternoon. The agent books under this name and brings anything above the
   ceiling back instead of accepting.
2. `/ledger`: the seeded Comcast row shows "Due". Nothing else is due.
3. Finish the stale 13:53 run (section 5) and confirm the dashboard says nothing is running.
4. Close every terminal and editor pane except the two kept for evidence shots (smoke, preflight)
   on a second desktop. Confirm phones render masked on `/runs/...` and `/calls`.
5. Phone roll call: five phones, ringer on, DND off, owner named, persona card in hand.
6. Screen capture with system audio running. Dial has no recording API; this is the only copy.

Freeze at 15:05. No code, no roster, no `.env` changes after that.

## 7. What to say if asked

**Why only two calls are real.** "This Dial account allows two concurrent calls; we read the
limit from the account endpoint before building. Calls past the cap come back 429, and we turn
that into a labeled simulated row that runs the same state machine. The product claim is the
fan-out; the account limit is a billing setting, not an architecture limit."

**Why the rows are simulated and not failed.** "A red failure would be dishonest in the other
direction: Dial did not fail, it declined at a quota. The row is labeled simulated in four places
and never ranked as a real quote without the label."

**How Keewano is used.** "Postgres is the system of record. Every event goes there first and then
to Keewano as a typed event, sixteen custom types, registered and accepting as of 14:35 today.
Keewano is where we ask cross-run questions: cheapest vendor per track over time, outcome
distribution per vendor including voicemail and no-answer, talk time per track split by live and
simulated. It surfaces patterns and supplies context. It does not rewrite prompts, and nobody
auto-edits the templates. The prior-context sentence the agent opens with is built from our store
from the same events."

**What is real today versus seeded.** "Real: the two live Dial calls in each act, their call ids,
transcripts and extracted outcomes; the Keewano ingest and registration; the Postgres events and
ledger rows those calls wrote; the tests. Simulated: rows past the concurrency cap, labeled.
Seeded: the history that makes act two fire today instead of in eleven months, labeled [seeded]
and said out loud."

**Why the multiplier says est.** "Wall clock is measured. The sequential figure is talk time plus
an assumed forty-five seconds of dialing and hold per call, and that assumption is printed next
to the number. We do not have a measured sequential baseline, so we do not claim one."

**Why our own phones.** "Two-party consent in California applies to the submitted video, and
our own phones are rehearsable. Only numbers in the user's vendors table are dialable; the router
emits vendor keys, never numbers. Real vendors are a roster change, and Dial's transferTo makes a
hand-off to the student possible after today."

**Why no retries.** "A no-answer row is honest and free. Retrying serializes the fan-out, which
is the one thing we claim."

**What accuracy means here.** "Per-track success is the share of completed calls whose outcome
carried a value: a quote amount, a confirmed slot, a reduction or cancellation. Recap agreement
is whether that value also appears in the agent's spoken closing recap. Both are reported
separately for real and simulated rows. Neither is ground truth against a human listener."

## Appendix: what is behind each screen

| screen | reads |
|---|---|
| / signed in | GET /runs, GET /runs/{id}, GET /ledger |
| / ask box | POST /runs |
| /runs/[runId] | GET /runs/{id}, POST /runs/{id}/tick, GET /ledger |
| /calls | GET /runs, GET /runs/{id} |
| /calls/[callId] | GET /runs/{id}, GET /ledger |
| /ledger | GET /ledger, POST /ledger/fire |
| /profile | GET /profile, POST /profile |
| /onboarding | POST /vendors, POST /profile |
| /evidence | pending; GET /evidence |
| /accuracy | pending; GET /accuracy |
| events view | pending; GET /events |

All routes under `/api/py`, bearer is the user's Supabase token. `GET /account`, `GET /calls`,
`GET /evidence` and `GET /accuracy` exist on the service today; their pages are the UI lane's.
