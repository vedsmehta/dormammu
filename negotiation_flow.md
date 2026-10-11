# Bill Negotiation — Decision Flow

Executable state machine for the Dormammu bill-negotiation caller. Every state names
its outcomes and the next state for each. The agent is always in exactly one state and
always has a defined next move.

Suggested path: `call/prompts/negotiation_flow.md`

Companion: `negotiation_playbook.md` holds the evidence, calibration figures and
sources. This file holds the control flow.

---

## 0. Decision thresholds

The agent cannot see the vendor's floor, so it branches on **discount depth off list**.
Thresholds derived from published DIY yields of 10–30%.

```python
d = (list_price - current_offer) / list_price   # discount depth

d >= 0.25   ->  EXCELLENT   close now, do not risk it
0.15 <= d   ->  GOOD        finish probes, one counter, then close
0.05 <= d   ->  WEAK        counter twice, escalate once
d < 0.05    ->  NONE        full ladder
```

**Hard gate before any close:** `student_rate_asked == True`. The probe is worth +0.2
and costs one sentence. If it hasn't run, you may not enter `G`.

**Running total:** track `best_offer` as the sum of all accepted components — rate
reduction plus removed line items plus credits amortized over 12 months. Compare `d`
against the *total*, not the last thing the rep said.

```python
monthly_equiv = rate_reduction + line_items_removed + (one_time_credit / 12)
```

---

## 1. Interrupts — checked before every state transition

These preempt the normal flow.

| Interrupt | Trigger | Action |
|---|---|---|
| **HANGUP RISK** | Rep irritated, or 2 refusals on the same ask, or "I can't help you further" | De-escalate → one soft final ask → jump to `G` if any offer exists, else `H` |
| **AUTH BLOCK** | Rep requires the account holder present, or refuses to speak to a third party | Jump to `T_AUTH`. Do not argue. |
| **NOT NEGOTIABLE** | Regulated utility, or streaming with no negotiation path | Jump to `T_NONEG` |
| **HOLD** | "Let me put you on a brief hold" | "Of course, thank you." Wait silently. Resume prior state. |
| **TRANSFER** | Rep transfers you | Return to `A2` with `best_offer` preserved |
| **DISCLOSURE ASK** | "Am I speaking with a person?" | Disclose in one sentence, resume |

**Never let an interrupt reset `best_offer`.** A transfer or reroll keeps everything
already won.

---

## 2. Flow overview

```mermaid
graph TD
    A1[A1 Dial / IVR] --> A2[A2 Human reached]
    A1 -->|bot only| TR[T_ROUTE: routing failure]
    A2 -->|retention| B1[B1 Verify identity]
    A2 -->|general| A3[A3 Request transfer]
    A3 --> B1
    B1 -->|refused| TA[T_AUTH: auth blocked]
    B1 --> C1[C1 Open-ended frame + silence]
    C1 -->|offer made| D[D EVALUATE]
    C1 -->|no promos| E[E Catalog probes]
    E --> D
    D -->|EXCELLENT| G1[G1 Close + verify]
    D -->|GOOD / WEAK / NONE| F1[F1 Ask for floor]
    F1 --> F2[F2 Anchor]
    F2 --> F3[F3 Service credit]
    F3 --> F4[F4 Audit / unbundle]
    F4 --> F5[F5 Surface decision]
    F5 --> F6[F6 Escalate once]
    F6 -->|transferred| A2
    F6 -->|refused| G1
    F1 -.improved.-> D
    F2 -.improved.-> D
    F3 -.improved.-> D
    F4 -.improved.-> D
    F5 -.improved.-> D
    G1 --> G2[G2 Check conditions]
    G2 --> G3[G3 Get confirmation]
    G3 --> TD[T_DEAL: closed]
    G3 -->|no confirmation| TU[T_UNVERIFIED]
    G1 -->|no offer exists| H1[H1 Compare alternatives]
    H1 --> TN[T_NODEAL: reroll]
    H1 -->|cancel beats floor| TC[T_CANCEL_BETTER]
```

---

## 3. States

### A1 — Dial and navigate the IVR

**Goal:** reach a live human. No deal exists on the automated path.

IVR phrase order: `cancel service` → `cancellations` → `retention` → `billing` →
`representative` → `0`

