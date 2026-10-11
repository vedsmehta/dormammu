"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { callHref, summarizeOutcome, TONE_CLASS } from "@/components/calls/summary";
import { RequestForm } from "@/components/home/RequestForm";
import { RunList } from "@/components/home/RunList";
import { Chip, Dot, ErrorNote, Loading, Panel, PanelTitle, Tag, Wave } from "@/components/ui";
import { assembleRuns, getLedger, getProfile, getRuns, tickRun } from "@/lib/api";
import {
  describeError,
  firstName,
  fmtDay,
  fmtDayFromTs,
  fmtDuration,
  fmtDurationWords,
  fmtUsd,
  greetingFor,
  parseTs,
  toLocalDay,
} from "@/lib/format";
import { isTerminal, trackLabel, type AssembledRun, type Call, type LedgerRow, type Profile, type RunListItem } from "@/lib/types";

// How many recent runs the dashboard assembles (calls + aggregate). Every figure below is computed
// over exactly these runs and says so.
const ASSEMBLE_LIMIT = 6;
const TICK_MS = 2000;

type CallWithRun = Call & { request: string };

function runActive(run: AssembledRun): boolean {
  if (run.calls.length === 0) return run.status === "dispatching" || run.status === "running";
  return run.calls.some((c) => !isTerminal(c.state));
}

function wallSeconds(run: AssembledRun): number | null {
  const start = parseTs(run.created_at);
  if (start === null) return null;
  if (!runActive(run) && run.aggregate?.wall_clock_seconds != null) return run.aggregate.wall_clock_seconds;
  const end = run.completed_at ? parseTs(run.completed_at) : parseTs(run.server_now);
  if (end === null) return null;
  return Math.max(0, (end - start) / 1000);
}

function StatTile({
  label,
  value,
  unit,
  note,
  href,
  hot,
  live,
}: {
  label: string;
  value: string;
  unit?: string;
  note: string;
  href: string;
  hot?: boolean;
  live?: boolean;
}) {
  return (
    <Link
      href={href}
      className={`block rounded-2xl border p-4 transition hover:-translate-y-0.5 ${
        hot ? "border-mint/40 bg-mint/10" : "border-line bg-panel hover:border-muted"
      }`}
    >
      <span className="flex items-center gap-2 text-xs text-muted">
        {live ? <Dot state="talking" /> : null}
        {label}
      </span>
      <b className="tnum mt-2 block font-display text-3xl font-semibold tracking-tight sm:text-4xl">
        {value}
        {unit ? <span className="ml-1 text-sm font-normal text-muted">{unit}</span> : null}
      </b>
      <small className="mt-1 block text-xs text-dim">{note}</small>
    </Link>
  );
}

function MiniRow({ call }: { call: CallWithRun }) {
  const s = summarizeOutcome(call);
  return (
    <Link
      href={callHref(call)}
      className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5 border-t border-line py-2.5 text-sm transition hover:pl-2"
    >
      <Dot state={call.state} />
      <span className="min-w-0">
        <span className="block truncate font-medium">{call.target_name}</span>
        <span className="block truncate text-xs text-muted">
          {trackLabel(call.track)}
          {s.sub ? ` · ${s.sub}` : ""}
        </span>
      </span>
      <span className={`tnum text-right font-mono text-sm ${TONE_CLASS[s.tone]}`}>
        {s.main}
        {call.state === "talking" ? <Wave /> : null}
      </span>
    </Link>
  );
}

function LedgerLine({ row, due }: { row: LedgerRow; due: boolean }) {
  return (
    <li className="border-t border-line py-2.5 first:border-t-0">
      <time className={`block font-mono text-xs ${due ? "text-warn" : "text-dim"}`}>
        {row.expires_on ? `${due ? "Due " : ""}${fmtDay(row.expires_on)}` : row.effective_on ? `From ${fmtDay(row.effective_on)}` : "Undated"}
      </time>
      <b className="block text-sm font-medium">{row.label}</b>
      <span className="text-xs text-muted">
        {row.kind.replace("_", " ")}
        {row.amount_usd !== null ? ` · ${fmtUsd(row.amount_usd)}` : ""}
      </span>
    </li>
  );
}

