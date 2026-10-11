# Demo runbook (record 15:15, submit 15:45, hard cutoff 16:00)

## Before you hit record

1. Both servers up from the repo root:
   `uv run uvicorn server.app:app --port 8000` and `npm run dev`. Open http://localhost:3000.
2. Signed in already. Roster seeded on `/onboarding` with the five phones in the room
   (`is_demo` checked). Each phone owner named out loud: ringer on, DND off.
3. `uv run python scripts/preflight.py` once: prints the Dial limits (expect
   `maxConcurrentCalls: 2`), lists the roster, warms the plan cache with the exact demo sentence.
4. Close every terminal and editor pane. Confirm phones render masked on `/runs/...`.
5. Screen plus system audio capture running. There is no recording API on Dial; this is the only
   copy of the call audio.

## Act one: the fan-out

1. Type exactly: `I'm moving to SF next month, handle it`. Enter.
2. Phones ringing at once, all in one frame. Two rows are live (the account cap), three carry
   the **simulated** badge. Say so on the voiceover.
3. One call's audio, clean. Each answerer holds a deliberate two-minute conversation and says a
   price or a time out loud. The agent closes with "Summary: ...".
4. Rows go green, the quote table ranks itself, BEST tag lands on the cheapest.
5. The footer: wall clock, talk time, "N.N× faster (est.)" with the overhead assumption visible.
6. The ledger section on `/`: one row with an `expires_on` eleven months out.

## Act two: eleven months later

1. On `/`, the "fire due ledger" control. Set the date past the `expires_on` and say that the
   date is overridden. Do not fake the eleven months.
2. The retention call dispatches on its own. The agent opens with context from act one:
   "Last time you quoted ..." That sentence is the whole prior-context claim.

## One shot of the schema

`supabase/migrations/0001_init.sql` or the `events` table in the Supabase dashboard. The repo
is going to judges; RLS is the first thing a technical judge checks.

## If something fails

- Nobody answers: the row shows `failed / no-answer` or `voicemail`. Honest and free. Keep going.
- Extraction returns nulls: the row renders `—` with the note. The transcript is still there.
- Edit not done by 15:35: upload the raw continuous capture and submit it.

## Consent

California is two-party consent. Everyone whose voice is in the submission says so on the
recording.