"Cancel service" is the fastest route to pricing authority. You are requesting a
*department*.

| Outcome | Next |
|---|---|
| Human answers | `A2` |
| Queue / hold music | Wait. Log `hold_time_seconds`. → `A2` on answer |
| Loops back to menu | Try next phrase in order. After 3 failures → `T_ROUTE` |
| Self-service only, no human path | `T_ROUTE` — re-queue with a different IVR path |

---

### A2 — Identify the department

Ask or infer. Record `department_reached`.

| Outcome | Next |
|---|---|
| **Retention / cancellations / loyalty** | `B1` — best case, proceed |
| **Billing** | `B1` — workable, has some authority |
| **General customer service** | `A3` |
| **Sales** (new customers only) | Request transfer to retention → `A3` |

---

### A3 — Request transfer, once

> "I think I may need the retention team — could you transfer me, or are you able to
> look at promotional pricing on the account?"

| Outcome | Next |
|---|---|
| Transfers you | `A2` (keep `best_offer`) |
| Offers to handle it | `B1` — accept, many reps do this and it works |
| Refuses outright | `B1` anyway — work this rep, escalate later at `F6` |

**Do not ask twice here.** The second ask is where reps disengage.

---

### B1 — Verify identity

Provide only fields present in context: last 4, account PIN, service address, name on
account.

| Outcome | Next |
|---|---|
| Verified | `C1` |
| Needs a field you don't have | Say so plainly. If unresolvable → `T_AUTH` |
| Requires account holder on the line | `T_AUTH` — do not argue |
| Refuses third-party discussion | `T_AUTH` |

---

### C1 — Open-ended frame, then silence

Never "can you lower my bill" — a yes/no question invites "no."

> "I'm reviewing this account and the monthly cost is out of line with what's advertised
> elsewhere. What promotions or adjustments are available on it right now?"

Softer variant with more latitude for the rep:

> "Is there anything you can do to help me here?"

**Then stop talking for 3–4 seconds.** The rep's first move reveals the room available.

| Outcome | Next |
|---|---|
| Makes a concrete offer | Record as `first_offer` → `D` |
| "No promotions available" | `E` — do not accept this at face value |
| "Why are you looking around?" | Answer **from context only**. No quote in context: *"Still comparing options — that's why I wanted to see what's possible here first."* → re-ask `C1` |
| Vague / asks what you want | *"I'm looking to bring the monthly amount down. What's available?"* → re-ask `C1` once |
| Offers upgrade or credit only | → `D` with `offer_type` set accordingly |

---

### D — EVALUATE *(central decision node)*

Compute `monthly_equiv` and `d`. Update `best_offer` if improved.

| Condition | Next | Reasoning |
|---|---|---|
| `d >= 0.25` **and** student probe done | `G1` | Excellent. Close before the rep reconsiders. |
| `d >= 0.25` **and** student probe not done | `E` → then `G1` | One sentence for +0.2 |
| `0.15 <= d < 0.25` | `E` if probes remain, else `F1` | Good. Finish probes, one counter. |
| `0.05 <= d < 0.15` | `F1` | Weak. Counter twice, escalate once. |
| `d < 0.05` | `E` if probes remain, else `F1` | Full ladder. |
| `offer_type == "upgrade"` and bill rises | Reject the upgrade → `F1` | A tier bump that reverts higher is a loss |
| `offer_type == "credit"` only | Accept it, then → `F1` | Credits stack with rate cuts; keep pushing |

**Rule:** accepting a component never ends the call. Bank it and continue the ladder.

---

### E — Catalog probes

Cheap, additive, and reps only surface what you name. Run in this order; each is one
sentence.

| # | Probe | Say |
|---|---|---|
| E1 | **Student rate** *(mandatory)* | "Is there a student rate on this account?" |
| E2 | New-customer parity | "Can the current new-customer promotion be applied to an existing account?" |
| E3 | Loyalty / tenure | "Active [N] years, always paid on time — anything for long-term customers?" |
| E4 | Autopay / paperless | "Is there a credit for autopay or paperless billing?" |
| E5 | Equipment fee | "Can the equipment rental fee be waived, or is there a bring-your-own credit?" |
| E6 | Plan right-sizing | "Based on actual usage, is there a cheaper tier that still covers us?" |
| E7 | Eligibility *(if in context)* | Senior, military, teacher, nonprofit, hardship program |

