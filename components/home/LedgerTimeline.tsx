"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Button, Chip, ErrorNote } from "@/components/ui";
import { fireLedger } from "@/lib/api";
import { EM, fmtDay, fmtUsd, shortId, toLocalDay } from "@/lib/format";
import type { LedgerKind, LedgerRow } from "@/lib/types";

const KIND_TONE: Record<LedgerKind, "warn" | "accent" | "ok" | "muted"> = {
  promo_expiry: "warn",
  renewal: "accent",
  appointment: "ok",
  quote: "muted",
};

function sortByExpiry(rows: LedgerRow[]): LedgerRow[] {
  return rows.slice().sort((a, b) => {
    if (!a.expires_on && !b.expires_on) return 0;
    if (!a.expires_on) return 1;
    if (!b.expires_on) return -1;
    return a.expires_on < b.expires_on ? -1 : a.expires_on > b.expires_on ? 1 : 0;
  });
}

function SourceLink({ row }: { row: LedgerRow }) {
  if (!row.source_call) return <span className="text-dim">{EM}</span>;
  if (row.run_id) {
    return (
      <Link href={`/runs/${row.run_id}#call-${row.source_call}`} className="text-accent hover:underline">
        call …{shortId(row.source_call)}
      </Link>
    );
  }
  return (
    <span className="text-muted" title={row.source_call}>
      call …{shortId(row.source_call)}
    </span>
  );
}

/**
 * Dated facts the calls produced, soonest expiry first, plus the act-two control. The as_of date
 * is a seeded override (BUILD-PLAN §12.3): the UI says so whenever it differs from today.
 */
export function LedgerTimeline({ rows }: { rows: LedgerRow[] | null }) {
  const router = useRouter();
  const [today, setToday] = useState<string>("");
  const [asOf, setAsOf] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = window.setTimeout(() => {
      const day = toLocalDay(new Date());
      setToday(day);
      setAsOf((prev) => prev || day);
    }, 0);
    return () => window.clearTimeout(id);
  }, []);

  const sorted = useMemo(() => (rows ? sortByExpiry(rows) : []), [rows]);
  const overridden = asOf !== "" && today !== "" && asOf !== today;
  const dueCount = asOf ? sorted.filter((r) => r.expires_on && r.expires_on <= asOf).length : 0;

  const fire = async () => {
    if (!asOf || busy) return;
    setBusy(true);
    setError(null);
    try {
      const run = await fireLedger(asOf);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {rows === null ? (
        <p className="font-mono text-xs text-dim">loading…</p>
      ) : sorted.length === 0 ? (
        <p className="text-sm text-muted">Nothing dated yet. A quote, a promo, or an appointment lands here.</p>
      ) : (
        <ol className="divide-y divide-line rounded-lg border border-line bg-panel">
          {sorted.map((row) => {
            const due = Boolean(asOf && row.expires_on && row.expires_on <= asOf);
            return (
              <li
                key={row.id}
                className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1 px-4 py-3 sm:grid-cols-[7rem_minmax(0,1fr)_5rem_8rem_7rem]"
              >
                <Chip tone={KIND_TONE[row.kind] ?? "muted"}>{row.kind.replace("_", " ")}</Chip>
                <span className="truncate text-sm">{row.label}</span>
                <span className="tnum font-mono text-sm sm:text-right">{fmtUsd(row.amount_usd)}</span>
                <span className={`tnum font-mono text-xs ${due ? "text-warn" : "text-muted"}`}>
                  {fmtDay(row.expires_on)}
                  {due ? " · due" : ""}
                </span>
                <span className="font-mono text-xs">
                  <SourceLink row={row} />
                </span>
              </li>
            );
          })}
        </ol>
      )}

      <div className="rounded-lg border border-dashed border-line p-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="block">
            <span className="mb-1 block font-mono text-xs uppercase tracking-wide text-muted">
              as of {overridden ? "(overridden)" : "(today)"}
            </span>
            <input
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              className="rounded-md border border-line bg-panel px-3 py-2 font-mono text-sm text-fg focus:border-accent focus:outline-none"
            />
          </label>
          <Button onClick={fire} disabled={busy || !asOf}>
            {busy ? "firing…" : "Act two: fire due ledger"}
          </Button>
          <span className="font-mono text-xs text-muted sm:pb-2">
            {dueCount} due {dueCount === 1 ? "row" : "rows"}
          </span>
        </div>
        <p className="mt-3 text-xs text-dim">
          {overridden
            ? `Seeded date override: the ledger is evaluated as of ${fmtDay(asOf)}, not today (${fmtDay(today)}). No elapsed time is being faked; the clock is set and labeled.`
            : "Pick a date to seed the clock forward. Rows whose expiry is on or before that date fire a retention run that opens with the prior quote as context."}
        </p>
        <ErrorNote message={error} />
      </div>
    </div>
  );
}
