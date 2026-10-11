"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { callHref, summarizeOutcome } from "@/components/calls/summary";
import { RequestForm } from "@/components/home/RequestForm";
import { RunList } from "@/components/home/RunList";
import { AppShell } from "@/components/shell";
import { Dot, ErrorNote, Loading, Panel, PanelTitle, Tag, Wave } from "@/components/ui";
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

const d = (s: string) => ({ "--d": s }) as CSSProperties;

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

/** The .panel.stat tiles across the top of the board. The first goes dark (.hot) while calls are live. */
function StatTile({
  label,
  value,
  unit,
  note,
  href,
  hot,
  live,
  index,
}: {
  label: string;
  value: string;
  unit?: string;
  note: string;
  href: string;
  hot?: boolean;
  live?: boolean;
  index: number;
}) {
  return (
    <Link href={href} className={hot ? "panel stat hot" : "panel stat"} style={{ "--i": index } as CSSProperties}>
      <span className="lbl">
        {live ? <span className="dot talking" aria-hidden="true" /> : null}
        {label}
      </span>
      <b className="num">
        {value}
        {unit ? <i>{unit}</i> : null}
      </b>
      <small>{note}</small>
    </Link>
  );
}

/** One .mini row: dot, vendor, state or sub, result. */
function MiniRow({ call }: { call: CallWithRun }) {
  const s = summarizeOutcome(call);
  const live = !isTerminal(call.state);
  return (
    <Link href={callHref(call)} className="mini">
      <Dot state={call.state} />
      <span className="who">{call.target_name}</span>
      <span className="st">
        {live ? (
          <>
            <span className={s.tone === "ok" ? "t-talking" : s.tone === "warn" ? "t-ringing" : s.tone === "muted" ? "t-muted" : ""}>{s.main}</span>
            {call.state === "talking" ? <Wave /> : null}
          </>
        ) : (
          <>
            {trackLabel(call.track)}
            {s.sub ? ` · ${s.sub}` : ""}
          </>
        )}
      </span>
      <span className={live ? "res" : s.tone === "warn" || s.tone === "bad" ? "res t-warn" : "res"}>{live ? "" : s.main}</span>
    </Link>
  );
}

