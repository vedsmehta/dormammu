"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { callHref, summarizeOutcome } from "@/components/calls/summary";
import { Button, ErrorNote, Tag } from "@/components/ui";
import { chooseOptions } from "@/lib/api";
import { describeError, fmtDuration, fmtDurationWords, fmtStampFull } from "@/lib/format";
import { TRACK_ORDER, trackLabel, type AggregateView, type BookingLink, type RankedCall } from "@/lib/types";

/** An option the user can still go with: a finished quote, or an appointment slot that is not confirmed yet. */
function choosable(c: RankedCall): boolean {
  const o = c.outcome;
  if (c.state !== "done" || !o) return false;
  if (o.track === "quote") return o.amount_usd != null;
  if (o.track === "schedule") return Boolean(o.slot_iso) && !o.confirmed;
  return false;
}

/**
 * The finished-run view from run-done.html: results ranked per track straight from
 * aggregate.tracks, then the paragraph that shows how the "est." multiplier was worked out. The
 * prototype's "Standard / Student / You save" columns have no backing field, so each row carries the
 * one result the call produced and its length.
 */
export function RunResults({
  aggregate,
  wallSeconds,
  runId,
  bookings = [],
}: {
  aggregate: AggregateView;
  wallSeconds: number | null;
  runId?: string;
  bookings?: BookingLink[];
}) {
  const router = useRouter();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const booked = new Map(bookings.map((b) => [b.call_id, b]));
  const canChoose = Boolean(runId) && Object.values(aggregate.tracks ?? {}).some((rows) => (rows ?? []).some((c) => choosable(c) && !booked.has(c.id)));

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const book = async () => {
    if (!runId || busy || picked.size === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await chooseOptions(runId, [...picked]);
      router.push(`/runs/${res.run.id}`);
    } catch (e) {
      setError(describeError(e));
      setBusy(false);
    }
  };

  const groups = TRACK_ORDER.map((t) => [t, aggregate.tracks?.[t] ?? []] as const).filter(([, rows]) => rows.length > 0);
  const mult = aggregate.multiplier;
  const seq = aggregate.sequential_estimate_seconds;
  const wall = aggregate.wall_clock_seconds ?? wallSeconds;

  return (
    <section aria-label="Results">
      {canChoose ? (
        <p className="lead">Here&rsquo;s what I found. Which ones do you want to go with? Tick them and I&rsquo;ll call back to book or set them up.</p>
      ) : null}
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
                    {runId && choosable(c) && !booked.has(c.id) ? (
                      <input type="checkbox" className="pick" checked={picked.has(c.id)} disabled={busy} onChange={() => toggle(c.id)} aria-label={`Go with ${c.target_name}`} />
                    ) : null}
                    <Link href={callHref(c)}>{c.target_name}</Link>
                    {booked.has(c.id) ? (
                      <Link href={`/runs/${booked.get(c.id)!.run_id}`} className="tag">
                        {booked.get(c.id)!.confirmed && booked.get(c.id)!.slot_iso ? `Booked ${fmtStampFull(booked.get(c.id)!.slot_iso)}` : "Booking call placed"}
                      </Link>
                    ) : null}
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

      {canChoose ? (
        <div className="savebar">
          <Button onClick={book} disabled={busy || picked.size === 0}>
            {busy ? "Placing booking calls…" : picked.size === 0 ? "Tick the ones you want" : `Go with ${picked.size}: call to book`}
          </Button>
          <ErrorNote message={error} />
        </div>
      ) : null}

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
