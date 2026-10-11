# Dormammu — PRD

**Built at:** SpeedHacks (a16z speedrun hackathon), Oct 11 2026, SF Tech Week
**Brief:** "Give people their time back — remove a meaningful bottleneck in how people work, learn, or access services."

## 1. Problem

Huge amounts of real-world adult life run through phone calls that are individually
low-value but collectively enormous time sinks: comparison shopping for a service,
booking something phone-gated, negotiating or canceling a bill, setting up new
accounts. Nobody does all of them well because each one means holding, navigating
an IVR, and talking to a human — repeated across every vendor you need to reach.

This bottleneck spikes hardest at life transitions: moving to a new place, starting
college, turning 18, starting a new job. In a single week you might need insurance
quotes, movers, a new ISP, a doctor, a dentist, a bank, and to cancel whatever you
had at the old address — each one its own phone call, usually during business hours,
usually on hold.

## 2. Product

Dormammu is an agentic calling system. One request from the user ("I'm moving next
month, handle it") triages everything that needs a phone call, spawns N calls
**simultaneously**, and aggregates the results back into one place.

**Target user (v1): college students**, specifically at the move-in / first
off-campus apartment moment. Why students first:

- **Densest cluster of phone-gated tasks.** ISP, renters insurance, utilities,
  a doctor and dentist near campus, and canceling it all nine months later —
  typically the first time they have done any of it themselves.
- **Least equipped to do it.** No prior quotes to anchor on, no idea what is
  negotiable, and the cohort most likely to avoid a phone call altogether.
- **A concrete lever the agent can pull.** Student rates and promos often exist
  but are only applied when asked for. The agent asks on every call.
- **Tight distribution.** One campus, one move-in week, word of mouth.

**Cohort-aware by design, not student-only forever.** The user has an
*eligibility profile* (student, senior, military/veteran, new mover, etc.) that
the router injects into every call script: which discounts to ask for, which
vendors are relevant, which proof the vendor will want. Students are cohort #1;
seniors (AARP/senior rates, Medicare-adjacent scheduling) are the obvious #2.
Adding a cohort means adding a profile, not rebuilding the product. Note the
axis is eligibility, not age as such — age is just the most common proxy.

### 2.1 Why users come back: the ledger

Move-in week is the acquisition moment, not the product. Every call Dormammu
places writes a structured record: vendor, account, rate, promo end date,
renewal date, what proof was required, who we spoke to, what worked. After the
first fan-out the user has something they have never had before — one place that
knows every account they hold and when each one next needs attention. That
**ledger** is the retained asset, and the calling agent becomes how it acts.

What brings a student back, in order of how often it fires:

| Trigger | Cadence | What Dormammu does |
|---|---|---|
| Bill came in higher than the ledger rate | Monthly | Flags the delta, offers to call and dispute |
| Recurring appointments (dentist, checkup, refills) | 1–6 months | Rebooks against the calendar |
| Open claims and disputes (deposit, refund, insurance claim) | Until resolved | Follows up on a schedule so the user doesn't have to chase |
| Intro/promo rate expiring | ~12 months | Calls retention before expiry, re-shops in parallel if they won't hold the rate |
| Eligibility re-verification (student status, renewals) | Each term/year | Reminds, collects proof once, resubmits to every vendor that needs it |
| Academic-calendar events (summer away, study abroad, move-out) | Each term | Pause, transfer, or cancel every account in one fan-out, because the ledger already lists them |
| Graduation | Once | Profile flips from `student` to `new grad`: student rates end, so re-shop everything |

The interaction model is **approve, not operate**: Dormammu notices the trigger
and sends "your internet promo ends in 21 days, want me to call?" The user taps
yes. Retention is measured in actions taken on the user's behalf per month, not
app opens.

Two things compound with use: the ledger gets more complete (higher switching
cost), and call outcomes across users show what a given vendor actually concedes
and what students nearby actually pay (see §6), which makes each next call better.

**Without Dormammu:** see appendix — money and time leak out continuously through
tasks that are individually not worth a person's attention but collectively add up,
because the only path today is doing each call yourself, sequentially.

## 3. Scope for today (all three tracks)

1. **Comparison shopping** — dispatch simultaneous calls to multiple vendors
   (movers, insurance, ISPs, etc.), return a ranked quote table.
2. **Appointment scheduling** — call phone-only booking lines (doctor, DMV-adjacent
   services, repair shops), check the user's calendar, lock in a time.
3. **Bill negotiation / cancellation** — call a vendor to negotiate a rate or cancel
   an old service.

All three share the same core loop: triage → dispatch N calls in parallel → per-call
agent executes its objective → aggregator normalizes results → user sees one
combined outcome.

**SMS fallback:** if a target doesn't answer after N failed call attempts within a
window, Dormammu switches that target to a full conversational agent over SMS
instead of a one-shot "please call us back" text — same negotiation/scheduling
objective, carried on over text. This also extends naturally to targets where text
is simply the better channel (e.g. a vendor that explicitly prefers SMS), not only
as a last resort for unreachable ones.

The eligibility profile (§2) rides along through that loop: the router adds
cohort-specific asks to each script ("is there a student rate?"), and the
aggregator shows the quote with and without the discount so the saving is visible.
For today only the `student` profile is built; the profile is a plain config
object so a second cohort can be shown as a one-line swap if time allows.

**Ledger (§2.1) scope for today:** every completed call writes a ledger entry
(vendor, rate, promo end, renewal date, next action), and the dashboard shows
those entries as a timeline of upcoming actions. One trigger is wired end to end:
promo expiry → notification → user approves → retention call. Bill ingestion
(email/bank), claim tracking, and the other triggers are roadmap, not built.

## 4. Demo mechanic

Submission is a **recorded video**, not a live on-stage call. This removes the
live-call risk flagged in earlier review (hold times, real-time consent law,
IVR unpredictability eating a timed demo slot) — calls can be placed ahead of time
against our own controlled test line(s) and/or real low-risk targets, recorded, and
edited into the submission. Still show the parallel-dispatch moment on screen
(N calls, concurrent status, results landing) since that's the actual thesis.

**Demo scenario:** a student types "I'm moving into my first apartment near
campus on the 1st, handle it." Dormammu fans out ISP quotes, renters insurance
quotes, and a dentist booking in parallel, and the results table shows the
student rate each vendor gave only because the agent asked.

**Second act (the retention beat):** cut to "11 months later." The ledger
timeline shows the ISP promo expiring in 21 days. The user taps approve,
Dormammu calls retention on the test line and keeps the rate, and the ledger
updates. This is what answers "isn't this a one-time tool?"

## 5. Core services

| Need | Choice | Why |
|---|---|---|
| Calling / telephony + voice agent | **Dial** (getdial.ai) | Sponsor tool. Provisions agent phone numbers AND runs the conversational layer itself (STT/TTS/turn-taking via Deepgram Flux, LLM-driven dialogue) from a plain-language instruction string — no separate voice-agent layer needed. MCP server + SDK already connected to this repo. Considered and dropped QuickVoice (a real OSS Retell-alternative) since it's a peer/competitor to Dial's own voice agent, not an add-on to it, and would've been redundant integration work. |
| SMS / texting agent | **Dial** (same SDK, SMS capability) | Powers the full-conversation SMS fallback — same negotiation/scheduling objective as the voice agent, carried over text for unreachable or SMS-preferring targets. |
| Calendar | **Google Calendar API** | Obvious choice for scheduling track. Use a Desktop-app OAuth client for fastest local setup; add team Google accounts as OAuth test users to avoid `403: access_denied`; keep `credentials.json` named exactly that per Google's sample code. |
| Mock vendor data (comparison shopping) | **Our own fixture data + our own test phone line(s)**, not a third-party sandbox | Full control, zero live-dependency risk, demoable and rehearsable. A hand-built JSON of 4-5 vendor quotes read back via a simple IVR/TTS stub we control. |
| Agent-performance analytics / continuous improvement | **Keewano (KeewanoDB)** | Core MVP feature — see §6. |

## 6. Keewano integration (core MVP, not a stretch goal)

Keewano's current product (KeewanoDB, launched Sept 2026) is a general event-sequence
database for AI agents, not gaming-locked despite the "gaming analytics" label —
agents are first-class entities in its data model.

**What it does for Dormammu:**
- Every call (each caller-agent, each turn, each tool-call, each outcome) is logged
  to KeewanoDB as an ordered event sequence per entity (one entity per call/agent run).
- Use **Keewano MCP for Agents** to query those sequences for patterns: which event
  sequences precede a failed negotiation vs. a successful one, which IVR navigation
  paths dead-end, which call scripts get hung up on.
- Feed those patterns back into the per-track call scripts/prompts — this is the
  "continually improve the agentic stack" loop: Keewano powers the analysis step,
  we close the loop by updating prompts/logic based on what it surfaces.
- The same sequences, aggregated across users, are what make the ledger (§2.1)
  smarter over time: what a given vendor's retention line actually concedes, and
  what rate comparable students ended up with.
- Honest scope note: Keewano analyzes and surfaces patterns, it does not
  auto-rewrite prompts. The improvement loop is Keewano-informed, not fully
  autonomous — state this plainly in the pitch rather than overclaiming.

**Setup:** install `@getdial/sdk`-equivalent via Keewano's TypeScript SDK
(`Keewano.init({ apiKey })`), ship call events via SDK or REST, query via MCP/Lua.
No hackathon-specific quickstart page found for Keewano (unlike Dial's) — check
their booth/Discord at the event for a demo API key or sandbox dataset.

## 7. Competitive landscape

- **Pine AI (19pine.ai)** — closest direct competitor. AI voice agent that calls
  Comcast/Verizon/AT&T/etc. to negotiate/cancel. $25M Series A. Self-reported 93%
  success rate, $3M saved aggregate (treat as their claim, not verified fact).
  Scoped to billing/subscriptions only, sequential.
- **Billshark, Trim, Rocky** — established bill-negotiation apps, mostly human- or
  semi-automated, not full voice-agent-led.
- **DoNotPay** — operating under a Jan 2025 FTC final order over "AI lawyer"
  marketing claims. Cautionary note: do not overclaim capabilities/success rates
  in Dormammu's own pitch, given regulatory scrutiny of this exact category.
- **Wajo AI ("Fo")** — general task assistant combining calendar + calling + quotes,
  human-in-the-loop fallback. Architecturally closest to our multi-track scope.

**Differentiation:** every competitor found is single-purpose and sequential (one
call at a time, one domain — usually bills). Dormammu's fan-out mechanic — N
simultaneous calls across arbitrary phone-gated tasks, aggregated back into one
result — was not found in any competitor. That is the core unique claim.
Secondary: none of them are cohort-aware. They negotiate the generic rate rather
than asking for the discounts a specific user is actually eligible for.

## 8. Open questions / risks

- Verify any third-party mock-data APIs (e.g. insurance quote sandboxes) are
  actually reachable before depending on them; prefer our own fixtures.
- Keewano hackathon-day setup (API key, sandbox dataset) unconfirmed — check with
  Keewano reps at the event.
- Recording-consent rules apply even for a pre-recorded demo if real vendors/humans
  are called and included in the submitted video — get verbal consent on-call or
  use our own test line's human to stay safe.

- Student discounts are often claimed online (UNiDAYS/SheerID-style verification),
  not by phone. The pitch should not lean on "we unlock student discounts" alone;
  the discount is a lever inside calls that are phone-gated for other reasons.
  Fixture vendors should each have a student rate that only surfaces when asked.
- The monthly trigger (bill higher than ledger rate) needs bill data we don't
  have today: email parsing or a bank/card connection. Without it, the triggers
  we can fire from call data alone are date-based (promo end, renewal,
  appointment recurrence), which are infrequent. Be honest in the pitch that
  monthly cadence depends on that ingestion step.
- Risk of the ledger being a passive list nobody opens. The mitigation is that
  it pushes actions to the user rather than waiting to be checked.
- Vendors may require proof of enrollment the agent cannot supply mid-call. For
  the demo, have the agent capture "what proof is needed" as a follow-up item.

## Appendix: without Dormammu

- Full price on every renewal because negotiating means holding, so people don't.
- Comparison shopping means calling 3-5 places yourself over several days.
- Phone-gated bookings mean calling during business hours or giving up and taking
  whatever's most convenient instead of best.
- Old subscriptions stay active for months because canceling requires a call people
  keep putting off.
- Nobody tracks all of this against actual spend, so overpayments sit unnoticed
  across many separate bills.
- Net effect: money and time leak out continuously, in amounts too small
  individually to act on, but large in aggregate — especially at life transitions
  when many of these hit at once.
