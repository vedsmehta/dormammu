"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button, Chip, ErrorNote, Loading, PageHeader } from "@/components/ui";
import { fireLedger, getLedger } from "@/lib/api";
import { describeError, EM, fmtDay, fmtUsd, shortId, toLocalDay } from "@/lib/format";
import type { LedgerKind, LedgerRow } from "@/lib/types";

const KIND_TONE: Record<LedgerKind, "warn" | "accent" | "ok" | "muted"> = {
  promo_expiry: "warn",
  renewal: "accent",
  appointment: "ok",
  quote: "muted",
};

function sortByDate(rows: LedgerRow[]): LedgerRow[] {
  return rows.slice().sort((a, b) => {
    const ak = a.expires_on ?? a.effective_on ?? "";
    const bk = b.expires_on ?? b.effective_on ?? "";
    if (!ak && !bk) return 0;
    if (!ak) return 1;
    if (!bk) return -1;
    return ak < bk ? -1 : ak > bk ? 1 : 0;
  });
}

function SourceLink({ row }: { row: LedgerRow }) {
  if (!row.source_call) return <span className="text-dim">{EM}</span>;
  if (row.run_id) {
    return (
      <Link
        href={`/calls/${encodeURIComponent(row.source_call)}?run=${encodeURIComponent(row.run_id)}`}
        className="text-teal underline underline-offset-4 hover:text-fg"
      >
        From call …{shortId(row.source_call)}
      </Link>
    );
  }
  return (
    <Link href={`/calls/${encodeURIComponent(row.source_call)}`} className="text-teal underline underline-offset-4 hover:text-fg" title={row.source_call}>
      From call …{shortId(row.source_call)}
    </Link>
  );
}

function Rail({ rows, due, dim }: { rows: LedgerRow[]; due?: boolean; dim?: boolean }) {
  return (
    <ol className={`mt-4 border-l border-line pl-6 ${dim ? "opacity-70" : ""}`}>
      {rows.map((r) => (
        <li key={r.id} className="relative pb-7 last:pb-0">
          <span
            className={`absolute -left-[31px] top-1.5 h-2.5 w-2.5 rounded-full ${
              due ? "bg-mint shadow-[0_0_0_4px_rgba(134,247,226,0.25)]" : "border-2 border-dim bg-bg"
            }`}
            aria-hidden="true"
          />
          <time className={`font-mono text-xs ${due ? "text-warn" : "text-dim"}`}>
            {r.expires_on ? `${due ? "Due · " : ""}${fmtDay(r.expires_on)}` : r.effective_on ? `From ${fmtDay(r.effective_on)}` : "Undated"}
          </time>
          <b className="mt-1 block font-display text-lg font-semibold tracking-tight sm:text-xl">{r.label}</b>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted">
            <Chip tone={KIND_TONE[r.kind] ?? "muted"}>{r.kind.replace("_", " ")}</Chip>
            {r.amount_usd !== null ? <span className="tnum font-mono">{fmtUsd(r.amount_usd)}</span> : null}
            {r.effective_on && r.expires_on ? <span className="text-dim">effective {fmtDay(r.effective_on)}</span> : null}
          </p>
          <p className="mt-2 text-sm">
            <SourceLink row={r} />
          </p>
        </li>
      ))}
    </ol>
  );
}

/**
 * The ledger page from ledger.html on real rows, plus the act-two control. The as_of date is a seeded
 * clock (BUILD-PLAN §12.3): the UI labels it as seeded to today or overridden, and never fakes
 * elapsed time.
 */
