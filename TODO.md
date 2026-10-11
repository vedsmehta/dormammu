# What is left

Written Oct 11 2026 on branch `aarav/next-up`. The demo flow works end to end in dry-run mode
(no keys, simulated calls). Everything below is what stands between that and the real thing.

## Before a live demo (needs keys, not code)

- [ ] Put real keys in `.env`: `OPENAI_API_KEY`, `DIAL_API_KEY`, `DIAL_NUMBER`, the Supabase keys,
      `KEEWANO_TOKEN` (a Project API Key, not a personal access token) and `KEEWANO_ENABLED=true`.
- [ ] Remove `DORMAMMU_DRY_RUN=1` and `DEV_USER_ID` from `.env` once the keys are in.
- [ ] Run one real research request and check that web search returns vendors, prices and phones.
      Web research has never been run on this machine: without `OPENAI_API_KEY` the engine falls
      back to the roster and says so on the page.
- [ ] Run `uv run python scripts/smoke.py` to place one real call before any multi-call run.
- [ ] Decide on `ALLOW_EXTERNAL_CALLS`. Unset, vendors found by research are never dialed; their
      rows run as labeled simulated calls. Only roster rows marked demo are dialed.

## Built, but only verified with simulated calls

- [ ] Choose-and-book (`POST /runs/{id}/choose`): the booking call's wording has not been heard on a
      real line. Listen to one and tune the objective in `server/app.py` (`choose_option`).
- [ ] The booking call is always on the schedule track. "Set the plan up over the phone" is only an
      instruction in the objective; there is no separate outcome for "plan activated, no appointment".
- [ ] `context_applied` events are new and are not in Keewano's registered event list, so they ride
      on the fallback event id. Register them if they should be queryable by type.
- [ ] No tests yet for `choose_option`, `_booking_links` or `_loop_evidence`.

## Known problems

- [ ] Without OpenAI, the roster fallback files vendors under the wrong category (an insurance line
      shows up under Movers). Likely the same cause as the failing test
      `test_research.py::test_roster_without_categories_falls_back_to_track_categories`.
- [ ] Six dispatch tests fail when `DORMAMMU_DRY_RUN=1` is in `.env`. They pass with it unset.
- [ ] The Keewano panel on `/evidence` shows the "looks like a personal access token" hint even
      when no token is set.
- [ ] `components/home/PresentationLanding.tsx` has three type errors (`heroCopy`, `research`,
      `proof` missing from its CSS module). That file and `app/present/` came from another session.

## Product gaps against the pitch

- [ ] Profiles other than student. `COHORTS` in `lib/types.ts` and `router/cohorts.py` only have
      "Full-time student"; the pitch says veteran and others.
- [ ] The onboarding form has no fields for "my current dentist" or "my current bank". The request
      sentence can mention them, but the profile cannot store them outside "What you pay for now".
- [ ] Banking is not a research category. `CATEGORY_TRACKS` in `server/app.py` has movers,
      internet, phone, insurance, dentist and doctor.
- [ ] The dashboard does not list research waiting for approval. A proposal is only reachable from
      the redirect after "Handle it" or by its URL.
- [ ] The landing page ask box does not carry the typed request through sign-in to the dashboard.
- [ ] Call recordings: the backend stores a transcript and a duration, no audio.
- [ ] Negotiated results (a reduced bill) cannot be "chosen"; only quotes and unconfirmed
      appointment slots can.

## Housekeeping

- [ ] No frontend tests. `package.json` only has `lint` and `build`.
- [ ] GitHub issues #4 to #9 are still open although most of that work is merged.
- [ ] `router/README.md` and `aggregator/README.md` still say "TODO: not implemented".
- [ ] Form validation was added to login, onboarding and profile but not clicked through by hand.
