import Link from "next/link";

import { callHref, summarizeOutcome } from "@/components/calls/summary";
import { Tag } from "@/components/ui";
import { fmtDuration, fmtDurationWords } from "@/lib/format";
import { TRACK_ORDER, trackLabel, type AggregateView, type RankedCall } from "@/lib/types";

/**
 * The finished-run view from run-done.html: results ranked per track straight from
 * aggregate.tracks, then the paragraph that shows how the "est." multiplier was worked out. The
 * prototype's "Standard / Student / You save" columns have no backing field, so each row carries the
 * one result the call produced and its length.
 */
export function RunResults({ aggregate, wallSeconds }: { aggregate: AggregateView; wallSeconds: number | null }) {
  const groups = TRACK_ORDER.map((t) => [t, aggregate.tracks?.[t] ?? []] as const).filter(([, rows]) => rows.length > 0);
  const mult = aggregate.multiplier;
  const seq = aggregate.sequential_estimate_seconds;
  const wall = aggregate.wall_clock_seconds ?? wallSeconds;

  return (
    <section aria-label="Results">
      {groups.length === 0 ? (
        <p>No ranked results. Every call either failed or produced nothing to rank.</p>
      ) : (
        groups.map(([track, rows]) => (
          <div key={track} className="rgroup">
            <h3>{trackLabel(track)}</h3>
            <div className="rhead three">
              <span />
              <span>Result</span>
              <span>Call</span>
            </div>
            {rows.map((c: RankedCall) => {
              const s = summarizeOutcome(c);
              const tone = s.tone === "warn" || s.tone === "bad" ? " t-warn" : s.tone === "muted" ? " t-muted" : "";
              return (
                <div key={c.id} className="rrow three">
                  <span className="who">
                    <Link href={callHref(c)}>{c.target_name}</Link>
                    {c.best ? <Tag>Best</Tag> : null}
                    {c.simulated ? <Tag tone="warn">Simulated</Tag> : null}
                    {s.sub ? <small title={s.sub}>{s.sub}</small> : null}
                  </span>
                  <span className={`num big${tone}`}>{s.main}</span>
                  <span className="num">{fmtDuration(c.duration_seconds)}</span>
                </div>
              );
            })}
          </div>
        ))
      )}

      <p className="est">
        {mult !== null && mult !== undefined ? (
          <>
            <strong>How the {mult.toFixed(1)}× is worked out (est.):</strong> {aggregate.n_completed}{" "}
            {aggregate.n_completed === 1 ? "call" : "calls"} took {fmtDurationWords(aggregate.talk_seconds)} of talk time. Made one after
            another, with an assumed {aggregate.dial_overhead_seconds}s of dialing and hold each, that is about {fmtDurationWords(seq)}{" "}
            against {fmtDurationWords(wall)} here.
          </>
        ) : (
          <>
            <strong>No speed estimate.</strong> The multiplier needs at least one completed call and a wall clock; it assumes{" "}
            {aggregate.dial_overhead_seconds}s of dialing and hold per sequential call.
          </>
        )}
        {aggregate.n_simulated > 0 ? ` ${aggregate.n_simulated} of ${aggregate.n_calls} rows ran on the simulated timeline, not a live Dial call.` : ""}
        {aggregate.n_failed > 0 ? ` ${aggregate.n_failed} failed and ${aggregate.n_failed === 1 ? "is" : "are"} not counted.` : ""}
      </p>
    </section>
  );
}
