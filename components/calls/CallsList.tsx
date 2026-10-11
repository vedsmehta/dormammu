"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { callHref, summarizeOutcome } from "@/components/calls/summary";
import { AppShell, PageTitle } from "@/components/shell";
import { Dot, ErrorNote, Loading, Tag, Wave } from "@/components/ui";
import { getCalls, type CallWithRequest } from "@/lib/api";
import { describeError, fmtDuration, fmtStamp, parseTs, tidy } from "@/lib/format";
import { isTerminal, trackLabel, type Call } from "@/lib/types";

// GET /calls returns rows newest first with the run's request and phones already masked.
const CALL_LIMIT = 200;

type Filter = "all" | "quote" | "schedule" | "negotiate" | "live" | "failed";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "quote", label: "Quotes" },
  { key: "schedule", label: "Appointments" },
  { key: "negotiate", label: "Negotiations" },
  { key: "live", label: "Live" },
  { key: "failed", label: "Failed" },
];

function matches(call: Call, f: Filter): boolean {
  switch (f) {
    case "all":
      return true;
    case "live":
      return !isTerminal(call.state);
    case "failed":
      return call.state === "failed";
    default:
      return call.track === f;
  }
}

type Group = { runId: string; request: string; calls: CallWithRequest[] };

/** Rows grouped by run, in the order the newest call of each run appears. */
function groupByRun(rows: CallWithRequest[]): Group[] {
  const groups = new Map<string, Group>();
  for (const c of rows) {
    const g = groups.get(c.run_id) ?? { runId: c.run_id, request: c.request ?? "", calls: [] };
    g.calls.push(c);
    groups.set(c.run_id, g);
  }
  return [...groups.values()];
}

function earliest(calls: Call[]): string | null {
  let best: string | null = null;
  let bestMs = Infinity;
  for (const c of calls) {
    const ms = parseTs(c.started_at);
    if (ms !== null && ms < bestMs) {
      bestMs = ms;
      best = c.started_at;
    }
  }
  return best;
}

/** One .crow from calls.html: dot, vendor, what came back, length, chevron. */
function CallLine({ call }: { call: Call }) {
  const s = summarizeOutcome(call);
  const live = !isTerminal(call.state);
  const tone = s.tone === "ok" ? "t-talking" : s.tone === "warn" || s.tone === "bad" ? "t-warn" : s.tone === "muted" ? "t-muted" : "";
  return (
    <Link href={callHref(call)} className="crow">
      <Dot state={call.state} />
      <span className="who">
        {call.target_name}
        <small>
          {trackLabel(call.track)}
          {call.simulated ? " · simulated" : ""}
        </small>
      </span>
      <span className="what">
        <span className={tone}>{s.main}</span>
        {call.state === "talking" ? <Wave /> : null}
        {s.sub ? <small title={s.sub}>{s.sub}</small> : null}
      </span>
      <span className="meta num">
        {live ? <span className="t-talking">Live</span> : fmtDuration(call.duration_seconds)}
        <small>Call</small>
      </span>
      <span className="chev" aria-hidden="true">
        →
      </span>
    </Link>
  );
}

export function CallsList() {
  const [rows, setRows] = useState<CallWithRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let cancelled = false;
    getCalls(CALL_LIMIT, 0)
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
  }, []);

  const groups = groupByRun(rows ?? []);
  const shown = groups.map((g) => ({ ...g, calls: g.calls.filter((c) => matches(c, filter)) })).filter((g) => g.calls.length > 0);

  return (
    <AppShell
      head={
        <PageTitle
          title="Calls"
          lede="Everything Dormammu has called for you, grouped by request. Open any one to read what was said and what came back."
        />
      }
    >
      <div className="filters" aria-label="Filter">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" onClick={() => setFilter(f.key)} aria-pressed={filter === f.key} className={filter === f.key ? "on" : undefined}>
            {f.label}
          </button>
        ))}
      </div>

      <ErrorNote message={error} />
      {rows === null && !error ? <Loading>Loading calls…</Loading> : null}

      {rows !== null && rows.length === 0 ? (
        <p className="request-empty" style={{ borderTop: 0 }}>
          No calls yet.{" "}
          <Link className="more" style={{ marginTop: 0 }} href="/">
            Start a request
          </Link>
          .
        </p>
      ) : null}

      {rows !== null && rows.length > 0 && shown.length === 0 ? <p className="request-empty">No calls match this filter.</p> : null}

      {shown.map((g) => {
        const live = g.calls.some((c) => !isTerminal(c.state));
        const simulated = g.calls.filter((c) => c.simulated).length;
        return (
          <section key={g.runId} className="request" aria-label={tidy(g.request)}>
            <div className="request-head">
              <div>
                <time>
                  {fmtStamp(earliest(g.calls))}
                  {live ? <Tag>Live</Tag> : null}
                  {simulated > 0 ? <Tag tone="warn">{simulated} simulated</Tag> : null}
                </time>
                <q>{tidy(g.request)}</q>
              </div>
              <Link href={`/runs/${g.runId}`}>{live ? "Watch the run" : "Open the run"}</Link>
            </div>
            {g.calls.map((c) => (
              <CallLine key={c.id} call={c} />
            ))}
          </section>
        );
      })}

      {rows !== null && rows.length > 0 ? (
        <p className="dash-note">
          {rows.length} {rows.length === 1 ? "call" : "calls"} across {groups.length} {groups.length === 1 ? "request" : "requests"}
          {rows.length >= CALL_LIMIT ? ` (the ${CALL_LIMIT} most recent)` : ""}.
        </p>
      ) : null}
    </AppShell>
  );
}