function LedgerLine({ row, due, index }: { row: LedgerRow; due: boolean; index: number }) {
  return (
    <li className={due ? "next" : undefined} style={{ "--i": index } as CSSProperties}>
      <time className={due ? "due" : undefined}>
        {row.expires_on ? `${due ? "Due · " : ""}${fmtDay(row.expires_on)}` : row.effective_on ? `From ${fmtDay(row.effective_on)}` : "Undated"}
      </time>
      <b>{row.label}</b>
      <p>
        {row.kind.replace("_", " ")}
        {row.amount_usd !== null ? ` · ${fmtUsd(row.amount_usd)}` : ""}
      </p>
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

  const head = (
    <div className="dash-top">
      <h1 className="rise" style={d(".1s")}>
        {greeting}, {name}
      </h1>
      <p className="rise" style={d(".15s")}>
        {sub}
      </p>
      <RequestForm />
      <ErrorNote message={error} />
    </div>
  );

  return (
    <AppShell head={head} wide>
      <div className="board">
        <div className="stats">
          <StatTile
            index={0}
            label="Running now"
            value={loaded ? String(liveCalls.length) : "…"}
            note={liveCalls.length ? "calls at the same time" : "no live calls"}
            href={featured ? `/runs/${featured.id}` : "/calls"}
            hot={liveCalls.length > 0}
            live={liveCalls.length > 0}
          />
          <StatTile
            index={1}
            label="Needs you"
            value={ledger ? String(due.length) : "…"}
            note={due.length ? "ledger rows due now" : "nothing due in the ledger"}
            href="/ledger"
          />
          <StatTile index={2} label="Calls made" value={loaded ? String(allCalls.length) : "…"} note={scope || "loading"} href="/calls" />
          <StatTile
            index={3}
            label="On the phone for you"
            value={loaded ? fmtDurationWords(talkSeconds) : "…"}
            note={loaded ? `${doneCalls.length} finished ${doneCalls.length === 1 ? "call" : "calls"}, ${scope}` : "loading"}
            href="/calls"
          />
        </div>

        <Panel className="span2" index={4} labelledBy="p-run">
          <PanelTitle id="p-run" href={featured ? `/runs/${featured.id}` : undefined} linkText="Open the run">
            Running now
          </PanelTitle>
          {assembled === null ? (
            <Loading />
          ) : featured ? (
            <>
              <div className="runline">
                <q>{featured.request}</q>
                <span className="clock num">{fmtDuration(wallSeconds(featured))}</span>
              </div>
              <p>
                {featured.calls.filter((c) => !isTerminal(c.state)).length} of {featured.calls.length} still going
                {featured.aggregate && featured.aggregate.n_simulated > 0 ? (
                  <Tag tone="warn">{featured.aggregate.n_simulated} simulated</Tag>
                ) : null}
              </p>
              <div className="list">
                {featured.calls.map((c) => (
                  <MiniRow key={c.id} call={{ ...c, request: featured.request }} />
                ))}
              </div>
            </>
          ) : (
            <p>No run is live. Start one above and it shows up here while the calls are out.</p>
          )}
        </Panel>

        <div className="stack">
          <Panel index={5} id="needs" labelledBy="p-needs">
            <PanelTitle id="p-needs" href="/ledger" linkText="Ledger">
              Needs you
            </PanelTitle>
            {ledger === null ? (
              <Loading />
            ) : due.length === 0 ? (
              <p>Nothing needs your yes right now.</p>
            ) : (
              <>
                <ol className="rail">
                  {due.slice(0, 3).map((r, i) => (
                    <LedgerLine key={r.id} row={r} due index={i} />
                  ))}
                </ol>
                <div className="acts">
                  <Link href="/ledger" className="btn btn-primary btn-sm">
                    Review and fire the due rows
                  </Link>
                </div>
              </>
            )}
          </Panel>
          <Panel index={6} labelledBy="p-next">
            <PanelTitle id="p-next" href="/ledger" linkText="Ledger">
              Coming up
            </PanelTitle>
            {ledger === null ? (
              <Loading />
            ) : upcoming.length === 0 ? (
              <p>Nothing dated yet. A quote, a promo, or an appointment lands here.</p>
            ) : (
              <ol className="rail">
                {upcoming.map((r, i) => (
                  <LedgerLine key={r.id} row={r} due={false} index={i} />
                ))}
              </ol>
            )}
          </Panel>
        </div>

        <Panel className="span2" index={7} labelledBy="p-recent">
          <PanelTitle id="p-recent" href="/calls" linkText="All calls">
            Recent calls
          </PanelTitle>
          {assembled === null ? (
            <Loading />
          ) : recentCalls.length === 0 ? (
            <p>No calls yet.</p>
          ) : (
            <div className="list">
              {recentCalls.map((c) => (
                <Link key={c.id} href={callHref(c)} className="mini">
                  <Dot state={c.state} />
                  <span className="who">{c.target_name}</span>
                  <span className="st">{summarizeOutcome(c).main}</span>
                  <span className="res num">{fmtDayFromTs(c.started_at)}</span>
                </Link>
              ))}
            </div>
          )}
        </Panel>

        <Panel index={8} labelledBy="p-out">
          <PanelTitle id="p-out">How calls ended</PanelTitle>
          {assembled === null ? (
            <Loading />
          ) : allCalls.length === 0 ? (
            <p>No calls to count yet.</p>
          ) : (
            <>
              <div
                className="bar"
                role="img"
                aria-label={`Of ${allCalls.length} calls: ${doneCalls.length} got an answer, ${liveCalls.length} still going, ${failedCalls.length} failed`}
              >
                {doneCalls.length ? <i style={{ flex: doneCalls.length, background: "var(--deep)" }} /> : null}
                {liveCalls.length ? <i style={{ flex: liveCalls.length, background: "var(--mint)" }} /> : null}
                {failedCalls.length ? <i style={{ flex: failedCalls.length, background: "#f2b84b" }} /> : null}
              </div>
              <ul className="legend">
                {[
                  ["Got an answer", doneCalls.length, "var(--deep)"],
                  ["Still going", liveCalls.length, "var(--mint)"],
                  ["Failed", failedCalls.length, "#f2b84b"],
                ].map(([label, n, color]) => (
                  <li key={String(label)}>
                    <span>
                      <i style={{ background: String(color) }} />
                      {label}
                    </span>
                    <b className="num">{n}</b>
                  </li>
                ))}
              </ul>
              <p className="dash-note">Counted {scope}.</p>
            </>
          )}
        </Panel>

        <Panel className="span3" index={9} labelledBy="p-runs">
          <PanelTitle id="p-runs" href="/onboarding" linkText="Edit roster">
            Recent requests
          </PanelTitle>
          <div className="list">
            <RunList runs={runs} />
          </div>
          {runs && runs.length >= 20 ? <p className="dash-note">The API lists the 20 most recent runs.</p> : null}
        </Panel>
      </div>

      {featured ? (
        <p className="dash-note">
          <Tag first>Live</Tag> rows refresh every {TICK_MS / 1000}s while the featured run has calls out.
        </p>
      ) : null}
    </AppShell>
  );
}