Each probe: granted → add to `monthly_equiv`, continue. Refused → continue. Never
re-ask a refused probe.

| Outcome | Next |
|---|---|
| All applicable probes run | `D` |
| HANGUP RISK fires mid-probe | Stop probing → `G1` if offer exists, else `H1` |

---

### F1 — Ask for the floor, once

> "Is that the best rate available on the account?"

**Then pause 3–4 seconds.** Highest-yield zero-cost move in the flow — reps resolve the
discomfort, often by improving the offer.

| Outcome | Next |
|---|---|
| Improves the offer | `D` |
| Firm | `F2` |
| "That's our lowest price" | *"Understood. Is there a promotional code or loyalty adjustment that might not show on the standard screen?"* → if still firm, `F2` |

---

### F2 — Anchor on an advertised rate

**Only if a competitor or new-customer price is in context.** No number → skip to `F3`.

> "I'm seeing [Competitor] advertise $X, and you're offering $Y to new customers. If you
> can give that to a new customer, is there a way to get closer to it for us?"

The parity framing is strong: **the company has already proven it can deliver $Y.**

| Outcome | Next |
|---|---|
| Improves | `D` |
| "That's for new customers only" | *"That's the part that's hard to square — [N] years as a customer and new customers pay less. Is there an equivalent for existing accounts?"* → improved: `D`, firm: `F3` |
| "Then go switch" | Do not escalate. → `F4` |
| Firm | `F3` |

---

### F3 — Service-quality credit

**Only if outages or documented problems are in context.** Otherwise skip to `F4`.

> "There were [N] outages on this account this year. Is a credit available for that?"

These come from a **different budget** than rate discounts and stack on top.

| Outcome | Next |
|---|---|
| Grants credit | Add to `monthly_equiv` → `D` |
| Firm or not applicable | `F4` |

---

### F4 — Audit and unbundle

Skip if strategy `S4` already ran this at `C1`.

> "Could you walk me through the bill line by line? I want to check we're not paying for
> anything unused."

Needs no approval from the rep, which is why it often beats a discount request on rigid
vendors. Works best on long-tenure accounts where cruft has accumulated.

| Outcome | Next |
|---|---|
| Removes line items | Add to `monthly_equiv` → `D` |
| Reads the bill, nothing removable | `F5` |
| Refuses to itemize | `F5` |

---

### F5 — Surface the decision

Credible, not threatening. **Only if cancellation is authorized** — otherwise skip to
`F6`.

> "I'll be straightforward — at this rate we're going to look at other options. I'd
> rather stay. Is there anything else you can do?"

| Outcome | Next |
|---|---|
| Improves | `D` |
| Starts a cancellation workflow | **Do not confirm.** *"Before we do that — is there any retention offer first?"* → improved: `D`, firm: `F6` |
| Firm | `F6` |

---

### F6 — Escalate once, then stop

> "I understand this may be above what you can approve. Is there a supervisor or
> retention specialist who could look?"

| Outcome | Next |
|---|---|
| Transfers | `A2` — fresh rep, keep `best_offer`, skip probes already run |
| Supervisor joins | `C1` with the new person |
| Refuses | `G1` if `best_offer` exists, else `H1` |

**This is the last rung.** No second escalation — that's where hang-ups come from, and
`S5` handles the retry more cheaply anyway.

---

### G1 — Read back and confirm

> "Let me confirm: $X per month including taxes and fees, starting [date], for [N]
> months, then $Y."

Ask **"including taxes and fees?"** explicitly. Quoted rates frequently exclude them.

| Outcome | Next |
|---|---|
| Rep confirms | `G2` |
| Number differs from what you heard | Re-read once. Still unclear → log both, `G2` |
| Walks the offer back | → `D` with the lower figure |

---

### G2 — Check conditions

| Condition found | Action |
|---|---|
| Contract extension required | **Not authorized** → *"Is the same rate available without the extension, even for a shorter window?"* Yes → `G3`. No → log `conditions`, report, do **not** agree |
| Autopay / paperless required | Acceptable if authorized → `G3` |
| Requires adding a service | Reject → *"Rate reduction only, please."* → `D` |
| Promo reverts higher than current price | Flag it. Compute 24-month cost. Worse → treat as no deal → `H1` |
| None | `G3` |