export function LedgerView() {
  const router = useRouter();
  const [rows, setRows] = useState<LedgerRow[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [today, setToday] = useState<string>("");
  const [asOf, setAsOf] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLedger()
      .then((r) => {
        if (cancelled) return;
        const day = toLocalDay(new Date());
        setToday(day);
        setAsOf((prev) => prev || day);
        setRows(r);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const day = toLocalDay(new Date());
        setToday(day);
        setAsOf((prev) => prev || day);
        setRows([]);
        setLoadError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sorted = useMemo(() => (rows ? sortByDate(rows) : []), [rows]);
  const overridden = asOf !== "" && today !== "" && asOf !== today;
  const due = asOf ? sorted.filter((r) => r.expires_on && r.expires_on <= asOf) : [];
  const upcoming = asOf ? sorted.filter((r) => r.expires_on && r.expires_on > asOf) : [];
  const undated = sorted.filter((r) => !r.expires_on);

  const fire = async () => {
    if (!asOf || busy) return;
    setBusy(true);
    setError(null);
    try {
      const run = await fireLedger(asOf);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <PageHeader
        title="Ledger"
        lede="Every dated fact the calls produced: a quote, a promo end, a renewal, an appointment. Each entry links to the call it came from."
      />

      <ErrorNote message={loadError} />
      {rows === null ? <Loading>loading ledger…</Loading> : null}

      {rows !== null ? (
        <>
          <section aria-labelledby="l1">
            <h2 id="l1" className="font-mono text-xs uppercase tracking-widest text-teal">
              Needs attention
            </h2>
            {due.length === 0 ? (
              <p className="mt-3 text-sm text-muted">
                Nothing is due as of {asOf ? fmtDay(asOf) : "today"}.
                {upcoming.length ? " Move the date forward below to see what would fire." : ""}
              </p>
            ) : (
              <Rail rows={due} due />
            )}

            <div className="mt-6 rounded-2xl border border-dashed border-line p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <label className="block">
                  <span className="mb-1 block font-mono text-xs uppercase tracking-wide text-muted">
                    as of · {overridden ? <span className="text-warn">overridden</span> : "seeded to today"}
                  </span>
                  <input
                    type="date"
                    value={asOf}
                    onChange={(e) => setAsOf(e.target.value)}
                    className="rounded-md border border-line bg-panel px-3 py-2 font-mono text-sm text-fg focus:border-accent focus:outline-none"
                  />
                </label>
                <Button onClick={fire} disabled={busy || !asOf || due.length === 0} variant="mint">
                  {busy ? "firing…" : `Fire due ledger (${due.length} ${due.length === 1 ? "row" : "rows"})`}
                </Button>
                {overridden ? (
                  <button type="button" onClick={() => setAsOf(today)} className="text-xs text-dim hover:text-fg sm:pb-2">
                    reset to today
                  </button>
                ) : null}
              </div>
              <p className="mt-3 text-xs text-dim">
                {overridden
                  ? `Seeded date override: the ledger is evaluated as of ${fmtDay(asOf)}, not today (${fmtDay(today)}). No elapsed time is being faked; the clock is set and labeled.`
                  : "Rows whose expiry is on or before this date fire a retention run that opens with the prior quote as context. Pick a later date to seed the clock forward."}
              </p>
              <div className="mt-2">
                <ErrorNote message={error} />
              </div>
            </div>
          </section>

          <section className="mt-12" aria-labelledby="l2">
            <h2 id="l2" className="font-mono text-xs uppercase tracking-widest text-teal">
              Upcoming
            </h2>
            {upcoming.length === 0 ? (
              <p className="mt-3 text-sm text-muted">Nothing dated after {asOf ? fmtDay(asOf) : "today"}.</p>
            ) : (
              <Rail rows={upcoming} />
            )}
          </section>

          <section className="mt-12" aria-labelledby="l3">
            <h2 id="l3" className="font-mono text-xs uppercase tracking-widest text-teal">
              On file
            </h2>
            {undated.length === 0 ? (
              <p className="mt-3 text-sm text-muted">No undated entries.</p>
            ) : (
              <Rail rows={undated} dim />
            )}
          </section>

          {sorted.length === 0 && !loadError ? (
            <p className="mt-8 text-sm text-muted">
              Nothing in the ledger yet. A quote, a promo end, or an appointment lands here when a call produces one.
            </p>
          ) : null}
        </>
      ) : null}
    </main>
  );
}
