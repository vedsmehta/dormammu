"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell, PageTitle } from "@/components/shell";
import { Dot, ErrorNote, Loading, Panel, PanelTitle, Tag } from "@/components/ui";
import { getAccuracy, getEvalReport, getEvidence, type AccuracyCore, type AccuracyReport, type EvalReport, type EvidenceReport } from "@/lib/api";
import { describeError, EM, fmtDuration, fmtStamp, fmtStampFull } from "@/lib/format";
import { trackLabel } from "@/lib/types";

const RECENT_LIMIT = 30;

const pct = (r: number | null | undefined) => (r === null || r === undefined ? EM : `${Math.round(r * 100)}%`);
const n = (v: number | null | undefined) => (v === null || v === undefined ? EM : String(v));

/** A .facts list from a flat record. */
function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd className="plain">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Counts as a .bar plus .legend, in the dashboard's "How calls ended" shape. */
function Counts({ counts, label }: { counts: Record<string, number>; label: string }) {
  const entries = Object.entries(counts);
  const total = entries.reduce((s, [, v]) => s + v, 0);
  if (entries.length === 0) return <p>Nothing counted yet.</p>;
  const palette = ["var(--deep)", "var(--mint)", "#f2b84b", "#a9ddd3", "var(--ink-3)", "#ffd3d3", "#bfe4dc"];
  return (
    <>
      <div className="bar" role="img" aria-label={`${label}: ${entries.map(([k, v]) => `${v} ${k}`).join(", ")}`}>
        {entries.map(([k, v], i) => (
          <i key={k} style={{ flex: v, background: palette[i % palette.length] }} />
        ))}
      </div>
      <ul className="legend">
        {entries.map(([k, v], i) => (
          <li key={k}>
            <span>
              <i style={{ background: palette[i % palette.length] }} />
              {k.replace(/_/g, " ")}
            </span>
            <b className="num">
              {v}
              {total ? <i className="plain" style={{ font: "400 13px/1 var(--body)", color: "var(--ink-3)", marginLeft: 6 }}>{Math.round((v / total) * 100)}%</i> : null}
            </b>
          </li>
        ))}
      </ul>
    </>
  );
}

function AccuracyBlock({ core, title, note }: { core: AccuracyCore; title: string; note?: string }) {
  const o = core.overall;
  return (
    <Panel>
      <PanelTitle>{title}</PanelTitle>
      {note ? <p>{note}</p> : null}
      <Facts
        rows={[
          ["Calls", `${o.n_calls} (${o.n_done} done, ${o.n_failed} failed, ${o.n_simulated} simulated)`],
          ["Answer rate", pct(o.answer_rate)],
          ["Voicemail rate", pct(o.voicemail_rate)],
          ["No-answer rate", pct(o.no_answer_rate)],
          ["Extraction coverage", pct(o.extraction_coverage)],
          ["Avg call length", o.avg_duration_seconds === null ? EM : fmtDuration(o.avg_duration_seconds)],
          ["Recap agreement", `${core.recap_agreement.agreed} of ${core.recap_agreement.checked} checked (${pct(core.recap_agreement.rate)})`],
        ]}
      />
      <p className="rules-h">Success by track</p>
      <ul className="legend">
        {Object.entries(core.tracks).map(([t, v]) => (
          <li key={t}>
            <span>
              {trackLabel(t)} <span className="t-muted">{v.n_with_value} of {v.n_done} done</span>
            </span>
            <b className="num">{pct(v.success_rate)}</b>
          </li>
        ))}
      </ul>
    </Panel>
  );
}

