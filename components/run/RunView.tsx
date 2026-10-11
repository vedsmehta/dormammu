"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { summarizeOutcome } from "@/components/calls/summary";
import { CallRow } from "@/components/run/CallRow";
import { RunFooter } from "@/components/run/RunFooter";
import { RunLedgerAside } from "@/components/run/RunLedgerAside";
import { RunResults } from "@/components/run/RunResults";
import { Chip, Dot, ErrorNote, StatusChip } from "@/components/ui";
import { ApiError, getRun, tickRun } from "@/lib/api";
import { fmtDuration, parseTs } from "@/lib/format";
import { getSupabaseBrowser } from "@/lib/supabase/client";
import { isTerminal, TRACK_ORDER, trackLabel, type AssembledRun, type Call, type Track } from "@/lib/types";

const TICK_MS = 2000;
const CLOCK_MS = 250;
// Safety valve only: every call has a 180s max on the Dial side, so a run never needs this long.
const MAX_TICKS = 900;

type RealtimeState = "off" | "connecting" | "live" | "error";

function describe(e: unknown): string {
  if (e instanceof ApiError) return e.status === 404 ? "Run not found (404)." : e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

/** Monotonic merge: a row never moves backwards in state_rank, whichever channel delivered it. */
function mergeCall(calls: Call[], incoming: Partial<Call> & { id: string }): Call[] {
  const idx = calls.findIndex((c) => c.id === incoming.id);
  if (idx === -1) return [...calls, incoming as Call];
  const existing = calls[idx];
  if ((incoming.state_rank ?? 0) < (existing.state_rank ?? 0)) return calls;
  const next = calls.slice();
  next[idx] = { ...existing, ...incoming };
  return next;
}

/** The tick response is the render source; local rows that got ahead of it via Realtime stay ahead. */
function mergeRun(prev: AssembledRun | null, next: AssembledRun): AssembledRun {
  if (!prev) return next;
  const local = new Map(prev.calls.map((c) => [c.id, c]));
  const calls = next.calls.map((c) => {
    const p = local.get(c.id);
    return p && (p.state_rank ?? 0) > (c.state_rank ?? 0) ? { ...c, ...p } : c;
  });
  return { ...next, calls };
}

function runActive(run: AssembledRun | null): boolean {
  if (!run) return true;
  if (run.calls.length === 0) return run.status === "dispatching" || run.status === "running";
  return run.calls.some((c) => !isTerminal(c.state));
}

function wallClockSeconds(run: AssembledRun | null, serverNow: number | null, active: boolean): number | null {
  if (!run) return null;
  if (!active && run.aggregate?.wall_clock_seconds != null) return run.aggregate.wall_clock_seconds;
  const start = parseTs(run.created_at);
  if (start === null) return null;
  const end = run.completed_at ? parseTs(run.completed_at) : null;
  if (end !== null) return Math.max(0, (end - start) / 1000);
  if (!active) {
    const last = run.calls.reduce((m, c) => Math.max(m, parseTs(c.ended_at) ?? 0), 0);
    if (last > 0) return Math.max(0, (last - start) / 1000);
  }
  if (serverNow === null) return null;
  return Math.max(0, (serverNow - start) / 1000);
}

/** Calls grouped by track in the prototype's order, unknown tracks last. */
function groupByTrack(calls: Call[]): [string, Call[]][] {
  const groups = new Map<string, Call[]>();
  for (const c of calls) {
    const key = c.track ?? "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(c);
  }
  const ordered: [string, Call[]][] = [];
  for (const t of TRACK_ORDER) if (groups.has(t)) ordered.push([t, groups.get(t)!]);
  for (const [k, v] of groups) if (!TRACK_ORDER.includes(k as Track)) ordered.push([k, v]);
  return ordered;
}

export function RunView({ runId }: { runId: string }) {
  const [run, setRun] = useState<AssembledRun | null>(null);
  const [offsetMs, setOffsetMs] = useState(0);
  const [nowMs, setNowMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tickError, setTickError] = useState<string | null>(null);
  const [realtime, setRealtime] = useState<RealtimeState>("off");
  const inFlight = useRef(false);
  const ticks = useRef(0);

  // Server-authoritative clock: remember server_now - Date.now() from the latest response.
  const apply = useCallback((next: AssembledRun) => {
    const serverNow = parseTs(next.server_now);
    const localNow = Date.now();
    if (serverNow !== null) setOffsetMs(serverNow - localNow);
    setNowMs(localNow);
    setRun((prev) => mergeRun(prev, next));
  }, []);

  useEffect(() => {
    let cancelled = false;
    getRun(runId)
      .then((r) => {
        if (cancelled) return;
        apply(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(describe(e));
      });
    return () => {
      cancelled = true;
    };
  }, [runId, apply]);

  const active = runActive(run);

  // Tick every 2s while any call is non-terminal; stop as soon as every call is done or failed.
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(async () => {
      if (inFlight.current || ticks.current >= MAX_TICKS) return;
      inFlight.current = true;
      ticks.current += 1;
      try {
        const r = await tickRun(runId);
        apply(r);
        setTickError(null);
      } catch (e: unknown) {
        setTickError(describe(e));
      } finally {
        inFlight.current = false;
      }
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [runId, active, apply]);

  // Local interpolation between ticks. Freezes when the run goes quiet.
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setNowMs(Date.now()), CLOCK_MS);
    return () => window.clearInterval(id);
  }, [active]);

  // Supabase Realtime on calls for this run, only when Supabase is configured.
  useEffect(() => {
    const supabase = getSupabaseBrowser();
    if (!supabase) return;
    const channel = supabase
      .channel(`calls-${runId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "calls", filter: `run_id=eq.${runId}` },
        (payload) => {
          const row = payload.new as Partial<Call>;
          if (!row || typeof row.id !== "string") return;
          const incoming = row as Partial<Call> & { id: string };
          setRun((prev) => (prev ? { ...prev, calls: mergeCall(prev.calls, incoming) } : prev));
        },
      )
      .subscribe((status) => {
        const s: string = status;
        setRealtime(s === "SUBSCRIBED" ? "live" : s === "CLOSED" ? "connecting" : s === "TIMED_OUT" || s === "CHANNEL_ERROR" ? "error" : "connecting");
      });
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [runId]);

  const serverNow = nowMs === null ? null : nowMs + offsetMs;
  const calls = run?.calls ?? [];
  const connected = calls.filter((c) => c.state === "talking" || c.state === "extracting" || c.state === "done").length;
  const finished = calls.filter((c) => isTerminal(c.state)).length;
  const failed = calls.filter((c) => c.state === "failed").length;
  const wall = wallClockSeconds(run, serverNow, active);
  const bestQuote = (run?.aggregate?.tracks?.quote ?? []).find((c) => c.best) ?? null;
  const bestIds = new Set((run?.aggregate?.tracks?.quote ?? []).filter((c) => c.best).map((c) => c.id));
  const firstSlot = (run?.aggregate?.tracks?.schedule ?? []).find((c) => c.outcome && (c.outcome as { slot_iso?: string | null }).slot_iso) ?? null;
  const mult = run?.aggregate?.multiplier ?? null;
  const groups = groupByTrack(calls);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <div className="mb-4 flex items-center justify-between gap-4">
        <Link href="/" className="font-mono text-xs text-dim hover:text-fg">
          ← dashboard
        </Link>
        {run ? (
          <div className="flex items-center gap-2">
            <StatusChip status={run.status} />
            <Chip
              tone={realtime === "live" ? "ok" : realtime === "error" ? "bad" : "muted"}
              title={
                realtime === "off"
                  ? "Supabase not configured; polling only"
                  : `Realtime ${realtime}; polling every ${TICK_MS / 1000}s regardless`
              }
            >
              {realtime === "live" ? "live" : realtime === "off" ? "polling" : realtime}
            </Chip>
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="space-y-3">
          <ErrorNote message={error} />
          <Link href="/" className="text-sm text-accent hover:underline">
            back to the dashboard
          </Link>
        </div>
      ) : null}

      {!run && !error ? <p className="font-mono text-sm text-dim">loading run…</p> : null}

      {run ? (
        <>
          <header className="animate-rise py-6 text-center sm:py-10">
            <q className="inline-block max-w-3xl rounded-full border border-line bg-panel px-5 py-2 text-sm text-muted sm:text-base">
              {run.request}
            </q>
            <p className="tnum mt-5 flex items-center justify-center gap-4 font-display text-6xl font-semibold tracking-tight sm:text-8xl">
              {active ? <Dot state="talking" className="h-4 w-4" /> : null}
              {fmtDuration(wall)}
            </p>
            <p className="mt-2 text-muted">
              {calls.length === 0
                ? active
                  ? "Planning the calls…"
                  : "This run produced no calls"
                : active
                  ? `${calls.length - finished} of ${calls.length} ${calls.length === 1 ? "call" : "calls"} in progress at the same time`
                  : `${calls.length} ${calls.length === 1 ? "call" : "calls"}, all finished`}
            </p>
            <div className="mx-auto mt-6 grid max-w-2xl grid-cols-1 divide-y divide-line rounded-2xl border border-line bg-panel sm:grid-cols-3 sm:divide-x sm:divide-y-0">
              {active ? (
                <>
                  <div className="p-3">
                    <span className="block text-xs text-muted">Connected</span>
                    <b className="tnum block font-display text-xl font-semibold">
                      {connected} of {calls.length}
                    </b>
                  </div>
                  <div className="p-3">
                    <span className="block text-xs text-muted">Finished</span>
                    <b className="tnum block font-display text-xl font-semibold">
                      {finished} of {calls.length}
                      {failed ? <span className="ml-1 text-sm font-normal text-bad">({failed} failed)</span> : null}
                    </b>
                  </div>
                </>
              ) : (
                <>
                  <div className="p-3">
                    <span className="block text-xs text-muted">Than one at a time</span>
                    <b className={`tnum block font-display text-xl font-semibold ${mult !== null ? "text-ok" : "text-dim"}`}>
                      {mult !== null ? `${mult.toFixed(1)}× faster (est.)` : "no estimate"}
                    </b>
                  </div>
                  <div className="p-3">
                    <span className="block text-xs text-muted">Finished</span>
                    <b className="tnum block font-display text-xl font-semibold">
                      {finished - failed} of {calls.length}
                      {failed ? <span className="ml-1 text-sm font-normal text-bad">({failed} failed)</span> : null}
                    </b>
                  </div>
                </>
              )}
              <div className="p-3">
                <span className="block text-xs text-muted">
                  {bestQuote ? (active ? "Best quote so far" : "Best quote") : firstSlot ? "Appointment" : "Best quote"}
                </span>
                <b className="tnum block truncate font-display text-xl font-semibold">
                  {bestQuote ? summarizeOutcome(bestQuote).main : firstSlot ? summarizeOutcome(firstSlot).main : <span className="text-dim">none yet</span>}
                </b>
              </div>
            </div>
          </header>

          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)]">
            <section className="overflow-hidden rounded-2xl border border-line bg-panel">
              <header className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-3">
                <h2 className="font-mono text-xs uppercase tracking-widest text-teal">{active ? "This run" : "Results"}</h2>
                <div className="tnum flex flex-wrap items-center gap-x-2 font-mono text-xs text-muted">
                  <span className={connected > 0 ? "text-ok" : ""}>{connected} connected</span>
                  <span className="text-dim">·</span>
                  <span className={failed > 0 ? "text-bad" : ""}>{failed} failed</span>
                  {active ? <span className="animate-blink text-ok">▮</span> : null}
                </div>
              </header>

              {!active && run.aggregate ? <RunResults aggregate={run.aggregate} wallSeconds={wall} /> : null}

              {calls.length === 0 ? (
                <p className="px-4 py-6 font-mono text-sm text-dim">
                  {active ? "no calls planned yet…" : "this run produced no calls"}
                </p>
              ) : (
                groups.map(([track, rows]) => (
                  <div key={track}>
                    <h3 className="border-b border-line bg-panel-2 px-4 py-1.5 font-mono text-[11px] uppercase tracking-widest text-dim">
                      {trackLabel(track)}
                    </h3>
                    <ol className="divide-y divide-line">
                      {rows.map((call) => (
                        <CallRow key={call.id} call={call} serverNow={serverNow} best={bestIds.has(call.id)} />
                      ))}
                    </ol>
                  </div>
                ))
              )}

              <RunFooter aggregate={run.aggregate} wallSeconds={wall} active={active} nCalls={calls.length} />
            </section>

            <RunLedgerAside run={run} active={active} />
          </div>
        </>
      ) : null}

      {tickError ? <p className="mt-3 font-mono text-xs text-warn">tick: {tickError}</p> : null}
    </main>
  );
}
