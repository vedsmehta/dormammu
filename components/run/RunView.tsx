"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { CallRow } from "@/components/run/CallRow";
import { RunFooter } from "@/components/run/RunFooter";
import { Brand, Chip, ErrorNote, StatusChip } from "@/components/ui";
import { UserChip } from "@/components/UserChip";
import { ApiError, getRun, tickRun } from "@/lib/api";
import { fmtDuration, parseTs } from "@/lib/format";
import { getSupabaseBrowser } from "@/lib/supabase/client";
import { isTerminal, type AssembledRun, type Call } from "@/lib/types";

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
  const failed = calls.filter((c) => c.state === "failed").length;
  const wall = wallClockSeconds(run, serverNow, active);
  const bestIds = new Set((run?.aggregate?.tracks?.quote ?? []).filter((c) => c.best).map((c) => c.id));

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          <Brand />
          <Link href="/" className="font-mono text-xs text-dim hover:text-fg">
            ← runs
          </Link>
        </div>
        <UserChip />
      </div>

      {error ? (
        <div className="space-y-3">
          <ErrorNote message={error} />
          <Link href="/" className="text-sm text-accent hover:underline">
            back to runs
          </Link>
        </div>
      ) : null}

      {!run && !error ? <p className="font-mono text-sm text-dim">loading run…</p> : null}

      {run ? (
        <section className="overflow-hidden rounded-lg border border-line bg-panel">
          <header className="border-b border-line px-4 py-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h1 className="text-lg font-medium leading-snug sm:text-xl">&ldquo;{run.request}&rdquo;</h1>
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
            </div>
            <div className="tnum mt-2 flex flex-wrap items-center gap-x-2 font-mono text-sm text-muted">
              <span>
                {calls.length} {calls.length === 1 ? "call" : "calls"}
              </span>
              <span className="text-dim">·</span>
              <span className={connected > 0 ? "text-ok" : ""}>{connected} connected</span>
              <span className="text-dim">·</span>
              <span className={failed > 0 ? "text-bad" : ""}>{failed} failed</span>
              <span className="text-dim">·</span>
              <span className="text-fg">
                <span aria-hidden="true">⏱ </span>
                {fmtDuration(wall)}
              </span>
              {active ? <span className="animate-blink text-ok">▮</span> : null}
            </div>
          </header>

          {calls.length === 0 ? (
            <p className="px-4 py-6 font-mono text-sm text-dim">
              {active ? "no calls planned yet…" : "this run produced no calls"}
            </p>
          ) : (
            <ol className="divide-y divide-line">
              {calls.map((call) => (
                <CallRow key={call.id} call={call} serverNow={serverNow} best={bestIds.has(call.id)} />
              ))}
            </ol>
          )}

          <RunFooter aggregate={run.aggregate} wallSeconds={wall} active={active} nCalls={calls.length} />
        </section>
      ) : null}

      {tickError ? <p className="mt-3 font-mono text-xs text-warn">tick: {tickError}</p> : null}
    </main>
  );
}
