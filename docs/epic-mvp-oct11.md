# Epic: Dormammu MVP — router → parallel calls → aggregator → dashboard

Tracking issue: [#4](https://github.com/vedsmehta/dormammu/issues/4)
Child issues: [#5](https://github.com/vedsmehta/dormammu/issues/5) router, [#6](https://github.com/vedsmehta/dormammu/issues/6) test-lines, [#7](https://github.com/vedsmehta/dormammu/issues/7) aggregator+ledger, [#8](https://github.com/vedsmehta/dormammu/issues/8) dashboard, [#9](https://github.com/vedsmehta/dormammu/issues/9) Keewano

## Context

Dormammu's thesis: one request fans out into N simultaneous phone calls, results come back aggregated. The calling primitive is already built and merged (PR #3): `call/agent.py` (LiveKit voice agent, SIP outbound, DTMF/IVR nav, STT/LLM/TTS, `record_outcome` tool), `call/dispatch.py` (`run_calls()` — concurrent fan-out with semaphore limit, file-based result polling), `sms/messenger.py` (Dial SMS send/wait-for-reply), `call/models.py` (`CallRequest`/`CallResult`). Tests exist for all of it (`test/test_call.py`, `test/test_sms.py`).

What's missing, in dependency order: **router** (turns free text into `CallRequest[]`), **test-lines** (our own controlled numbers to call so the demo is rehearsable and safe), **aggregator + ledger** (normalizes `CallResult[]` into one comparable outcome, persists promo/renewal dates), **dashboard** (live concurrent-call view + ledger timeline), and **Keewano** (event logging + per-call contextual retrieval for the "continuously improves" claim). The PRD also commits to a cohort/eligibility-profile layer and a two-act demo (move-in fan-out, then "11 months later" retention call) — confirmed in scope today.

**Decision made this session:** drop the LiveKit voice path, rebuild outbound voice calling on Dial's own API directly. Confirmed real: `dial-sdk` on PyPI (v0.40.2) exposes `dial.make_call(MakeCallParams(to=..., from_number_id=..., outbound_instruction=...))`, same client/auth pattern already used for SMS in `sms/messenger.py`. **Unverified:** DTMF/IVR navigation support and call-lifecycle/webhook details — no documentation found either way, and the package has shipped multiple point releases in recent weeks. Team has explicitly chosen to commit fully to Dial with no LiveKit fallback kept warm — see Risk #1.

## Current State (verified by reading `origin/main`, Oct 11 2026)

| Piece | State | File(s) |
|---|---|---|
| Call dataclasses | Done | `call/models.py` |
| Voice call placement | Done, but on LiveKit (being replaced) | `call/agent.py`, `call/dispatch.py` |
| SMS send/receive | Done, on Dial | `sms/messenger.py` |
| Router | Not started | — |
| Test-lines (fixtures/IVR) | Not started | — |
| Aggregator | Not started | — |
| Ledger | Not started | — |
| Dashboard | Not started | — |
| Keewano logging | Not started | — |
| Eligibility profiles | Not started | — |
| Dial voice via `make_call` | Confirmed to exist; DTMF/IVR support unverified | — |

## Proposed architecture

```
user text ──> router ──> N × CallRequest ──> dispatch (Dial voice/SMS) ──> N × CallResult
                                                                                 │
                                                                                 v
                                                                           aggregator
                                                                          ┌──────┴──────┐
                                                                     ledger (disk)   dashboard (poll)
                                                                          │
                                                                     Keewano (event log + context, async, non-blocking)
```

Contract every track builds against (frozen, already exists): `CallRequest{to, task, fields, caller_name}` / `CallResult{room, to, status, success, summary, details, transcript, duration_seconds, error}` in `call/models.py`. Nobody touches this schema today — if someone genuinely needs a field, it's a 2-line addition proposed to the group, not a silent fork.

## Risk #1 (P0, blocks everything) — Dial voice migration

`dial.make_call()` is a real, documented method (confirmed via PyPI + docs.getdial.ai), and the SMS code already in the repo proves the client/auth pattern works. The gap is DTMF/IVR navigation — undocumented either way — and the demo's vendor calls are expected to navigate phone menus. **Team decision: full commit to Dial, no LiveKit fallback kept.** Whoever owns the calling track must, in the first 30-45 minutes:
1. Replace `call/agent.py`'s SIP/LiveKit call placement with `dial.make_call()`, keeping the same `CallRequest` → instruction-string construction and `record_outcome`-equivalent outcome capture.
2. Test DTMF/menu navigation against a real test-line IVR (see #6) as the first real call placed.
3. If DTMF doesn't work cleanly, this is now a blocking incident for the whole team, not a silent degrade — raise it immediately so the group can decide (e.g. scope the demo script to avoid IVR menus entirely) rather than discovering it during final integration.

## Keewano scope note (extends PRD §6)

PRD §6 as written describes a human-mediated loop — Keewano surfaces patterns, a person updates prompts. The team's actual intent (confirmed this session) is tighter: Keewano also feeds *contextual retrieval* back into the calling agent itself — at call time, pull relevant prior-call context (this vendor's known IVR path, this user's past outcomes) into that call's instructions, so the agent gets more contextually aware across calls without a human in the loop for that part. The "does not auto-rewrite prompts" honesty line still holds for the *script-authoring* loop (nobody's auto-editing `router/vendor_categories.json` templates) — per-call contextual retrieval into the agent's working instructions is in scope and is not prompt auto-rewriting. State both halves plainly in the demo pitch. Full detail in [#9](https://github.com/vedsmehta/dormammu/issues/9).

## Out of scope today

- Bill ingestion (email/bank) for the monthly "bill higher than ledger rate" trigger — date-based triggers only.
- Second eligibility cohort (senior/military) — profile is a config object, built so it's a one-line swap, but only `student` ships today.
- Google Calendar scheduling track — no owner assigned this round; cut unless a 5th person or slack appears.
- LiveKit voice path — superseded by Dial per the decision above; not maintained going forward.

## Child Issues

| # | Title | Priority | Effort | Dependencies |
|---|---|---|---|---|
| [#5](https://github.com/vedsmehta/dormammu/issues/5) | Router: free text → N parallel CallRequests | Critical | ~1.5h | Needs Risk #1's Dial migration landed for real end-to-end test, can build/test against mocked CallResult in parallel |
| [#6](https://github.com/vedsmehta/dormammu/issues/6) | Test-lines: controlled vendor numbers + fixtures | Critical | ~1h | None — can start immediately, feeds Risk #1's DTMF test and #5/#7 |
| [#7](https://github.com/vedsmehta/dormammu/issues/7) | Aggregator + ledger | High | ~2h | Needs #5's CallRequest shape (already frozen) — can build/test against mocked CallResult in parallel |
| [#8](https://github.com/vedsmehta/dormammu/issues/8) | Dashboard (polls `.calls/` + ledger) | High | ~2h | Needs #7's ledger file shape defined; can scaffold against `call/models.py`'s existing `.calls/` dir immediately |
| [#9](https://github.com/vedsmehta/dormammu/issues/9) | Keewano event logging + context retrieval | Medium | ~1.5h + unconfirmed sandbox setup | Needs Risk #1's Dial migration landed (hooks into the same call lifecycle) |

## Dependency Graph

```
Risk #1 (Dial voice migration, P0) ──┬──> #9 Keewano logging
                                 │
#6 Test-lines ───────────────────┼──> (real end-to-end test for Risk #1, #5, #7)
                                 │
#5 Router ───────────────────────┼──> #7 Aggregator + ledger ──> #8 Dashboard
                                 │                (all three buildable against
                                 └─── mocked CallResult in parallel with Risk #1)
```

## Sequencing Rationale

Risk #1 and #6 are the only hard blockers for a *real* end-to-end demo call — everything else can be built and unit-tested against mocked `CallRequest`/`CallResult` data in parallel, then integration-tested the moment Risk #1 lands. This is why the team can run 4 tracks concurrently starting now rather than waiting in a line.

## Definition of Done

1. One real call placed via `dial.make_call()` against a test-lines number, successfully navigating at least one DTMF menu, producing a `CallResult` with `status: completed`.
2. Router takes the demo prompt and returns ≥3 `CallRequest`s with student-rate asks injected.
3. Aggregator produces one ranked comparison table from a real 3-call fan-out; one ledger entry written.
4. Dashboard shows live status for the 3 concurrent calls and renders the ledger's one wired promo-expiry entry.
5. At least one completed call's event sequence is visible in KeewanoDB, and a second call to the same number shows picked-up context from the first (or this line is explicitly cut from the pitch if sandbox setup doesn't land in time).
6. Demo video recorded showing the two-act structure: move-in fan-out, then the "11 months later" retention beat.

## Rollback Plan

Each track is additive on top of the frozen `CallRequest`/`CallResult` contract — if any one track isn't ready by 3pm, cut it from the demo video script rather than blocking others (e.g. no dashboard → narrate call statuses from `dispatch.py` CLI output instead; no Keewano → drop that pitch line, don't fake it). If Risk #1's Dial migration is not working by the time-box, this is a team-wide blocking call, not a quiet fallback — raise it immediately.