function EvalCard({ report }: { report: EvalReport }) {
  const disagree = report.cases.filter((c) => c.recap_disagrees);
  return (
    <Panel className="span2">
      <PanelTitle>Extraction eval · {report.model}</PanelTitle>
      <p>
        {report.n} fixture transcripts from {report.fixtures.dir}, generated {fmtStamp(report.generated_at)}. {report.errors} errors.
      </p>
      <div className="runline">
        <q>Exact match on every scored field</q>
        <span className="clock num">{pct(report.exact_rate)}</span>
      </div>
      <ul className="legend">
        {Object.entries(report.by_track).map(([t, v]) => (
          <li key={t}>
            <span style={{ flex: 1, gap: 14 }}>
              <span style={{ minWidth: 110 }}>{trackLabel(t)}</span>
              <span className="talkbar" style={{ marginLeft: 0, maxWidth: 220 }}>
                <i style={{ width: `${Math.round(v.exact_rate * 100)}%` }} />
              </span>
            </span>
            <b className="num">
              {pct(v.exact_rate)} <i className="plain" style={{ font: "400 13px/1 var(--body)", color: "var(--ink-3)" }}>n={v.n}</i>
            </b>
          </li>
        ))}
      </ul>
      {disagree.length ? (
        <p className="note warn">
          Recap disagreed with the extractor on {disagree.length} {disagree.length === 1 ? "case" : "cases"}:{" "}
          {disagree.map((c) => c.id).join(", ")}. The eval scores the extractor against the fixture, so these are flagged, not failed.
        </p>
      ) : null}
      <details style={{ marginTop: 18 }}>
        <summary className="tog">All {report.cases.length} cases, expected against actual</summary>
        <ol className="transcript no-time">
          {report.cases.map((c) => (
            <li key={c.id} className={c.exact ? "agent" : "rep"}>
              <span className="sp">{c.track}</span>
              <p>
                <b>{c.id}</b>
                {c.exact ? <Tag>exact</Tag> : <Tag tone="warn">{Math.round(c.partial * 100)}% partial</Tag>}
                {c.recap_disagrees ? <Tag tone="warn">recap disagrees</Tag> : null}
                <br />
                <span className="t-muted">{c.scenario}</span>
                <br />
                {Object.entries(c.fields).map(([f, ok]) => (
                  <Tag key={f} tone={ok ? "line" : "warn"} first={false}>
                    {f} {ok ? "✓" : "✕"}
                  </Tag>
                ))}
                <br />
                <span className="num" style={{ fontSize: 14 }}>
                  expected {JSON.stringify(c.expected)}
                  <br />
                  actual {c.actual ? JSON.stringify(c.actual) : c.error ?? "none"}
                </span>
              </p>
            </li>
          ))}
        </ol>
      </details>
    </Panel>
  );
}

