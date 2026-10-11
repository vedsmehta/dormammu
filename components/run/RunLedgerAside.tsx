"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Chip } from "@/components/ui";
import { getLedger } from "@/lib/api";
import { describeError, fmtDay, fmtUsd } from "@/lib/format";
import type { AssembledRun, LedgerKind, LedgerRow } from "@/lib/types";

const KIND_TONE: Record<LedgerKind, "warn" | "accent" | "ok" | "muted"> = {
  promo_expiry: "warn",
  renewal: "accent",
  appointment: "ok",
  quote: "muted",
};

/**
 * "Landing in your ledger" from run.html: the dated facts this run's calls wrote. Loaded once, then
 * again when the run goes quiet, since the ledger is written as calls complete.
 */
export function RunLedgerAside({ run, active }: { run: AssembledRun; active: boolean }) {
  const [rows, setRows] = useState<LedgerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getLedger()
      .then((r) => {
        if (cancelled) return;
        setRows(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, [run.id, active]);

  const callIds = new Set(run.calls.map((c) => c.id));
  const mine = (rows ?? []).filter((r) => (r.source_call && callIds.has(r.source_call)) || r.run_id === run.id);

  return (
    <aside className="rounded-2xl border border-line bg-panel p-5" aria-labelledby="run-ledger-h">
      <div className="flex items-center justify-between gap-3">
        <h2 id="run-ledger-h" className="font-mono text-xs uppercase tracking-widest text-teal">
          Landing in your ledger
        </h2>
        <Link href="/ledger" className="text-xs text-muted underline underline-offset-4 hover:text-fg">
          Open the ledger
        </Link>
      </div>
      {error ? <p className="mt-3 font-mono text-xs text-warn">{error}</p> : null}
      {rows === null && !error ? <p className="mt-3 font-mono text-xs text-dim">loading…</p> : null}
      {rows !== null && mine.length === 0 ? (
        <p className="mt-3 text-sm text-muted">
          {active ? "Nothing yet. A quote, a promo end, or an appointment lands here as calls finish." : "This run wrote nothing dated."}
        </p>
      ) : null}
      {mine.length > 0 ? (
        <ol className="mt-3 border-l border-line pl-4">
          {mine.map((r) => (
            <li key={r.id} className="relative pb-4 last:pb-0">
              <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-dim bg-panel" aria-hidden="true" />
              <time className="font-mono text-xs text-dim">
                {r.expires_on ? `until ${fmtDay(r.expires_on)}` : r.effective_on ? `from ${fmtDay(r.effective_on)}` : "undated"}
              </time>
              <b className="block text-sm font-medium">{r.label}</b>
              <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                <Chip tone={KIND_TONE[r.kind] ?? "muted"}>{r.kind.replace("_", " ")}</Chip>
                {r.amount_usd !== null ? <span className="tnum font-mono">{fmtUsd(r.amount_usd)}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : null}
    </aside>
  );
}
