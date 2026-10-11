import Link from "next/link";

import { callHref, summarizeOutcome, TONE_CLASS } from "@/components/calls/summary";
import { Chip, Tag } from "@/components/ui";
import { fmtDuration, fmtDurationWords } from "@/lib/format";
import { TRACK_ORDER, trackLabel, type AggregateView, type RankedCall } from "@/lib/types";

/**
 * The finished-run view from run-done.html: results ranked per track straight from
 * aggregate.tracks, then the paragraph that shows how the "est." multiplier was worked out. The
 * prototype's "Standard / Student / You save" columns had no backing field and were not ported.
 */
export function RunResults({ aggregate, wallSeconds }: { aggregate: AggregateView; wallSeconds: number | null }) {
  const groups = TRACK_ORDER.map((t) => [t, aggregate.tracks?.[t] ?? []] as const).filter(([, rows]) => rows.length > 0);
  const mult = aggregate.multiplier;
  const seq = aggregate.sequential_estimate_seconds;
  const wall = aggregate.wall_clock_seconds ?? wallSeconds;

  return (
    <section className="border-b border-line px-4 py-5" aria-label="Results">
      {groups.length === 0 ? (
        <p className="text-sm text-muted">No ranked results. Every call either failed or produced nothing to rank.</p>
      ) : (
        groups.map(([track, rows]) => (
          <div key={track} className="mt-5 first:mt-0">
            <h3 className="font-mono text-[11px] uppercase tracking-widest text-dim">{trackLabel(track)}</h3>
            <ol className="mt-1 divide-y divide-line">
              {rows.map((c: RankedCall) => {
                const s = summarizeOutcome(c);
                return (
                  <li key={c.id} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 py-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_5rem]">
                    <span className="min-w-0">
                      <span className="flex items-center gap-2">
                        <Link href={callHref(c)} className="truncate font-display text-base font-semibold tracking-tight hover:text-mint">
                          {c.target_name}
                        </Link>
                        {c.best ? <Tag>Best</Tag> : null}
                        {c.simulated ? <Chip tone="warn">simulated</Chip> : null}
                      </span>
                      {s.sub ? <small className="block truncate text-xs text-muted" title={s.sub}>{s.sub}</small> : null}
                    </span>
                    <span className={`tnum font-display text-lg font-semibold tracking-tight sm:text-right ${TONE_CLASS[s.tone]}`}>{s.main}</span>
                    <span className="tnum col-span-2 font-mono text-xs text-dim sm:col-span-1 sm:text-right">
                      {fmtDuration(c.duration_seconds)}
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ))
      )}

      <p className="mt-5 max-w-3xl text-xs text-muted">
        {mult !== null && mult !== undefined ? (
          <>
            <strong className="text-fg">How the {mult.toFixed(1)}× is worked out (est.):</strong> {aggregate.n_completed}{" "}
            {aggregate.n_completed === 1 ? "call" : "calls"} took {fmtDurationWords(aggregate.talk_seconds)} of talk time. Made one
            after another, with an assumed {aggregate.dial_overhead_seconds}s of dialing and hold each, that is about{" "}
            {fmtDurationWords(seq)} against {fmtDurationWords(wall)} here.
          </>
        ) : (
          <>
            <strong className="text-fg">No speed estimate.</strong> The multiplier needs at least one completed call and a wall
            clock; it assumes {aggregate.dial_overhead_seconds}s of dialing and hold per sequential call.
          </>
        )}
        {aggregate.n_simulated > 0
          ? ` ${aggregate.n_simulated} of ${aggregate.n_calls} rows ran on the simulated timeline, not a live Dial call.`
          : ""}
        {aggregate.n_failed > 0 ? ` ${aggregate.n_failed} failed and ${aggregate.n_failed === 1 ? "is" : "are"} not counted.` : ""}
      </p>
    </section>
  );
}