/** /evidence: what is real, what is simulated, and what the demo is held to. Every number is from the API. */
export function EvidenceView() {
  const [ev, setEv] = useState<EvidenceReport | null>(null);
  const [evError, setEvError] = useState<string | null>(null);
  const [acc, setAcc] = useState<AccuracyReport | null>(null);
  const [accError, setAccError] = useState<string | null>(null);
  const [evalReport, setEvalReport] = useState<EvalReport | null>(null);

  // One load each: /evidence calls Dial, so it is never polled.
  useEffect(() => {
    let cancelled = false;
    getEvidence()
      .then((r) => {
        if (!cancelled) setEv(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setEvError(describeError(e));
      });
    getAccuracy()
      .then((r) => {
        if (!cancelled) setAcc(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setAccError(describeError(e));
      });
    getEvalReport()
      .then((r) => {
        if (!cancelled) setEvalReport(r);
      })
      .catch(() => {
        if (!cancelled) setEvalReport(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const evalShown = acc?.eval ?? evalReport;

  return (
    <AppShell
      head={
        <PageTitle
          title="Evidence"
          lede="What is wired to a real phone line, what is simulated, and the rules the agent is held to. Every number here is read from the service, and seeded or simulated data is labeled wherever the service tells the two apart."
        />
      }
      wide
    >
      <ErrorNote message={evError} />
      {!ev && !evError ? <Loading>Loading evidence…</Loading> : null}

      {ev ? (
        <div className="board">
          <Panel className="span2" index={0}>
            <PanelTitle>Dial</PanelTitle>
            {ev.dial.account.error ? <p className="note warn">{ev.dial.account.error}</p> : null}
            <Facts
              rows={[
                ["Configured", ev.dial.account.configured ? "Yes" : "No"],
                ["Max concurrent calls", n((ev.dial.account.limits?.maxConcurrentCalls as number | undefined) ?? null)],
                ["Max call length", ev.dial.account.max_call_duration_seconds === null ? EM : fmtDuration(ev.dial.account.max_call_duration_seconds)],
                ["From number", ev.dial.account.from_number ?? EM],
                ["Numbers on the account", ev.dial.account.numbers.length ? ev.dial.account.numbers.map((x) => `${x.number ?? EM}${x.calling_enabled ? "" : " (calling off)"}`).join(", ") : EM],
              ]}
            />
            <p className="rules-h">Real calls placed through Dial · {ev.dial.n_real_calls}</p>
            {ev.dial.real_calls.length === 0 ? (
              <p>None yet. {ev.dial.n_simulated_calls} simulated and {ev.dial.n_undialed_calls} undialed rows exist.</p>
            ) : (
              <div className="list">
                {ev.dial.real_calls.map((c) => (
                  <Link key={c.id} href={`/calls/${encodeURIComponent(c.id)}?run=${encodeURIComponent(c.run_id)}`} className="mini">
                    <Dot state={c.state} />
                    <span className="who">
                      {c.target_name} <span className="t-muted num" style={{ font: "400 13px/1 var(--body)" }}>{c.target_phone}</span>
                    </span>
                    <span className="st">
                      {c.state}
                      {c.termination_type ? ` · ${c.termination_type}` : ""} · dial {c.dial_call_id}
                    </span>
                    <span className="res num">{fmtDuration(c.duration_seconds)}</span>
                  </Link>
                ))}
              </div>
            )}
          </Panel>

          {ev.loop ? (
            <Panel className="span2" index={1}>
              <PanelTitle href="/insights" linkText="Insights">
                Self-improvement loop
              </PanelTitle>
              <p>
                Every call is logged, the analysis layer reads the log, and the next call to the same vendor opens with what the last one learned. The
                agent says that sentence out loud. It supplies context; it does not rewrite its own prompts.
              </p>
              <Facts
                rows={[
                  ["Calls that opened with prior context", `${ev.loop.n_with_prior_context} of ${ev.loop.n_calls}`],
                  ["Runs where context was applied (events)", String(ev.loop.n_context_applied_events)],
                  ["Vendors called more than once", String(ev.loop.context_effect?.n_repeat_vendors ?? EM)],
                  [
                    "Repeat quotes vs first quotes",
                    ev.loop.context_effect?.quote?.later_lower_by_usd != null ? `$${ev.loop.context_effect.quote.later_lower_by_usd.toFixed(2)} lower on repeat calls` : EM,
                  ],
                  [
                    "Repeat retention calls vs first",
                    ev.loop.context_effect?.negotiate?.later_saved_more_usd != null ? `$${ev.loop.context_effect.negotiate.later_saved_more_usd.toFixed(2)}/mo more saved` : EM,
                  ],
                  ["Recommendations produced", String(ev.loop.n_recommendations ?? EM)],
                ]}
              />
              {ev.loop.error ? <p className="note warn">{ev.loop.error}</p> : null}
              <p className="rules-h">What the agent was told before recent calls</p>
              {ev.loop.examples.length === 0 ? <p>No call has carried prior context yet. Call a vendor twice and the second call shows up here.</p> : null}
              <ol className="rail">
                {ev.loop.examples.map((x) => (
                  <li key={x.call_id} style={{ animation: "none" }}>
                    <b>
                      {x.call_id && x.run_id ? <Link href={`/calls/${x.call_id}?run=${x.run_id}`}>{x.target_name}</Link> : x.target_name}
                      {x.simulated ? <Tag tone="warn">Simulated</Tag> : null}
                    </b>
                    <span>{x.prior_context}</span>
                  </li>
                ))}
              </ol>
            </Panel>
          ) : null}

          <Panel index={1}>
            <PanelTitle>Keewano</PanelTitle>
            <Facts
              rows={[
                ["Enabled", ev.keewano.enabled ? "Yes" : "No"],
                ["Token kind", ev.keewano.token_kind],
                ["Status", ev.keewano.status ? JSON.stringify(ev.keewano.status) : EM],
                ["Last error", ev.keewano.last_error ?? EM],
              ]}
            />
            {ev.keewano.auth_hint ? <p className="note warn">{ev.keewano.auth_hint}</p> : null}
            <p className="rules-h">Registered event types</p>
            <ul className="asks" style={{ gap: 8 }}>
              {Object.entries({ ...(ev.keewano.event_types ?? {}), ...(ev.keewano.companion_types ?? {}) }).map(([k, id]) => (
                <li key={k} style={{ padding: "8px 14px", fontSize: 14, animation: "none" }} title={id}>
                  {k}
                </li>
              ))}
              {!ev.keewano.event_types && !ev.keewano.companion_types ? <li style={{ animation: "none" }}>none registered</li> : null}
            </ul>
            <p className="rules-h">Events by type</p>
            <Counts counts={ev.events.by_type} label="Events by type" />
          </Panel>

          <Panel className="span2" index={2}>
            <PanelTitle>Events stream</PanelTitle>
            <p>The {Math.min(RECENT_LIMIT, ev.events.recent.length)} most recent rows of the events ledger, the system of record.</p>
            <ol className="transcript no-time">
              {ev.events.recent.slice(0, RECENT_LIMIT).map((e, i) => (
                <li key={e.id ?? i} className="rep">
                  <span className="sp">{e.type}</span>
                  <p>
                    <span className="num" style={{ color: "var(--ink)" }}>
                      {fmtStampFull(e.created_at)}
                    </span>
                    <br />
                    <span style={{ fontSize: 14, overflowWrap: "anywhere" }}>
                      {e.data || EM}
                      {e.truncated ? " …" : ""}
                    </span>
                  </p>
                </li>
              ))}
            </ol>
          </Panel>

          <div className="stack">
            <Panel index={3}>
              <PanelTitle>Calls by state</PanelTitle>
              <Counts counts={ev.calls.by_state} label="Calls by state" />
            </Panel>
            <Panel index={4}>
              <PanelTitle>Real, simulated, undialed</PanelTitle>
              <Counts counts={ev.calls.by_simulated} label="Calls by kind" />
            </Panel>
          </div>

          <Panel className="span2" index={5}>
            <PanelTitle>Policy</PanelTitle>
            <Facts
              rows={[
                ["AI disclosure", ev.policy.ai_disclosure ?? EM],
                ["Summary closer", ev.policy.summary_closer ?? EM],
                ["Only vendor rows dialable", ev.policy.only_vendor_rows_dialable ? "Yes" : "No"],
                ["Multiplier label", ev.policy.multiplier_label],
                ["Dial overhead", ev.policy.dial_overhead_seconds === null ? EM : `${ev.policy.dial_overhead_seconds} s per sequential call`],
                ["Max calls per run", String(ev.policy.max_calls_per_run)],
                ["System of record", ev.policy.system_of_record],
                ["Analysis sink", ev.policy.analysis_sink],
              ]}
            />
          </Panel>

          <Panel index={6}>
            <PanelTitle>Build</PanelTitle>
            <Facts
              rows={[
                ["Commit", ev.build.commit ?? EM],
                ["Store", ev.build.store],
                ["Python", ev.build.python],
                ["Generated", fmtStampFull(ev.generated_at)],
              ]}
            />
          </Panel>
        </div>
      ) : null}

      <section className="sec" aria-labelledby="acc-h">
        <h2 id="acc-h">Accuracy</h2>
        <ErrorNote message={accError} />
        {!acc && !accError ? <Loading>Loading accuracy…</Loading> : null}
        {acc ? (
          <>
            <p>{acc.note}</p>
            <div className="board" style={{ marginTop: 26 }}>
              <AccuracyBlock core={acc} title={`All calls · ${acc.overall.n_calls} (cap ${acc.cap})`} />
              <AccuracyBlock core={acc.split.real} title="Real Dial calls" note="simulated = false" />
              <AccuracyBlock core={acc.split.simulated} title="Simulated and seeded" note="simulated = true; fixture rows" />
            </div>
            {acc.recap_agreement.definition ? <p className="dash-note">Recap agreement: {acc.recap_agreement.definition}.</p> : null}
          </>
        ) : null}
        {evalShown ? (
          <div className="board" style={{ marginTop: 26 }}>
            <EvalCard report={evalShown} />
          </div>
        ) : null}
      </section>
    </AppShell>
  );
}
