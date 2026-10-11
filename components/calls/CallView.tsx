"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { summarizeOutcome } from "@/components/calls/summary";
import { AppShell, PageTitle } from "@/components/shell";
import { Dot, ErrorNote, Loading, stateWord, Tag, Wave } from "@/components/ui";
import { getLedger, getRecentRuns, getRun, tickRun } from "@/lib/api";
import { describeError, EM, fmtDay, fmtDuration, fmtSlot, fmtStampFull, fmtUsd, maskPhone, parseTs, tidy } from "@/lib/format";
import { isTerminal, trackLabel, type AssembledRun, type Call, type LedgerRow } from "@/lib/types";

const TICK_MS = 2000;
const SCAN_RUNS = 20;

const d = (s: string) => ({ "--d": s }) as CSSProperties;

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
      <p>
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
    <dl className="facts">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
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

  if (error || !run || !call) {
    const message = error ?? (run && !call ? `No call ${callId} in run ${run.id}.` : null);
    return (
      <AppShell head={<PageTitle back={{ href: run ? `/runs/${run.id}` : "/calls", label: run ? "Open the run" : "Back to calls" }} title={message ? "Call" : "Loading call…"} />}>
        {message ? <ErrorNote message={message} /> : <Loading>Loading call…</Loading>}
      </AppShell>
    );
  }

  const s = summarizeOutcome(call);
  const elapsed = elapsedFor(call, run);
  const turns = Array.isArray(call.transcript_turns) ? call.transcript_turns : null;
  const mine = (ledger ?? []).filter((r) => r.source_call === call.id);
  const tone = s.tone === "ok" ? "t-talking" : s.tone === "warn" || s.tone === "bad" ? "t-ringing" : s.tone === "muted" ? "t-muted" : "";

  const head = (
    <PageTitle back={{ href: `/runs/${run.id}`, label: tidy(run.request).length > 60 ? tidy(run.request).slice(0, 57) + "…" : tidy(run.request) }} title={call.target_name}>
      <p className={`outcome num rise ${tone}`.trim()} style={d(".2s")}>
        {s.main}
        {call.state === "talking" ? <Wave /> : null}
        {s.sub ? <small>{s.sub}</small> : null}
      </p>
      <p className="meta rise" style={d(".3s")}>
        <Dot state={call.state} />
        <span>{stateWord(call.state)}</span>
        <span>·</span>
        <span>{trackLabel(call.track)}</span>
        <span>·</span>
        <span>{fmtStampFull(call.started_at)}</span>
        <span>·</span>
        <span className="num">{fmtDuration(elapsed)}</span>
        <span>·</span>
        <span className="num">{maskPhone(call.target_phone)}</span>
        {call.simulated ? (
          <Tag tone="warn" first title="Simulated: this row ran on the scripted timeline, not a live Dial call">
            Simulated
          </Tag>
        ) : null}
        {call.termination_type ? (
          <Tag tone="line" first>
            {call.termination_type}
          </Tag>
        ) : null}
      </p>
    </PageTitle>
  );

  return (
    <AppShell head={head}>
      <div className="call-grid">
        <section aria-labelledby="said-h">
          <h2 className="eyebrow" id="said-h">
            What was said
          </h2>
          {turns && turns.length ? (
            <ol className="transcript no-time">
              {turns.map((t, i) => {
                const agent = /agent|assistant|ai/i.test(t.speaker);
                return (
                  <li key={i} className={agent ? "agent" : "rep"}>
                    <span className="sp">{t.speaker}</span>
                    <p>{t.text}</p>
                  </li>
                );
              })}
            </ol>
          ) : call.transcript ? (
            <div className="transcript">
              <pre>{call.transcript}</pre>
            </div>
          ) : (
            <p className="dash-note">{active ? "The transcript arrives when the call ends." : "No transcript was recorded for this call."}</p>
          )}
          {active ? (
            <p className="dash-note">
              Live: refreshing every {TICK_MS / 1000}s.{tickError ? ` tick: ${tickError}` : ""}
            </p>
          ) : null}
        </section>

        <aside className="side">
          <section className="sec" aria-labelledby="back-h">
            <h2 id="back-h">What came back</h2>
            <OutcomeFacts call={call} />
          </section>

          <section className="sec" aria-labelledby="told-h">
            <h2 id="told-h">What the agent was told</h2>
            <p className="said">{call.objective || EM}</p>
            {call.prior_context ? (
              <p>
                <span className="t-muted">Prior context · </span>
                {call.prior_context}
              </p>
            ) : null}
            {call.vendor_key ? <p className="dash-note">vendor key {call.vendor_key}</p> : null}
          </section>

          <section className="sec" aria-labelledby="ledger-h">
            <h2 id="ledger-h">In your ledger</h2>
            {ledger === null ? (
              <Loading />
            ) : mine.length === 0 ? (
              <p>
                Nothing from this call yet. Dated facts land in the{" "}
                <Link className="edit" href="/ledger">
                  ledger
                </Link>{" "}
                as calls finish.
              </p>
            ) : (
              <dl className="facts">
                {mine.map((r) => (
                  <div key={r.id}>
                    <dt>{r.kind.replace("_", " ")}</dt>
                    <dd>
                      <span>
                        {tidy(r.label)}
                        <small className="verified" style={{ display: "block", marginTop: 4 }}>
                          {r.amount_usd !== null ? `${fmtUsd(r.amount_usd)} · ` : ""}
                          {r.expires_on ? `until ${fmtDay(r.expires_on)}` : r.effective_on ? `from ${fmtDay(r.effective_on)}` : "undated"}
                        </small>
                      </span>
                    </dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </aside>
      </div>
    </AppShell>
  );
}
