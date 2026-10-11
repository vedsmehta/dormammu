"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { callHref, summarizeOutcome, TONE_CLASS } from "@/components/calls/summary";
import { Chip, Dot, ErrorNote, Loading, PageHeader, Tag, Wave } from "@/components/ui";
import { getRecentRuns } from "@/lib/api";
import { describeError, fmtDuration, fmtStamp } from "@/lib/format";
import { isTerminal, trackLabel, type AssembledRun, type Call } from "@/lib/types";

const RUN_LIMIT = 20;

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

function runActive(run: AssembledRun): boolean {
  if (run.calls.length === 0) return run.status === "dispatching" || run.status === "running";
  return run.calls.some((c) => !isTerminal(c.state));
}

function CallLine({ call }: { call: Call }) {
  const s = summarizeOutcome(call);
  const live = !isTerminal(call.state);
  return (
    <Link
      href={callHref(call)}
      className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-t border-line py-4 transition hover:pl-2 sm:grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1.5fr)_5rem_1rem]"
    >
      <Dot state={call.state} />
      <span className="min-w-0">
        <span className="block truncate font-display text-lg font-semibold tracking-tight">{call.target_name}</span>
        <small className="block truncate text-xs text-muted">
          {trackLabel(call.track)}
          {call.simulated ? " · simulated" : ""}
        </small>
      </span>
      <span className="col-span-2 col-start-2 min-w-0 sm:col-span-1 sm:col-start-auto">
        <span className={`block truncate font-display text-lg font-semibold tracking-tight ${TONE_CLASS[s.tone]}`}>
          {s.main}
          {call.state === "talking" ? <Wave /> : null}
        </span>
        {s.sub ? (
          <small className="block truncate text-xs text-muted" title={s.sub}>
            {s.sub}
          </small>
        ) : null}
      </span>
      <span className="tnum row-start-1 text-right font-mono text-xs text-muted sm:row-start-auto">
        {live ? <span className="text-ok">Live</span> : fmtDuration(call.duration_seconds)}
        <small className="block text-dim">Call</small>
      </span>
      <span className="hidden text-dim sm:inline" aria-hidden="true">
        →
      </span>
    </Link>
  );
}

export function CallsList() {
  const [runs, setRuns] = useState<AssembledRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  useEffect(() => {
    let cancelled = false;
    getRecentRuns(RUN_LIMIT)
      .then((r) => {
        if (cancelled) return;
        setRuns(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describeError(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const total = (runs ?? []).reduce((n, r) => n + r.calls.length, 0);
  const groups = (runs ?? [])
    .map((r) => ({ run: r, calls: r.calls.filter((c) => matches(c, filter)) }))
    .filter((g) => g.calls.length > 0 || (filter === "all" && g.run.calls.length === 0));

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <PageHeader
        title="Calls"
        lede="Everything Dormammu has called for you, grouped by request. Open any one to read what was said and what came back."
      />

      <div className="flex flex-wrap gap-2" aria-label="Filter">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            aria-pressed={filter === f.key}
            className={`rounded-full border px-3 py-1.5 text-xs transition ${
              filter === f.key ? "border-fg bg-fg text-bg" : "border-line bg-panel text-muted hover:border-muted hover:text-fg"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="mt-4">
        <ErrorNote message={error} />
      </div>

      {runs === null && !error ? (
        <div className="mt-6">
          <Loading>loading calls…</Loading>
        </div>
      ) : null}

      {runs !== null && runs.length === 0 ? (
        <p className="mt-8 text-sm text-muted">
          No calls yet.{" "}
          <Link href="/" className="text-accent hover:underline">
            Start a request
          </Link>
          .
        </p>
      ) : null}

      {runs !== null && runs.length > 0 && groups.length === 0 ? (
        <p className="mt-8 text-sm text-muted">No calls match this filter.</p>
      ) : null}

      {groups.map(({ run, calls }) => {
        const live = runActive(run);
        return (
          <section key={run.id} className="mt-10" aria-label={run.request}>
            <div className="grid gap-2 pb-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
              <div>
                <time className="flex flex-wrap items-center gap-2 text-xs text-dim">
                  {fmtStamp(run.created_at)}
                  {live ? <Tag>Live</Tag> : null}
                  {run.status === "failed" ? <Chip tone="bad">failed</Chip> : null}
                  {run.aggregate && run.aggregate.n_simulated > 0 ? <Chip tone="warn">{run.aggregate.n_simulated} simulated</Chip> : null}
                </time>
                <q className="mt-1 block font-display text-xl font-semibold leading-tight tracking-tight sm:text-2xl">{run.request}</q>
              </div>
              <Link href={`/runs/${run.id}`} className="text-sm text-teal underline underline-offset-4 hover:text-fg">
                {live ? "Watch the run" : "Open the run"}
              </Link>
            </div>
            {calls.length === 0 ? (
              <p className="border-t border-line py-4 text-sm text-muted">
                {live ? "Planning the calls…" : "This run produced no calls."}
              </p>
            ) : (
              calls.map((c) => <CallLine key={c.id} call={c} />)
            )}
          </section>
        );
      })}

      {runs !== null && runs.length > 0 ? (
        <p className="mt-10 font-mono text-[11px] text-dim">
          {total} {total === 1 ? "call" : "calls"} across {runs.length} {runs.length === 1 ? "request" : "requests"}
          {runs.length >= RUN_LIMIT ? ` (the API lists the ${RUN_LIMIT} most recent)` : ""}.
        </p>
      ) : null}
    </main>
  );
}
