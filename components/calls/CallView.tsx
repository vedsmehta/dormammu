"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { summarizeOutcome, TONE_CLASS } from "@/components/calls/summary";
import { Chip, Dot, ErrorNote, Loading, PageHeader, stateWord, Wave } from "@/components/ui";
import { getLedger, getRecentRuns, getRun, tickRun } from "@/lib/api";
import { describeError, EM, fmtDay, fmtDuration, fmtSlot, fmtStampFull, fmtUsd, maskPhone, parseTs } from "@/lib/format";
import { isTerminal, trackLabel, type AssembledRun, type Call, type LedgerRow } from "@/lib/types";

const TICK_MS = 2000;
const SCAN_RUNS = 20;

function elapsedFor(call: Call, run: AssembledRun): number | null {
  if (call.state === "planned") return 0;
  const start = parseTs(call.started_at);
  if (start === null) return call.duration_seconds ?? null;
  const end = call.ended_at ? parseTs(call.ended_at) : null;
  if (end !== null) return Math.max(0, (end - start) / 1000);
  if (isTerminal(call.state)) return call.duration_seconds ?? null;
  const now = parseTs(run.server_now);
  return now === null ? null : Math.max(0, (now - start) / 1000);
}

/** The "What came back" facts, one row per outcome field that is set. */
function OutcomeFacts({ call }: { call: Call }) {
  const o = call.outcome;
  if (!o) {
    return (
      <p className="mt-3 text-sm text-muted">
        {call.state === "failed"
          ? `Nothing. ${call.termination_type ?? call.error ?? "The call did not produce an outcome."}`
          : isTerminal(call.state)
            ? "No outcome was extracted."
            : "Nothing yet. The outcome is read from the transcript once the call ends."}
      </p>
    );
  }
  const rows: [string, string][] = [];
  if (o.track === "quote") {
    rows.push(["Quote", o.amount_usd === null ? EM : `${fmtUsd(o.amount_usd)}${o.unit ? ` ${o.unit}` : ""}`]);
    if (o.caveats?.length) rows.push(["Caveats", o.caveats.join("; ")]);
  } else if (o.track === "schedule") {
    rows.push(["Slot", o.slot_iso ? fmtSlot(o.slot_iso) : EM]);
    rows.push(["Confirmed", o.confirmed ? "Yes" : "No"]);
  } else if (o.track === "negotiate") {
    rows.push(["Action", o.action ?? "none"]);
    if (o.saved_usd !== null && o.saved_usd !== undefined) rows.push(["Saved", fmtUsd(o.saved_usd)]);
    if (o.effective_date) rows.push(["Effective", fmtDay(o.effective_date)]);
  }
  if (o.note) rows.push(["Note", o.note]);
  return (
    <dl className="mt-3 divide-y divide-line">
      {rows.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[7rem_minmax(0,1fr)] gap-3 py-2 text-sm">
          <dt className="text-muted">{k}</dt>
          <dd className="break-words font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CallView({ callId, runId }: { callId: string; runId: string | null }) {
  const [run, setRun] = useState<AssembledRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tickError, setTickError] = useState<string | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[] | null>(null);
  const inFlight = useRef(false);

  const apply = useCallback((next: AssembledRun) => setRun(next), []);

  // Load the run this call belongs to. Without ?run= we scan the recent runs for it, since there
  // is no per-call endpoint.
  useEffect(() => {
    let cancelled = false;
    const load = runId
      ? getRun(runId)
      : getRecentRuns(SCAN_RUNS).then((runs) => {
          const hit = runs.find((r) => r.calls.some((c) => c.id === callId));
          if (!hit) throw new Error(`This call is not in the ${SCAN_RUNS} most recent runs. Open it from its run.`);
          return hit;
        });
    load
      .then((r) => {
        if (cancelled) return;
        apply(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [callId, runId, apply]);

  const call = run?.calls.find((c) => c.id === callId) ?? null;
  const active = Boolean(call && !isTerminal(call.state));
  const activeRunId = active && run ? run.id : null;

  // The tick drives the run forward; without it a live call shown here would never advance.
  useEffect(() => {
    if (!activeRunId) return;
    const id = window.setInterval(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        apply(await tickRun(activeRunId));
        setTickError(null);
      } catch (e: unknown) {
        setTickError(describeError(e));
      } finally {
        inFlight.current = false;
      }
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [activeRunId, apply]);

  // Ledger rows this call wrote. Reloaded when the call finishes.
  const callKey = call?.id ?? null;
  useEffect(() => {
    if (!callKey) return;
    let cancelled = false;
    getLedger()
      .then((rows) => {
        if (!cancelled) setLedger(rows);
      })
      .catch(() => {
        if (!cancelled) setLedger([]);
      });
    return () => {
      cancelled = true;
    };
  }, [callKey, active]);

  if (error) {
    return (
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">
        <ErrorNote message={error} />
        <Link href="/calls" className="mt-3 inline-block text-sm text-accent hover:underline">
          back to calls
        </Link>
      </main>
    );
  }
  if (!run) {
    return (
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">
        <Loading>loading call…</Loading>
      </main>
    );
  }
  if (!call) {
    return (
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">
        <ErrorNote message={`No call ${callId} in run ${run.id}.`} />
        <Link href={`/runs/${run.id}`} className="mt-3 inline-block text-sm text-accent hover:underline">
          open the run
        </Link>
      </main>
    );
  }

  const s = summarizeOutcome(call);
  const elapsed = elapsedFor(call, run);
  const turns = Array.isArray(call.transcript_turns) ? call.transcript_turns : null;
  const mine = (ledger ?? []).filter((r) => r.source_call === call.id);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <PageHeader
        back={{ href: `/runs/${run.id}`, label: run.request.length > 60 ? run.request.slice(0, 57) + "…" : run.request }}
        title={call.target_name}
      >
        <p className={`tnum mt-4 flex flex-wrap items-baseline gap-x-4 gap-y-1 font-display text-4xl font-semibold tracking-tight sm:text-6xl ${TONE_CLASS[s.tone]}`}>
          {s.main}
          {call.state === "talking" ? <Wave /> : null}
          {s.sub ? <small className="font-sans text-base font-normal text-muted">{s.sub}</small> : null}
        </p>
        <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
          <Dot state={call.state} />
          <span>{stateWord(call.state)}</span>
          <span className="text-dim">·</span>
          <span>{trackLabel(call.track)}</span>
          <span className="text-dim">·</span>
          <span>{fmtStampFull(call.started_at)}</span>
          <span className="text-dim">·</span>
          <span className="tnum font-mono">{fmtDuration(elapsed)}</span>
          <span className="text-dim">·</span>
          <span className="tnum font-mono">{maskPhone(call.target_phone)}</span>
          {call.simulated ? (
            <Chip tone="warn" title="Simulated: this row ran on the scripted timeline, not a live Dial call">
              simulated
            </Chip>
          ) : null}
          {call.termination_type ? <Chip tone="muted">{call.termination_type}</Chip> : null}
        </p>
      </PageHeader>

      <div className="grid gap-8 lg:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <section aria-labelledby="said-h">
          <h2 id="said-h" className="font-mono text-xs uppercase tracking-widest text-teal">
            What was said
          </h2>
          {turns && turns.length ? (
            <ol className="mt-3 divide-y divide-line">
              {turns.map((t, i) => {
                const agent = /agent|assistant|ai/i.test(t.speaker);
                return (
                  <li key={i} className="grid grid-cols-[4rem_minmax(0,1fr)] gap-3 py-3 text-sm">
                    <span className="pt-0.5 font-mono text-[11px] uppercase tracking-wide text-dim">{t.speaker}</span>
                    <p className={agent ? "text-fg" : "text-muted"}>{t.text}</p>
                  </li>
                );
              })}
            </ol>
          ) : call.transcript ? (
            <pre className="mt-3 whitespace-pre-wrap font-sans text-sm leading-relaxed text-muted">{call.transcript}</pre>
          ) : (
            <p className="mt-3 text-sm text-muted">
              {active ? "The transcript arrives when the call ends." : "No transcript was recorded for this call."}
            </p>
          )}
          {active ? (
            <p className="mt-4 font-mono text-[11px] text-dim">
              Live: refreshing every {TICK_MS / 1000}s.{tickError ? ` tick: ${tickError}` : ""}
            </p>
          ) : null}
        </section>

        <aside className="space-y-8">
          <section aria-labelledby="back-h">
            <h2 id="back-h" className="font-mono text-xs uppercase tracking-widest text-teal">
              What came back
            </h2>
            <OutcomeFacts call={call} />
          </section>

          <section aria-labelledby="told-h">
            <h2 id="told-h" className="font-mono text-xs uppercase tracking-widest text-teal">
              What the agent was told
            </h2>
            <p className="mt-3 text-sm text-fg">{call.objective || EM}</p>
            {call.prior_context ? (
              <p className="mt-2 text-sm text-muted">
                <span className="font-mono text-[11px] uppercase tracking-wide text-dim">prior context · </span>
                {call.prior_context}
              </p>
            ) : null}
            {call.vendor_key ? <p className="mt-2 font-mono text-[11px] text-dim">vendor key {call.vendor_key}</p> : null}
          </section>

          <section aria-labelledby="ledger-h">
            <h2 id="ledger-h" className="font-mono text-xs uppercase tracking-widest text-teal">
              In your ledger
            </h2>
            {ledger === null ? (
              <div className="mt-3">
                <Loading />
              </div>
            ) : mine.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                Nothing from this call yet. Dated facts land in the{" "}
                <Link href="/ledger" className="text-accent hover:underline">
                  ledger
                </Link>{" "}
                as calls finish.
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-line">
                {mine.map((r) => (
                  <li key={r.id} className="py-2 text-sm">
                    <span className="flex items-center gap-2">
                      <Chip tone="muted">{r.kind.replace("_", " ")}</Chip>
                      <b className="font-medium">{r.label}</b>
                    </span>
                    <span className="mt-1 block font-mono text-xs text-muted">
                      {r.amount_usd !== null ? `${fmtUsd(r.amount_usd)} · ` : ""}
                      {r.expires_on ? `until ${fmtDay(r.expires_on)}` : r.effective_on ? `from ${fmtDay(r.effective_on)}` : "undated"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}
