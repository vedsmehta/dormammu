"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties } from "react";

import { getLedger } from "@/lib/api";
import { describeError, fmtDay, fmtUsd } from "@/lib/format";
import type { AssembledRun, LedgerRow } from "@/lib/types";

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
    <aside aria-labelledby="run-ledger-h">
      <h2 id="run-ledger-h">Landing in your ledger</h2>
      {error ? <p className="note warn">{error}</p> : null}
      {rows === null && !error ? <p className="dash-note">Loading…</p> : null}
      {rows !== null && mine.length === 0 ? (
        <p>{active ? "Nothing yet. A quote, a promo end, or an appointment lands here as calls finish." : "This run wrote nothing dated."}</p>
      ) : null}
      {mine.length > 0 ? (
        <ol className="rail">
          {mine.map((r, i) => (
            <li key={r.id} className={i === 0 ? "next" : undefined} style={{ "--i": i } as CSSProperties}>
              <time>{r.expires_on ? `Until ${fmtDay(r.expires_on)}` : r.effective_on ? `From ${fmtDay(r.effective_on)}` : "Undated"}</time>
              <b>{r.label}</b>
              <p>
                {r.kind.replace("_", " ")}
                {r.amount_usd !== null ? ` · ${fmtUsd(r.amount_usd)}` : ""}
              </p>
            </li>
          ))}
        </ol>
      ) : null}
      <Link className="more" href="/ledger" style={mine.length > 0 ? { marginTop: 0 } : undefined}>
        Open the ledger
      </Link>
    </aside>
  );
}
