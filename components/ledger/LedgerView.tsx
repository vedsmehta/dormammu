"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { AppShell, PageTitle } from "@/components/shell";
import { Button, ErrorNote, Field, Loading, Tag } from "@/components/ui";
import { fireLedger, getLedger } from "@/lib/api";
import { describeError, fmtDay, fmtUsd, shortId, toLocalDay, tidy } from "@/lib/format";
import type { LedgerKind, LedgerRow } from "@/lib/types";

const KIND_TONE: Record<LedgerKind, "mint" | "warn" | "line"> = {
  promo_expiry: "warn",
  renewal: "line",
  appointment: "mint",
  quote: "line",
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
  if (!row.source_call) return null;
  const href = row.run_id
    ? `/calls/${encodeURIComponent(row.source_call)}?run=${encodeURIComponent(row.run_id)}`
    : `/calls/${encodeURIComponent(row.source_call)}`;
  return (
    <Link className="from" href={href} title={row.source_call}>
      From call …{shortId(row.source_call)}
    </Link>
  );
}

/** The .rail.wide from ledger.html: one entry per dated fact, the due ones lit. */
function Rail({ rows, due, past }: { rows: LedgerRow[]; due?: boolean; past?: boolean }) {
  return (
    <ol className="rail wide">
      {rows.map((r, i) => (
        <li key={r.id} className={due ? "next" : past ? "past" : undefined} style={{ "--i": i } as CSSProperties}>
          <time className={due ? "due" : undefined}>
            {r.expires_on ? `${due ? "Due · " : ""}${fmtDay(r.expires_on)}` : r.effective_on ? `From ${fmtDay(r.effective_on)}` : "Undated"}
          </time>
          <b>{tidy(r.label)}</b>
          <p>
            <Tag tone={KIND_TONE[r.kind] ?? "line"} first>
              {r.kind.replace("_", " ")}
            </Tag>
            {r.amount_usd !== null ? <span className="num"> {fmtUsd(r.amount_usd)}</span> : null}
            {r.effective_on && r.expires_on ? <span className="t-muted"> · effective {fmtDay(r.effective_on)}</span> : null}
          </p>
          <SourceLink row={r} />
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
    <AppShell
      head={
        <PageTitle
          title="Ledger"
          lede="Every dated fact the calls produced: a quote, a promo end, a renewal, an appointment. Each entry links to the call it came from."
        />
      }
    >
      <ErrorNote message={loadError} />
      {rows === null ? <Loading>Loading ledger…</Loading> : null}

      {rows !== null ? (
        <>
          <section className="sec" aria-labelledby="l1">
            <h2 id="l1">Needs attention</h2>
            {due.length === 0 ? (
              <p>
                Nothing is due as of {asOf ? fmtDay(asOf) : "today"}.{upcoming.length ? " Move the date forward below to see what would fire." : ""}
              </p>
            ) : (
              <Rail rows={due} due />
            )}

            <section className="panel asof" aria-label="As-of date">
              <h2>
                <span>As of · {overridden ? <span className="t-warn">overridden</span> : "seeded to today"}</span>
              </h2>
              <div className="acts">
                <Field label="Date" tight>
                  <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
                </Field>
                <Button onClick={fire} disabled={busy || !asOf || due.length === 0} size="sm">
                  {busy ? "Firing…" : `Fire due ledger (${due.length} ${due.length === 1 ? "row" : "rows"})`}
                </Button>
                {overridden ? (
                  <button type="button" onClick={() => setAsOf(today)} className="more">
                    Reset to today
                  </button>
                ) : null}
              </div>
              <p>
                {overridden
                  ? `Seeded date override: the ledger is evaluated as of ${fmtDay(asOf)}, not today (${fmtDay(today)}). No elapsed time is being faked; the clock is set and labeled.`
                  : "Rows whose expiry is on or before this date fire a retention run that opens with the prior quote as context. Pick a later date to seed the clock forward."}
              </p>
              <ErrorNote message={error} />
            </section>
          </section>

          <section className="sec" aria-labelledby="l2">
            <h2 id="l2">Upcoming</h2>
            {upcoming.length === 0 ? <p>Nothing dated after {asOf ? fmtDay(asOf) : "today"}.</p> : <Rail rows={upcoming} />}
          </section>

          <section className="sec" aria-labelledby="l3">
            <h2 id="l3">On file</h2>
            {undated.length === 0 ? <p>No undated entries.</p> : <Rail rows={undated} past />}
          </section>

          {sorted.length === 0 && !loadError ? (
            <p className="dash-note">Nothing in the ledger yet. A quote, a promo end, or an appointment lands here when a call produces one.</p>
          ) : null}
        </>
      ) : null}
    </AppShell>
  );
}
