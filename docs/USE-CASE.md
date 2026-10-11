# The use case: a Stanford student moving off campus

One sentence in:

> I'm moving to Palo Alto on October 25 for the school year, 1-bedroom, handle the move, internet and a dentist.

The user is a full-time Stanford student taking their first off-campus apartment for the fall, on a
student budget. Every vendor on the roster is one of the phone calls that move forces: movers to
compare, an internet provider to set up at the new address, a dentist near campus to see as a new
patient. Dormammu places all of those calls at once and brings the answers back into one table.

This document is the product truth for the router and the instruction builder (`router/`). The
code names this scenario family in its system prompt, parses the concrete details out of the
request without a model, and writes them into every objective. Nothing below is aspirational:
each line maps to a function and a test.

## The fan-out

| Track | Vendor | What the agent asks for | Student ask |
|---|---|---|---|
| quote | each mover | all-in price, flat or hourly, stairs, packing, open dates | move discount if any, deposit at most 10 percent |
| negotiate | internet provider | new-customer promo, fee waived, no contract, install date | student rate, first month free if offered |
| schedule | dentist | earliest new-patient cleaning inside availability | student insurance or self-pay rate |

Concretely, for the sentence above with the demo roster (two movers, one provider, one dentist):

- Movers: "Get an all-in quote from BearHug Moving to move a 1-bedroom to Palo Alto on October 25:
  flat or hourly, whether stairs or a long carry add anything, whether packing is extra, and what
  dates they have open."
- Internet: "Set up internet with Comcast at the client's new address in Palo Alto for move-in on
  October 25: ask for the current new-customer promotion, the student rate, no contract, the
  installation fee waived, and confirm the install date."
- Dentist: "Book the earliest new-patient dental cleaning with Dr. Chen that fits the client's
  availability, around the move on October 25, and get the self-pay price if insurance isn't
  accepted."

Each objective then gets the cohort's track-specific asks appended once ("Ask for: ...", from
`router/cohorts.py`), so a judge can read exactly what the agent is told to claim.

### Details carried from the request

`router.plan.parse_request_details(request)` pulls out the date, the bedroom count, the origin, the
destination and the budget. It handles "October 25", "Oct 25th", "25 October", "10/25", "the 1st",
"next month"; "1-bedroom", "1BR", "two bedroom", "studio"; "moving to Palo Alto", "from San Jose to
Palo Alto"; "$900" and "1,200 dollars". The LLM gets the same parsed details listed in its prompt,
and the no-LLM fallback (`fallback_plans`) writes them into the sentences above. A move with no
date says "date to be confirmed"; nothing is ever invented.

### Two internet variants

The negotiate track has two shapes and the agent is told which one it is on:

- New service (the move): no bill exists yet. The agent asks for the current new-customer
  promotion, the student rate on top, the installation fee waived, a no-contract term, and
  confirms the install date against the move-in date.
- Retention (an existing bill, "lower my internet bill"): the agent goes over the account and runs
  the negotiation ladder below for a lower monthly rate with no contract extension.

The request decides: a move with no bill words ("lower", "cancel", "bill", "renewal") is new
service; anything else on this track is retention.

## The negotiation script

`router/negotiation.py` is the spoken version of `negotiation_flow.md`. The ladder is the same on
every call (open, probes, ask for the floor and go silent, anchor only on a number you were given,
bill walk-through, one escalation, read-back with taxes and fees, email confirmation, warm close).
What changes per call is the opening, chosen by `choose_strategy(objective, prior_context, vendor)`:

| Key | Opening | Fires when |
|---|---|---|
| new_service | new-customer promo at the new address | objective is to set up service |
| S3_cancel | "we're deciding whether to keep this" | objective authorizes cancellation |
| S1_anchor | anchor on the price in context | a dollar figure is in the context |
| S4_audit | bill line by line first | vendor refused last time, or is flagged rigid |
| S2_loyalty | long-term customer ask | everything else (lowest hang-up risk) |

