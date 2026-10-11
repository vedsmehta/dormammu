# Dashboard: UI and implementation plan

Owner: Shubh · Issue [#8](https://github.com/vedsmehta/dormammu/issues/8) · Branch `feature/dashboard`

Built against `BUILD-PLAN.md` §4.5–4.13 and §12 (stack, data and honesty rules),
PRD §2.1 / §4 (ledger, two-act demo), with Pine AI as the UX reference.
Where this doc and `BUILD-PLAN.md` disagree, `BUILD-PLAN.md` wins.

---

## 1. Goal

One screen that films well: a student types one sentence, N calls light up at
once, results land in a ranked table, a ledger row is written. Then "11 months
later", the ledger fires a retention call that opens with context from act one.

**The `ringing → talking` transition across several rows at once is the money
shot.** Every design decision serves making that moment unmistakable.

## 2. What we take from Pine AI, and what we don't

Reference: [19pine.ai](https://www.19pine.ai), [Pine Work](https://www.19pine.ai/work).

| Pine does | We do |
|---|---|
| One prompt: *"What do you need done today?"* | Same: one input, one button. *"What do you need handled?"* |
| Category chips: Lower My Bills, Cancel Subscriptions, Get Money Back | Example chips: *Moving into my first apartment*, *Lower my internet bill*, *Find a dentist near campus* |
| Outcome-first results: "$300/year saved", "270 minutes saved" | Same headline style, **but only real numbers**: savings from actual quotes, time as "3.8× faster (est.)" with the assumption shown (BUILD-PLAN §4.9, §8.4) |
| Quantified live progress: "12 sources reviewed, 2 completed" | Status strip: *5 calls · 3 talking · 1 done · 1 no-answer* |
| Live Monitor tab for one call | Grid of **all calls at once**. This is the differentiator; Pine is one task at a time |
| Case list with SCHEDULED / ACTIVE / READY | Ledger timeline with upcoming actions and an **Approve** button |
| "Take control whenever you want" | Out of scope today |
| Calm, minimal, one strong CTA, sans-serif | Calm and minimal, **dark** (BUILD-PLAN §4.11), monospace numerals |

Tone: short, direct, delegating. "Handle it." "Done." "Want me to call?"
Never "AI-powered magic".

## 3. Screens

Four routes. Screens 2 and 3 are the same page in two states.

### 3.1 `/`: Ask

```
┌────────────────────────────────────────────────────────────┐
│  dormammu                                   🎓 Student  ▾  │
│                                                            │
│        What do you need handled?                           │
│   ┌──────────────────────────────────────────────────┐     │
│   │ I'm moving into my first apartment near campus   │     │
│   │ on the 1st, handle it.                           │     │
│   └──────────────────────────────────────────────────┘     │
│   [Moving in]  [Lower a bill]  [Book a dentist]            │
│                                         [ Handle it → ]    │
│                                                            │
│   Upcoming · 2           ISP promo ends in 21 days  →      │
└────────────────────────────────────────────────────────────┘
```

- Cohort chip shows the active eligibility profile (`profiles.cohort`). The asks
  it injects (student rate, waive activation fee) are visible on hover, so a
  judge can see what the agent is told to claim (BUILD-PLAN §12.2).
- "Handle it" → `POST /api/runs { request }` → navigate to `/runs/[runId]`.
- The ledger teaser links to `/ledger`.

### 3.2 `/runs/[runId]`: Live (the demo screen)

```
┌────────────────────────────────────────────────────────────────────┐
│ "I'm moving into my first apartment near campus on the 1st"        │
│ 5 calls · 3 talking · 1 done · 1 ringing                ⏱ 00:47    │
├────────────────────────────────────────────────────────────────────┤
│ Sonic Internet     quote     ●●●○○  TALKING   02:14  ▓▓▓▓▓░░       │
│ Lemonade Renters   quote     ●●●●●  DONE      01:52  $9/mo         │
│ Comcast Xfinity    quote     ●●●○○  TALKING   01:31                │
│ Dr. Chen DDS       schedule  ●●●○○  TALKING   01:04                │
│ AT&T Fiber         quote     ●●○○○  RINGING   00:08  [simulated]   │
├────────────────────────────────────────────────────────────────────┤
│ Agent is asking every vendor for the student rate.                 │
└────────────────────────────────────────────────────────────────────┘
```

- One row per `calls` row. Columns: vendor (`target_name`), track, a 5-dot state
  stepper, state label, server-authoritative timer, outcome cell.
- State → visual (from `CallState` in BUILD-PLAN §4.1):

  | state | dots | label | treatment |
  |---|---|---|---|
  | planned | ○○○○○ | QUEUED | muted |
  | dialing | ●○○○○ | DIALING | muted, spinner |
  | ringing | ●●○○○ | RINGING | amber, slow pulse |
  | talking | ●●●○○ | TALKING | **green, row glows, live waveform bar** |
  | extracting | ●●●●○ | READING CALL | blue shimmer |
  | done | ●●●●● | DONE | outcome fades in |
  | failed | ✕ | NO ANSWER / FAILED | red, outcome "—" |

- `ringing → talking`: row background flashes green once, waveform starts, a soft
  tick sound is optional (off by default). This must be visible at a glance on video.
- Expanding a row shows the masked number (`+1 (415) ***-**67`), the objective,
  `prior_context` if any, and the transcript once available.
- `simulated: true` rows always show a **[simulated]** badge (§8.5).

### 3.3 `/runs/[runId]`: Results (same page, once every call is terminal)

```
┌────────────────────────────────────────────────────────────────────┐
│  Done. 5 calls in 2m 51s · 3.8× faster than calling one by one (est.)│
│  You save $216/yr with student rates.                              │
├────────────────────────────────────────────────────────────────────┤
│  INTERNET                     price      student    you save       │
│  ▸ Sonic Internet   BEST      $50/mo     $40/mo     $120/yr        │
│    Comcast Xfinity            $65/mo     $55/mo     $120/yr        │
│    AT&T Fiber  [sim]          $70/mo     —          —              │
│  RENTERS INSURANCE                                                  │
│  ▸ Lemonade         BEST      $12/mo     $9/mo      $36/yr         │
│  DENTIST                                                            │
│    Dr. Chen DDS     ✓ Booked  Tue Nov 4, 10:30am                    │
├────────────────────────────────────────────────────────────────────┤
│  Added to your ledger: Sonic promo ends Oct 2027 · Dentist Apr 2027│
│  est. assumes 45s dial overhead per call when done one by one      │
└────────────────────────────────────────────────────────────────────┘
```

- Ranking comes from the aggregator (`aggregate()`, BUILD-PLAN §4.9). The UI does
  not re-rank. Nulls sort last and are never BEST.
- The "student" and "you save" columns render **only when the outcome carries
  both prices**. Today's `Outcome.quote` has a single `amountUsd`; see §8 open
  question 2. Without it the table shows one price column and the caveats.
- The multiplier is labeled **est.** and the overhead assumption is printed
  underneath, every time.
- The page stops ticking once every call is terminal, so it sits still for filming.

### 3.4 `/ledger`: Act two

```
┌────────────────────────────────────────────────────────────────────┐
│  Your accounts                                       🎓 Student    │
│                                                                    │
│  ● in 21 days   Sonic Internet promo ends       $40/mo → $50/mo    │
│                 Want me to call and keep the rate?  [ Approve ]    │
│  ○ Apr 2027     Dr. Chen cleaning due                [ Rebook ]    │
│  ○ Aug 2027     Lemonade renters renewal            $9/mo          │
│                                                                    │
│  Every entry links to the call it came from.                       │
└────────────────────────────────────────────────────────────────────┘
```

- Rows from `ledger`, ordered by `expires_on`. Entries within 30 days are
  highlighted and get the action button (issue #8).
- Each row links to `source_call` (the transcript), which is what makes it defensible.
- **Approve** starts a `negotiate` run for that vendor and navigates to its live
  page, so act two reuses the same screen. If the dispatch path isn't ready, the
  button is a visible no-op with a "coming soon" toast (issue #8 allows a no-op).
- The 11-month jump is a **seeded date**, never a faked result. Show a small
  "date seeded for demo" note when the seed is active (BUILD-PLAN §12.3).

### 3.5 Auth and onboarding (minimal)

`/login` (magic link), `/auth/callback`, `/onboarding` (enter the 5 vendor names
and phone numbers into `vendors`; numbers never touch git). Plain forms, no
design effort beyond matching the theme. Be signed in before filming (§4.12).

## 4. Visual design

- **Theme:** dark-first, calm. Near-black background, one accent green for
  "talking"/success, amber for ringing, red for failed, muted gray for everything
  else. No gradients, no illustrations.
- **Type:** Inter (UI) + JetBrains Mono (timers, prices, state labels), both from
  Google Fonts with system fallbacks. All numbers use tabular figures so timers
  don't jitter.
- **Layout:** single centered column, max ~960px, generous whitespace (Pine's
  calm). The live table is full width of that column.
- **Motion:** only state changes animate. Pulse on ringing, one-time flash on
  talking, fade-in on outcome. Respect `prefers-reduced-motion`.
- **Components (hand-built, no component library):** `AskBox`, `CohortChip`,
  `StatusStrip`, `CallRow`, `StateDots`, `Timer`, `OutcomeCell`, `SimulatedBadge`,
  `ResultsTable`, `HeadlineMetrics`, `LedgerTimeline`, `LedgerRow`.
- Must read at phone width (stack the row: vendor + state on top, timer + outcome below).

## 5. Implementation

### 5.1 Stack (fixed by BUILD-PLAN §0, §2.5, §4.11)

- **Next.js 15 App Router**, TypeScript, **Tailwind v4** (`@import "tailwindcss"`,
  no `tailwind.config.js`).
- **Supabase**: `@supabase/ssr` for auth, browser client for Realtime reads.
  Service-role key server-only.
- Deployed to **Vercel**, with Deployment Protection off.
- Lives in `dashboard/` (this folder) so it stays separate from the Python core.
  *To confirm with Vedant: BUILD-PLAN's `app/**` paths are written as if at the
  repo root.*

### 5.2 File layout

```
dashboard/
  app/
    layout.tsx, globals.css
    page.tsx                         Ask
    runs/[runId]/page.tsx            Live + Results
    ledger/page.tsx                  Ledger
    login/page.tsx, auth/callback/route.ts, onboarding/page.tsx
    api/runs/route.ts                POST dispatch      (Lane A owns logic; I wire the call)
    api/runs/[runId]/route.ts        GET run snapshot   (force-dynamic, no-store)
    api/runs/[runId]/tick/route.ts   POST ingest tick   (Lane A owns logic)
  components/                        the list in §4
  lib/
    supabase/{client,server}.ts
    db.types.ts                      generated from Supabase, never hand-written
    view.ts                          state → dots/label/color; phone masking; time formatting
    fixtures.ts                      demo-mode data (see 5.4)
  middleware.ts                      session refresh
```

### 5.3 Live data flow on `/runs/[runId]`

1. Server component loads the run + calls once (first paint, no spinner).
2. Client subscribes to Supabase Realtime on `calls` filtered by `run_id`.
3. Client `POST`s `/tick` every 2s **while any call is non-terminal**; the tick's
   response is also the render fallback if Realtime drops (§4.6, §4.11).
4. Merge updates by `id`, **ignoring any update with a lower `state_rank`**
   (monotonic, review 14A), so a late packet can't move a row backwards.
5. Timers: `serverNow - started_at`, interpolated locally between ticks. Never
   compare `Date.now()` against server timestamps (§4.11).
6. When all calls are terminal: stop ticking, unsubscribe, render Results.

Rows must render with a null outcome, a null amount, and a failed state.

### 5.4 Demo mode (so I can build before the backend exists)

- `?demo=1` (or `NEXT_PUBLIC_DEMO=1`) swaps the data source for `lib/fixtures.ts`:
  a scripted timeline that moves 5 rows through `planned → dialing → ringing →
  talking → extracting → done/failed` over ~90s, plus a seeded ledger.
- Same components, same `view.ts` mapping, same types as real data, so nothing
  is thrown away when Supabase is wired.
- Fixture rows are flagged `simulated: true` and show the badge. This is a
  frontend dev tool, separate from Lane A's `lib/simulate.ts`, which writes real
  labeled rows to Postgres when Dial's concurrency cap is hit.

## 6. Milestones

| # | Deliverable | Depends on | Est. |
|---|---|---|---|
| M1 | Next.js + Tailwind v4 scaffold in `dashboard/`, theme tokens, fonts | nothing | 20m |
| M2 | Live screen in demo mode: `CallRow`, `StateDots`, `Timer`, `StatusStrip`, the talking transition | M1 | 60m |
| M3 | Results view + Ledger page in demo mode | M2 | 45m |
| M4 | Ask screen + cohort chip, wired to demo run | M1 | 20m |
| M5 | Supabase: auth, server snapshot route, Realtime + tick loop | Lane A: schema, `/tick`, `/api/runs` | 45m |
| M6 | Approve → retention run; phone masking; reduced-motion; phone width | M3, M5 | 30m |
| M7 | Film pass: freeze-on-terminal check, simulated badges, est. label | all | 15m |

**Cut order if time runs out:** M6 phone width → expandable transcript →
Ask screen chips → ledger Rebook button. The live grid (M2) and results (M3)
are never cut. If M5 slips, film the demo-mode screen and say so in the
voiceover (simulated rows are labeled, §8.5).

## 7. Acceptance criteria

From issue #8 and BUILD-PLAN §4.11:

1. Three or more dispatched calls show transitioning states without a page reload.
2. New call updates appear within ~2s.
3. The ledger shows a promo-expiry entry with a visible Approve action.
4. Empty run and missing ledger render cleanly, with no errors.
5. Timers never go negative or jump (server-authoritative clock).
6. Simulated rows are badged; phone numbers are masked; the multiplier says "est."
7. The page stops polling once every call is terminal.

## 8. Open questions

1. **Where the Next.js app lives:** `dashboard/` (proposed) vs. the repo root as
   BUILD-PLAN's paths imply.
2. **Student vs. standard price:** `Outcome.quote` has one `amountUsd`. Showing
   "you save" needs either a second field (e.g. `standardAmountUsd`) from the
   extractor or two quote rows per vendor. Needs Lane A/aggregator agreement.
   Until then the table shows one price.
3. **Theme:** BUILD-PLAN §4.11 says dark. Pine is light. Plan follows BUILD-PLAN;
   say if the team wants a light Pine-style look instead.
4. **Approve in act two:** real `negotiate` dispatch (preferred) or no-op for
   today (issue #8 minimum).
5. **Who owns `/api/runs` and `/tick`:** BUILD-PLAN puts their logic in Lane A;
   this plan assumes I only call them and render the result.