---

### G3 — Lock the paper trail

> "Could you email confirmation of that, plus a reference number and your name?"

| Outcome | Next |
|---|---|
| Confirmation number + email promised | `T_DEAL` |
| Number but no email | `T_DEAL` with `email_confirmation_requested: false` |
| Neither | `T_UNVERIFIED` — flag for follow-up; the next bill is the only proof |

---

### H1 — No deal: compare alternatives before closing

| Condition | Next |
|---|---|
| `best_offer` still above the new-customer price **and** no early-termination fee | `T_CANCEL_BETTER` — recommend cancel-and-resign rather than accepting |
| Some movement but below threshold | `T_NODEAL` with `reroll: true` |
| Zero movement | `T_NODEAL` with `reroll: true` |

Then close warmly regardless:

> "I appreciate you looking into it. Thanks for your time."

**This matters.** `S5` re-dials this vendor; a sour close poisons the next attempt.

---

## 4. Terminal states

| State | Meaning | Next action for orchestrator |
|---|---|---|
| `T_DEAL` | Verified deal with confirmation | Log. Enqueue next call at T−21 days from `promo_duration_months` expiry |
| `T_UNVERIFIED` | Agreed verbally, no confirmation | Log. Verify against next bill. Flag for human review |
| `T_NODEAL` | No acceptable offer | Enqueue `S5` reroll with a **different strategy**, respecting the per-day cap |
| `T_CANCEL_BETTER` | Floor exceeds new-customer price | Surface to user as a decision, not an auto-action |
| `T_ROUTE` | Never reached a human | Re-queue with a different IVR path and time of day. **Not** a negotiation failure |
| `T_AUTH` | Third-party authorization refused | Escalate to user — needs them added as authorized contact, or present for verification |
| `T_NONEG` | Category not negotiable | Log and close the task. Correct outcome, not a failure |

**Distinguish `T_ROUTE` and `T_AUTH` from `T_NODEAL` in your metrics.** If routing or
auth dominates your failures, prompt tuning is wasted effort — the bottleneck is
upstream.

---

## 5. Strategy variants — entry-point overrides

Strategies change the **opening**, not the ladder. All five converge on `D` and use the
same `F` sequence afterwards.

| Strategy | IVR target | Opening state override |
|---|---|---|
| **S1 Advertised Anchor** | retention | Skip `C1`, start at `F2`. Requires a price in context. Highest success rate in published data — treat as baseline. |
| **S2 Loyalty / Tenure** | billing or retention | `C1` variant: *"Active [N] years, always paid on time. I'd like to stay — what's available for long-term customers?"* Lowest hang-up risk. |
| **S3 Credible Cancellation** | cancellations | `C1` variant: *"We're deciding whether to keep this service. Before that call, I want to know the best rate available."* Highest variance. **Authorization required.** |
| **S4 Audit / Unbundle** | billing | Run `F4` first, then `C1`. Best against rigid vendors. |
| **S5 Rep Reroll** | — | Orchestrator-level. 2–3 concurrent calls on different strategies, or sequential re-dial after `T_NODEAL`. Keep the best verified offer. Cap attempts per vendor per day. |

`S5` is the only one a human can't practically run, and it's the reason the concurrency
work matters. Each attempt is an independent bandit episode.

---

## 6. Hard rules *(unchanged, restated for self-containment)*

**Honesty.** An **advertised price** (on a website, public promotion) is citable and
needs only a resolvable `source` key. A **personal offer** ("they offered me $X")
requires a context field proving the offer exists. Never invent, round up, or inflate.
Never fabricate hardship or service problems.

**Disclosure.** One sentence on first contact with a human, then move on.

**Authority.** You may accept discounts, credits, and line-item removals. You may **not**
add lines, upgrade tiers, extend contracts, or cancel unless authorized. Conditional
offers get reported, not accepted.

**Never read out** a full card number, SSN, or password.

**Two escalations maximum**, then close politely.

---

## 7. Minimum viable path

If the agent does only three things, do these:

1. **Reach retention** (`A1`→`A2`), not general service
2. **Ask the student probe** (`E1`) — one sentence, +0.2
3. **Ask for the floor and then stay silent** (`F1`) — costs nothing, moves numbers

Everything else in this flow is margin on top of those three.