The choice is deterministic and comes with a one-line reason, which the instruction carries
("Why: ..."), so the call record can show why the agent opened the way it did.

Thresholds from `negotiation_flow.md` §0, spoken: 25 percent or more off, take it and close; 15 to
25, one counter then close; under 15, run the whole list. The student-rate probe runs before any
close, no matter how good the offer is.

Hard rules stay: cite only numbers you were given, never invent an outage or a competitor quote,
accept discounts and credits but never add services or extend a contract, one sentence of AI
disclosure on first contact.

## The self-improvement loop

Every call writes rows to Postgres (`runs`, `calls`, `events`); Keewano is the analysis sink. The
analysis layer (`analysis/`) folds those rows into per-vendor scorecards, failure modes, time-of-day
effects and a "does prior context pay" comparison. The router consumes that in one place:

- `router.plan.prior_context(store, user_id, vendor_key, track)` asks
  `analysis.context.prior_context_from_insights(compute_insights(...), vendor_key, track)` first,
  and falls back to the last substantive outcome with that vendor ("Last time (Oct 3) BearHug
  Moving quoted $1,240 flat."). `prior_context_detail(...)` returns the sentence with its source
  (`insights`, `last_outcome` or `none`).
- Insights are computed once per user and cached for `INSIGHTS_CACHE_SECONDS` (default 120).
  `plan()` warms the cache so the five `prior_context` calls that follow it share one pass.
- `router.plan.plan_context(store, user_id)` returns `{source, insights, context, vendors_with_insight,
  n_recommendations, generated_at}` so the dispatch route can log a `context_applied` event naming
  the vendors whose call opened with insight-driven context.

The agent then says the context out loud right after the disclosure, in one sentence. That sentence
is the loop made visible: a second call to the same vendor opens with what the first one learned.

What the loop does not do: rewrite prompts or vendor templates on its own. Keewano and the
analysis layer surface patterns and supply context at dispatch. Say it that way.

## Honesty constraints that shape the scripts

- The agent asserts only the cohort on the profile (`profiles.cohort`, default `student`), never an
  inferred one. The asks are config, not prompt prose.
- The router emits vendor keys, never phone numbers; only roster rows are dialable.
- Prices and dates in a script come from the request, the profile or a prior call row. None are
  made up, and a move without a date says so.
- Simulated and seeded rows stay labeled; the speed multiplier is "est." with the overhead
  assumption shown.

## What to point at in a demo

1. Type the sentence at the top. The objectives on the planned rows carry "1-bedroom", "Palo
   Alto" and "October 25" before any call is placed.
2. On the internet row, the instruction names the approach ("new service at a new address") and
   the reason. On a retention row with a prior quote in context, it names "advertised anchor" and
   lists the only number the agent may cite.
3. On any vendor called before, the row's `prior_context` is the sentence the agent opens with,
   and the `context_applied` event lists which vendors got insight-driven context.
4. The per-track "Ask for:" line on each objective is the student lever, readable in the data.

## Functions the UI and wiring phases consume

| Function | Returns |
|---|---|
| `router.plan.plan(store, request, user_id)` | CallPlan dicts, never raises |
| `router.plan.parse_request_details(request)` | date, bedrooms, origin, destination, budget |
| `router.plan.prior_context(store, user_id, vendor_key, track)` | sentence or None |
| `router.plan.prior_context_detail(...)` | `{text, source}` |
| `router.plan.plan_context(store, user_id)` | insights and `vendors_with_insight` |
| `router.plan.clear_context_cache()` | forgets cached insights |
| `router.cohorts.cohort_asks(cohort, track)` | the asks for one track |
| `router.negotiation.choose_strategy(objective, prior_context, vendor)` | `(key, reason)` |
| `router.instructions.strategy_for(plan, vendor, prior_context)` | `{key, label, reason}` |
| `router.instructions.build(plan, vendor, profile, prior_context)` | the full instruction |
