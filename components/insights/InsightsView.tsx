"use client";

import { useEffect, useState } from "react";

import { AppShell, PageTitle } from "@/components/shell";
import { ErrorNote, Loading, Panel, PanelTitle, Tag } from "@/components/ui";
import { getInsights, type InsightsReport, type VendorScorecard } from "@/lib/api";
import { describeError, EM, fmtDuration, fmtStamp, fmtUsd } from "@/lib/format";
import { TRACK_ORDER, trackLabel } from "@/lib/types";

const pct = (r: number | null | undefined) => (r === null || r === undefined ? EM : `${Math.round(r * 100)}%`);
const usd = (v: number | null | undefined) => (v === null || v === undefined ? EM : fmtUsd(v));

function ScoreCard({ card, index }: { card: VendorScorecard; index: number }) {
  const facts: [string, string][] = [
    ["Calls", `${card.n_calls} (${card.n_done} done, ${card.n_failed} failed${card.n_live ? `, ${card.n_live} live` : ""})`],
    ["Answer rate", pct(card.answer_rate)],
    ["Avg length", card.avg_duration_seconds === null ? EM : fmtDuration(card.avg_duration_seconds)],
    ["Last called", card.last_called_at ? fmtStamp(card.last_called_at) : EM],
  ];
  if (card.track === "quote") {
    facts.push(["Best quote", usd(card.best_quote_usd)], ["Latest quote", usd(card.latest_quote_usd)], ["Trend", card.quote_trend ?? EM]);
  } else if (card.track === "negotiate") {
    facts.push(["Saved per month", usd(card.total_saved_usd_per_month)], ["Reduction rate", pct(card.reduction_rate)]);
  } else if (card.track === "schedule") {
    facts.push(["Confirmed rate", pct(card.confirmed_rate)]);
  }
  const failures = Object.entries(card.failure_breakdown);
  return (
    <Panel index={index}>
      <PanelTitle>
        {card.name}
        {card.n_simulated > 0 ? <Tag tone="warn">{card.n_simulated} simulated</Tag> : null}
      </PanelTitle>
      {card.takeaway ? <p className="ptitle" style={{ fontSize: 18 }}>{card.takeaway}</p> : null}
      <dl className="facts">
        {facts.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {failures.length ? <p>Failed as {failures.map(([k, v]) => `${k.replace(/_/g, " ")} ×${v}`).join(", ")}.</p> : null}
    </Panel>
  );
}

/** /insights: the analysis report, recommendations first, then one card per vendor. */
export function InsightsView() {
  const [report, setReport] = useState<InsightsReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getInsights()
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const cards = Object.values(report?.vendor_scorecards ?? {});
  const byTrack = TRACK_ORDER.map((t) => [t, cards.filter((c) => c.track === t)] as const).filter(([, v]) => v.length > 0);
  const other = cards.filter((c) => !TRACK_ORDER.includes(c.track as (typeof TRACK_ORDER)[number]));
  const cov = report?.coverage;
  const fx = report?.failure_modes;
  const ce = report?.context_effect;

  return (
    <AppShell
      head={
        <PageTitle
          title="Insights"
          lede="What the calls so far say about each vendor, and what to do differently next time. Patterns and context only: nothing here rewrites prompts or vendor templates."
        />
      }
      wide
    >
      <ErrorNote message={error} />
      {!report && !error ? <Loading>Loading insights…</Loading> : null}

      {report ? (
        <>
          <section className="sec" aria-labelledby="rec-h">
            <h2 id="rec-h">Recommendations</h2>
            {report.recommendations.length === 0 ? (
              <p>Nothing to recommend yet. Recommendations need repeat calls to the same vendor.</p>
            ) : (
              <ol className="rail wide">
                {report.recommendations.map((r, i) => (
                  <li key={`${r.kind}-${r.vendor_key ?? i}`} className={i === 0 ? "next" : undefined}>
                    <time>
                      {r.kind.replace(/_/g, " ")}
                      {r.track ? ` · ${trackLabel(r.track)}` : ""} · score {r.score}
                    </time>
                    <b>{r.text}</b>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {byTrack.map(([track, rows]) => (
            <section key={track} className="sec" aria-label={trackLabel(track)}>
              <h2>{trackLabel(track)}</h2>
              <div className="board" style={{ marginTop: 22 }}>
                {rows.map((c, i) => (
                  <ScoreCard key={c.vendor_key} card={c} index={i} />
                ))}
              </div>
            </section>
          ))}
          {other.length ? (
            <section className="sec" aria-label="Other vendors">
              <h2>Other</h2>
              <div className="board" style={{ marginTop: 22 }}>
                {other.map((c, i) => (
                  <ScoreCard key={c.vendor_key} card={c} index={i} />
                ))}
              </div>
            </section>
          ) : null}

          {ce ? (
            <section className="sec" aria-labelledby="ctx-h">
              <h2 id="ctx-h">What prior context changed</h2>
              <p>
                {ce.n_repeat_vendors} {ce.n_repeat_vendors === 1 ? "vendor was" : "vendors were"} called more than once. {ce.n_later_with_prior_context} of{" "}
                {ce.n_later_calls} later calls carried prior context ({pct(ce.share_later_with_prior_context)}).
              </p>
              <dl className="facts" style={{ marginTop: 18, maxWidth: 640 }}>
                <div>
                  <dt>Answer rate</dt>
                  <dd>
                    first {pct(ce.first_answer_rate)} → later {pct(ce.later_answer_rate)}
                  </dd>
                </div>
                <div>
                  <dt>Quotes</dt>
                  <dd>
                    {ce.quote.n_vendors ? `first ${usd(ce.quote.avg_first_usd)} → later ${usd(ce.quote.avg_later_usd)} (lower by ${usd(ce.quote.later_lower_by_usd)})` : EM}
                  </dd>
                </div>
                <div>
                  <dt>Negotiations</dt>
                  <dd>
                    {ce.negotiate.n_vendors
                      ? `saved ${usd(ce.negotiate.avg_first_saved_usd)} → ${usd(ce.negotiate.avg_later_saved_usd)} (more by ${usd(ce.negotiate.later_saved_more_usd)})`
                      : EM}
                  </dd>
                </div>
                <div>
                  <dt>Appointments</dt>
                  <dd>{ce.schedule.n_vendors ? `confirmed ${pct(ce.schedule.first_confirmed_rate)} → ${pct(ce.schedule.later_confirmed_rate)}` : EM}</dd>
                </div>
              </dl>
            </section>
          ) : null}

          {fx ? (
            <section className="sec" aria-labelledby="fail-h">
              <h2 id="fail-h">Failure modes</h2>
              <p>
                {fx.n_failed} of {fx.n_terminal} finished calls failed ({pct(fx.failure_rate)}).
              </p>
              {Object.keys(fx.counts).length ? (
                <ul className="legend" style={{ maxWidth: 520 }}>
                  {Object.entries(fx.counts).map(([k, v]) => (
                    <li key={k}>
                      <span>{k.replace(/_/g, " ")}</span>
                      <b className="num">
                        {v} <i style={{ font: "400 13px/1 var(--body)", color: "var(--ink-3)" }}>{pct(fx.share[k])}</i>
                      </b>
                    </li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          {cov ? (
            <p className="est">
              <strong>Coverage:</strong> {cov.n_runs} runs and {cov.n_calls} calls ({cov.n_done} done, {cov.n_failed} failed, {cov.n_live} live)
              {cov.first_call_at ? `, from ${fmtStamp(cov.first_call_at)} to ${fmtStamp(cov.last_call_at)}` : ""}. {cov.n_simulated} calls are simulated and{" "}
              {cov.n_seeded} are seeded demo data: <strong>{cov.label}</strong> ({pct(cov.share_synthetic)} of all calls). {cov.n_runs_seeded} of the runs are seeded.
              {cov.runs_truncated ? ` Only the ${cov.max_runs} most recent runs are read.` : ""} Generated {fmtStamp(report.generated_at)}.
            </p>
          ) : null}
        </>
      ) : null}
    </AppShell>
  );
}