export function Dashboard({ email }: { email: string | null }) {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [assembled, setAssembled] = useState<AssembledRun[] | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [today, setToday] = useState<string>("");
  const [greeting, setGreeting] = useState<string>("Hello");
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getRuns(), getLedger(), getProfile()]).then(async ([r, l, p]) => {
      if (cancelled) return;
      const now = new Date();
      setToday(toLocalDay(now));
      setGreeting(greetingFor(now));
      const problems: string[] = [];
      if (l.status === "fulfilled") setLedger(l.value);
      else problems.push(`ledger: ${describeError(l.reason)}`);
      if (p.status === "fulfilled") setProfile(p.value);
      if (r.status === "fulfilled") {
        setRuns(r.value);
        const full = await assembleRuns(r.value, ASSEMBLE_LIMIT);
        if (!cancelled) setAssembled(full);
      } else {
        problems.push(`runs: ${describeError(r.reason)}`);
        setRuns([]);
        setAssembled([]);
      }
      if (!cancelled) setError(problems.length ? problems.join(" · ") : null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const featured = assembled?.find(runActive) ?? null;
  const featuredId = featured?.id ?? null;

  // Keep the featured live run moving: the tick is what drives ingest and simulation, and nobody
  // else ticks it while the run page is closed. Stops as soon as every call is terminal.
  const applyTick = useCallback((next: AssembledRun) => {
    setAssembled((prev) => (prev ? prev.map((r) => (r.id === next.id ? next : r)) : prev));
  }, []);

  useEffect(() => {
    if (!featuredId) return;
    const id = window.setInterval(async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        applyTick(await tickRun(featuredId));
      } catch {
        // A failed tick is retried on the next interval; the run page reports tick errors.
      } finally {
        inFlight.current = false;
      }
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [featuredId, applyTick]);

  const allCalls: CallWithRun[] = (assembled ?? []).flatMap((r) => r.calls.map((c) => ({ ...c, request: r.request })));
  const liveCalls = allCalls.filter((c) => !isTerminal(c.state));
  const doneCalls = allCalls.filter((c) => c.state === "done");
  const failedCalls = allCalls.filter((c) => c.state === "failed");
  const talkSeconds = (assembled ?? []).reduce((sum, r) => sum + (r.aggregate?.talk_seconds ?? 0), 0);
  const recentCalls = allCalls
    .slice()
    .sort((a, b) => (parseTs(b.started_at) ?? 0) - (parseTs(a.started_at) ?? 0))
    .slice(0, 6);
  const scope = assembled ? `across the ${assembled.length} most recent ${assembled.length === 1 ? "request" : "requests"}` : "";

  const sortedLedger = (ledger ?? []).slice().sort((a, b) => {
    const ak = a.expires_on ?? a.effective_on ?? "9999";
    const bk = b.expires_on ?? b.effective_on ?? "9999";
    return ak < bk ? -1 : ak > bk ? 1 : 0;
  });
  const due = today ? sortedLedger.filter((r) => r.expires_on && r.expires_on <= today) : [];
  const upcoming = today ? sortedLedger.filter((r) => !(r.expires_on && r.expires_on <= today)).slice(0, 4) : [];

  const name = firstName(profile?.display_name, email);
  const loaded = runs !== null && assembled !== null;
  const sub = !loaded
    ? "Loading your runs…"
    : liveCalls.length > 0
      ? `${liveCalls.length} ${liveCalls.length === 1 ? "call is" : "calls are"} running${due.length ? ` and ${due.length} ledger ${due.length === 1 ? "row needs" : "rows need"} your yes` : ""}.`
      : due.length
        ? `Nothing is running. ${due.length} ledger ${due.length === 1 ? "row needs" : "rows need"} your yes.`
        : runs && runs.length === 0
          ? "Nothing yet. Tell Dormammu what you need handled."
          : "Nothing is running right now.";

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">
      <header className="animate-rise">
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          {greeting}, {name}
        </h1>
        <p className="mt-2 text-muted">{sub}</p>
        <div className="mt-6">
          <RequestForm />
        </div>
      </header>

      <div className="mt-4">
        <ErrorNote message={error} />
      </div>

      <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Running now"
          value={loaded ? String(liveCalls.length) : "…"}
          note={liveCalls.length ? "calls at the same time" : "no live calls"}
          href={featured ? `/runs/${featured.id}` : "/calls"}
          hot={liveCalls.length > 0}
          live={liveCalls.length > 0}
        />
        <StatTile
          label="Needs you"
          value={ledger ? String(due.length) : "…"}
          note={due.length ? "ledger rows due now" : "nothing due in the ledger"}
          href="/ledger"
        />
        <StatTile
          label="Calls made"
          value={loaded ? String(allCalls.length) : "…"}
          note={scope || "loading"}
          href="/calls"
        />
        <StatTile
          label="On the phone for you"
          value={loaded ? fmtDurationWords(talkSeconds) : "…"}
          note={loaded ? `${doneCalls.length} finished ${doneCalls.length === 1 ? "call" : "calls"}, ${scope}` : "loading"}
          href="/calls"
        />
      </div>

      <div className="mt-6 grid gap-5 lg:grid-cols-3">
        <Panel className="p-5 lg:col-span-2">
          <PanelTitle href={featured ? `/runs/${featured.id}` : undefined} linkText="Open the run">
            Running now
          </PanelTitle>
          {assembled === null ? (
            <div className="mt-4">
              <Loading />
            </div>
          ) : featured ? (
            <>
              <div className="mt-4 flex items-end justify-between gap-4">
                <q className="font-display text-lg font-semibold leading-snug tracking-tight sm:text-xl">{featured.request}</q>
                <span className="tnum font-display text-3xl font-semibold tracking-tight">
                  {fmtDuration(wallSeconds(featured))}
                </span>
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2 font-mono text-xs text-muted">
                <span>
                  {featured.calls.filter((c) => !isTerminal(c.state)).length} of {featured.calls.length} still going
                </span>
                {featured.aggregate && featured.aggregate.n_simulated > 0 ? (
                  <Chip tone="warn">{featured.aggregate.n_simulated} simulated</Chip>
                ) : null}
              </div>
              <div className="mt-3">
                {featured.calls.map((c) => (
                  <MiniRow key={c.id} call={{ ...c, request: featured.request }} />
                ))}
              </div>
            </>
          ) : (
            <p className="mt-4 text-sm text-muted">No run is live. Start one above and it shows up here while the calls are out.</p>
          )}
        </Panel>

        <div className="grid gap-5">
          <Panel className="p-5">
            <PanelTitle href="/ledger" linkText="Ledger">
              Needs you
            </PanelTitle>
            {ledger === null ? (
              <div className="mt-4">
                <Loading />
              </div>
            ) : due.length === 0 ? (
              <p className="mt-4 text-sm text-muted">Nothing needs your yes right now.</p>
            ) : (
              <>
                <ul className="mt-3">
                  {due.slice(0, 3).map((r) => (
                    <LedgerLine key={r.id} row={r} due />
                  ))}
                </ul>
                <Link
                  href="/ledger"
                  className="mt-3 inline-flex rounded-full bg-mint px-3 py-1.5 text-xs font-medium text-bg hover:bg-white"
                >
                  Review and fire the due rows
                </Link>
              </>
            )}
          </Panel>
          <Panel className="p-5">
            <PanelTitle href="/ledger" linkText="Ledger">
              Coming up
            </PanelTitle>
            {ledger === null ? (
              <div className="mt-4">
                <Loading />
              </div>
            ) : upcoming.length === 0 ? (
              <p className="mt-4 text-sm text-muted">Nothing dated yet. A quote, a promo, or an appointment lands here.</p>
            ) : (
              <ul className="mt-3">
                {upcoming.map((r) => (
                  <LedgerLine key={r.id} row={r} due={false} />
                ))}
              </ul>
            )}
          </Panel>
        </div>

        <Panel className="p-5 lg:col-span-2">
          <PanelTitle href="/calls" linkText="All calls">
            Recent calls
          </PanelTitle>
          {assembled === null ? (
            <div className="mt-4">
              <Loading />
            </div>
          ) : recentCalls.length === 0 ? (
            <p className="mt-4 text-sm text-muted">No calls yet.</p>
          ) : (
            <div className="mt-3">
              {recentCalls.map((c) => (
                <Link
                  key={c.id}
                  href={callHref(c)}
                  className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 border-t border-line py-2.5 text-sm transition hover:pl-2"
                >
                  <Dot state={c.state} />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{c.target_name}</span>
                    <span className="block truncate text-xs text-muted">{summarizeOutcome(c).main}</span>
                  </span>
                  <span className="tnum font-mono text-xs text-dim">{fmtDayFromTs(c.started_at)}</span>
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel className="p-5">
          <PanelTitle>How calls ended</PanelTitle>
          {assembled === null ? (
            <div className="mt-4">
              <Loading />
            </div>
          ) : allCalls.length === 0 ? (
            <p className="mt-4 text-sm text-muted">No calls to count yet.</p>
          ) : (
            <>
              <div
                className="mt-4 flex h-3 gap-0.5 overflow-hidden rounded-full"
                role="img"
                aria-label={`Of ${allCalls.length} calls: ${doneCalls.length} got an answer, ${liveCalls.length} still going, ${failedCalls.length} failed`}
              >
                {doneCalls.length ? <i className="bg-teal" style={{ flex: doneCalls.length }} /> : null}
                {liveCalls.length ? <i className="bg-mint" style={{ flex: liveCalls.length }} /> : null}
                {failedCalls.length ? <i className="bg-warn" style={{ flex: failedCalls.length }} /> : null}
              </div>
              <ul className="mt-3 text-sm">
                {[
                  ["Got an answer", doneCalls.length, "bg-teal"],
                  ["Still going", liveCalls.length, "bg-mint"],
                  ["Failed", failedCalls.length, "bg-warn"],
                ].map(([label, n, cls]) => (
                  <li key={String(label)} className="flex items-center justify-between border-t border-line py-2 first:border-t-0">
                    <span className="flex items-center gap-2 text-muted">
                      <i className={`h-2.5 w-2.5 rounded-full ${cls}`} />
                      {label}
                    </span>
                    <b className="tnum font-mono">{n}</b>
                  </li>
                ))}
              </ul>
              <p className="mt-3 text-xs text-dim">Counted {scope}.</p>
            </>
          )}
        </Panel>
      </div>

      <section className="mt-10">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="font-mono text-xs uppercase tracking-widest text-teal">Recent requests</h2>
          <Link href="/onboarding" className="text-xs text-dim hover:text-fg">
            edit roster →
          </Link>
        </div>
        <RunList runs={runs} />
        {runs && runs.length >= 20 ? <p className="mt-2 text-xs text-dim">The API lists the 20 most recent runs.</p> : null}
      </section>

      {featured ? (
        <p className="mt-8 font-mono text-[11px] text-dim">
          <Tag tone="mint">Live</Tag> rows refresh every {TICK_MS / 1000}s while the featured run has calls out.
        </p>
      ) : null}
    </main>
  );
}
