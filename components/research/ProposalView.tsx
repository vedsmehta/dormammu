"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";

import { ItemCard } from "@/components/research/ItemCard";
import { RegulationsCard } from "@/components/research/RegulationsCard";
import { apiDetail, callable, defaultChecked, statusTone, vendorOf } from "@/components/research/support";
import { AppShell } from "@/components/shell";
import { Button, ErrorNote, Loading, Panel, PanelTitle, Tag } from "@/components/ui";
import { approveProposal, createRun, getProfile, getProposal, rejectProposal } from "@/lib/api";
import { fmtDay, fmtStamp, fmtStampFull, fmtUsd, shortId } from "@/lib/format";
import {
  DEFAULT_CALLER_NAME,
  proposalStatusWord,
  trackLabel,
  type Proposal,
  type ProposalApproveResult,
  type ProposalBody,
  type ProposalItem,
} from "@/lib/types";

// After approval the result (skipped items, the simulated-rows notice) stays on screen this long
// before the page moves to the run. "Open the run now" skips the wait.
const ROUTE_DELAY_S = 8;

const d = (s: string) => ({ "--d": s }) as CSSProperties;

type Group = {
  key: string;
  label: string;
  track: string | null;
  why: string | null;
  source: string | null;
  query: string | null;
  items: ProposalItem[];
};

/** Items in the proposal's category order; a category the engine searched but filled with nothing still shows. */
function groupItems(body: ProposalBody): Group[] {
  const groups = new Map<string, Group>();
  for (const c of body.categories ?? []) {
    groups.set(c.key, { key: c.key, label: c.label || c.key, track: c.track ?? null, why: c.why ?? null, source: c.source ?? null, query: c.query ?? null, items: [] });
  }
  for (const it of body.items ?? []) {
    const key = it.category || "other";
    let g = groups.get(key);
    if (!g) {
      g = { key, label: key, track: it.track ?? null, why: null, source: null, query: null, items: [] };
      groups.set(key, g);
    }
    g.items.push(it);
  }
  return [...groups.values()];
}

/** The parsed request details that are present, as label and value pairs. */
function detailChips(body: ProposalBody): [string, string][] {
  const det = body.details ?? {};
  const out: [string, string][] = [];
  if (det.destination) out.push(["to", String(det.destination)]);
  if (det.origin) out.push(["from", String(det.origin)]);
  if (det.date) out.push(["on", String(det.date)]);
  else if (det.move_date) out.push(["on", fmtDay(String(det.move_date))]);
  if (det.bedrooms) out.push(["size", String(det.bedrooms)]);
  if (det.budget) out.push(["budget", String(det.budget)]);
  else if (typeof det.budget_ceiling_usd === "number") out.push(["budget", fmtUsd(det.budget_ceiling_usd)]);
  return out;
}

/**
 * /research/[proposalId]: the proposal the engine wrote, for the user to approve item by item.
 * Regulations first, then the items grouped by category, then the footer that turns the checked items
 * into a run. Every value on the page is read from the proposals row; the agent's two fixed opening
 * lines come from the profile (caller name, display name) the way router/instructions.py builds them.
 */
export function ProposalView({ proposalId }: { proposalId: string }) {
  const router = useRouter();
  const [row, setRow] = useState<Proposal | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [caller, setCaller] = useState<string>(DEFAULT_CALLER_NAME);
  const [client, setClient] = useState<string>("a client");
  const [checked, setChecked] = useState<string[]>([]);
  const [busy, setBusy] = useState<null | "approve" | "reject" | "roster" | "reload">(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [result, setResult] = useState<ProposalApproveResult | null>(null);
  const [countdown, setCountdown] = useState<number>(ROUTE_DELAY_S);

  const load = useCallback(
    (cancelledRef?: { current: boolean }) =>
      Promise.allSettled([getProposal(proposalId), getProfile()]).then(([p, prof]) => {
        if (cancelledRef?.current) return;
        if (p.status === "fulfilled") {
          setRow(p.value);
          setChecked((p.value.proposal?.items ?? []).filter(defaultChecked).map((it) => it.id));
          setLoadError(null);
        } else {
          setLoadError(apiDetail(p.reason));
        }
        if (prof.status === "fulfilled") {
          const name = prof.value.prefs?.caller_name;
          setCaller(typeof name === "string" && name.trim() ? name.trim() : DEFAULT_CALLER_NAME);
          const who = prof.value.display_name;
          setClient(typeof who === "string" && who.trim() ? who.trim() : "a client");
        }
        setBusy(null);
      }),
    [proposalId],
  );

  useEffect(() => {
    const cancelled = { current: false };
    void load(cancelled);
    return () => {
      cancelled.current = true;
    };
  }, [load]);

  // Once approved, hold the result on screen for ROUTE_DELAY_S, then open the run.
  useEffect(() => {
    if (!result) return;
    const runId = result.run.id;
    const id = window.setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          window.clearInterval(id);
          router.push(`/runs/${runId}`);
          return 0;
        }
        return c - 1;
      });
    }, 1000);
    return () => window.clearInterval(id);
  }, [result, router]);

  const body = row?.proposal ?? null;
  const groups = useMemo(() => (body ? groupItems(body) : []), [body]);
  const items = useMemo(() => body?.items ?? [], [body]);
  const byId = useMemo(() => new Map(items.map((it) => [it.id, it])), [items]);
  const approvedIds = new Set(row?.approved_item_ids ?? []);
  const status = row?.status ?? null;
  const selecting = status === "proposed" && !result;
  const callableCount = items.filter(callable).length;
  const uncallable = items.length - callableCount;

  const toggle = (id: string, next: boolean) =>
    setChecked((prev) => (next ? (prev.includes(id) ? prev : [...prev, id]) : prev.filter((x) => x !== id)));

  const approve = async () => {
    if (!row || busy || checked.length === 0) return;
    setBusy("approve");
    setActionError(null);
    try {
      const res = await approveProposal(row.id, checked);
      setResult(res);
      setCountdown(ROUTE_DELAY_S);
      setRow(res.proposal ?? row);
      setBusy(null);
    } catch (err) {
      setActionError(apiDetail(err));
      setBusy(null);
    }
  };

  const reject = async () => {
    if (!row || busy) return;
    setBusy("reject");
    setActionError(null);
    try {
      await rejectProposal(row.id);
      router.push("/");
    } catch (err) {
      setActionError(apiDetail(err));
      setBusy(null);
    }
  };

  const rosterInstead = async () => {
    if (!row || busy) return;
    setBusy("roster");
    setActionError(null);
    try {
      const run = await createRun(row.request);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setActionError(apiDetail(err));
      setBusy(null);
    }
  };

  const chips = body ? detailChips(body) : [];
  const errors = body?.errors ?? [];
  const nameOf = (id: string) => {
    const it = byId.get(id);
    return it ? vendorOf(it).name : id;
  };

  const head = (
    <div className="page-title">
      <Link className="back rise" href="/research">
        ← Research
      </Link>
      {row ? (
        <>
          <h1 className="rise text-[clamp(28px,3.8vw,50px)]" style={d(".1s")}>
            <q>{row.request}</q>
          </h1>
          {chips.length > 0 ? (
            <div className="meta rise mt-4" style={d(".15s")}>
              {chips.map(([k, v], i) => (
                <Tag key={k} tone="line" first={i === 0}>
                  <span className="opacity-70">{k}</span> {v}
                </Tag>
              ))}
            </div>
          ) : null}
          {body?.summary ? (
            <p className="rise" style={d(".2s")}>
              {body.summary}
            </p>
          ) : null}
          <div className="meta rise mt-4 text-[14px] text-white/75" style={d(".25s")}>
            <Tag tone={statusTone(row.status)} first>
              {proposalStatusWord(row.status)}
            </Tag>
            {body?.web_search ? <Tag>live web research</Tag> : <Tag tone="warn">model knowledge only, no citations</Tag>}
            {body ? (
              <span className="num">
                {body.search_count ?? 0} {body.search_count === 1 ? "search" : "searches"}
              </span>
            ) : null}
            {body?.generated_at ? <span>· generated {fmtStampFull(body.generated_at)}</span> : null}
            {typeof body?.seconds === "number" ? <span className="num">· {body.seconds}s</span> : null}
            {body?.model ? <span>· {body.model}</span> : null}
          </div>
        </>
      ) : (
        <h1 className="rise text-[clamp(28px,3.8vw,50px)]" style={d(".1s")}>
          {loadError ? "Proposal" : "Loading proposal…"}
        </h1>
      )}
    </div>
  );

  return (
    <AppShell head={head}>
      <ErrorNote message={loadError} />
      {row === null && !loadError ? <Loading>Loading proposal…</Loading> : null}

      {row && body ? (
        <>
          {status === "failed" ? (
            <div className="note bad" role="alert">
              <p>
                <b>Research failed.</b> {errors.length ? errors.join(" ") : "The engine returned no items."} Nothing was called.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-3">
                <button type="button" className="btn btn-line btn-sm" disabled={busy !== null} onClick={rosterInstead}>
                  {busy === "roster" ? "Dispatching…" : "Call my roster now instead"}
                </button>
                <span className="text-[14px] opacity-80">Same request, no research: the vendors on your roster are called directly.</span>
              </div>
            </div>
          ) : null}

          {status === "researching" || status === "approved" ? (
            <p className="note" role="status">
              {status === "researching" ? "Still researching." : "Approved; the run is being created."}{" "}
              <button
                type="button"
                className="underline underline-offset-4"
                disabled={busy !== null}
                onClick={() => {
                  setBusy("reload");
                  void load();
                }}
              >
                {busy === "reload" ? "Refreshing…" : "Refresh"}
              </button>
            </p>
          ) : null}

          {status !== "failed" && errors.length > 0 ? (
            <details className="note warn">
              <summary className="tog">
                Research reported {errors.length} {errors.length === 1 ? "issue" : "issues"}; the proposal holds what finished.
              </summary>
              <ul className="mt-2 grid gap-1 text-[14px]">
                {errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </details>
          ) : null}

          <section className="sec" aria-labelledby="s-regs">
            <h2 id="s-regs">Regulations</h2>
            <p>Checked for the destination state before any call. Each rule says what the agent does about it.</p>
            <div className="mt-5">
              <RegulationsCard regulations={body.regulations ?? []} state={body.details?.state ?? null} />
            </div>
          </section>

          {groups.map((g) => (
            <section key={g.key} className="sec" aria-labelledby={`s-${g.key}`}>
              <h2 id={`s-${g.key}`}>
                {g.label}
                {g.track ? <Tag tone="line">{trackLabel(g.track)}</Tag> : null}
                {g.source === "profile" ? <Tag tone="line">from your profile</Tag> : null}
                {g.source === "roster" ? <Tag tone="line">from your roster</Tag> : null}
              </h2>
              {g.why ? <p>{g.why}</p> : null}
              {g.items.length === 0 ? (
                <p className="request-empty mt-5">
                  The search returned no vendors for this category.
                  {g.query ? (
                    <>
                      {" "}
                      Searched: <q>{g.query}</q>.
                    </>
                  ) : null}
                </p>
              ) : (
                <div className="mt-5 grid gap-5">
                  {g.items.map((it, i) => (
                    <ItemCard
                      key={it.id}
                      item={it}
                      index={i + 1}
                      mode={selecting ? "select" : "locked"}
                      checked={checked.includes(it.id)}
                      approved={approvedIds.has(it.id)}
                      onToggle={toggle}
                      caller={caller}
                      client={client}
                    />
                  ))}
                </div>
              )}
            </section>
          ))}

          {items.length === 0 && status !== "failed" ? (
            <p className="dash-note">The proposal has no items. Nothing can be approved from it.</p>
          ) : null}

          {selecting ? (
            <div className="savebar">
              <Button onClick={approve} disabled={busy !== null || checked.length === 0}>
                {busy === "approve" ? "Approving…" : `Approve and call ${checked.length}`}
              </Button>
              <Button variant="line" onClick={reject} disabled={busy !== null}>
                {busy === "reject" ? "Rejecting…" : "Reject"}
              </Button>
              <span className="text-[14px] text-ink-2">
                {checked.length} of {callableCount} callable {callableCount === 1 ? "item" : "items"} checked
                {uncallable > 0 ? `; ${uncallable} without a verified phone cannot be called` : ""}.
              </span>
              <ErrorNote message={actionError} />
            </div>
          ) : null}

          {result ? (
            <Panel className="mt-8" index={1} labelledBy="p-approved">
              <PanelTitle id="p-approved" href={`/runs/${result.run.id}`} linkText="Open the run">
                Approved
              </PanelTitle>
              <p>
                {result.run.calls.length} {result.run.calls.length === 1 ? "call" : "calls"} on run …{shortId(result.run.id)}. Opening it in{" "}
                <span className="num">{countdown}s</span>.
              </p>
              {!result.external_calls_enabled ? (
                <p className="note warn" role="status">
                  External calling is disabled in this environment. The approved outside numbers run as labeled simulated rows on the run page,
                  not as live calls
                  {result.run.aggregate ? ` (${result.run.aggregate.n_simulated} of ${result.run.aggregate.n_calls} simulated)` : ""}.
                </p>
              ) : null}
              {result.skipped.length > 0 ? (
                <>
                  <h3 className="mt-5 text-[12px] uppercase tracking-[0.08em] text-ink-3">Skipped</h3>
                  <ul className="mt-1">
                    {result.skipped.map((s) => (
                      <li key={s.item_id} className="border-t border-line py-2 text-[15px] first:border-t-0">
                        <b className="font-display font-semibold">{nameOf(s.item_id)}</b> <span className="text-ink-2">· {s.reason}</span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : null}
              <div className="acts">
                <Link className="btn btn-primary btn-sm" href={`/runs/${result.run.id}`}>
                  Open the run now
                </Link>
              </div>
            </Panel>
          ) : null}

          {status === "dispatched" && !result ? (
            <Panel className="mt-8" index={1} labelledBy="p-dispatched">
              <PanelTitle id="p-dispatched" href={row.run_id ? `/runs/${row.run_id}` : undefined} linkText="Open the run">
                Calls placed
              </PanelTitle>
              <p>
                Approved {fmtStamp(row.updated_at)}: {approvedIds.size} {approvedIds.size === 1 ? "item" : "items"} became calls
                {row.run_id ? ` on run …${shortId(row.run_id)}` : ""}.
              </p>
              {row.run_id ? (
                <div className="acts">
                  <Link className="btn btn-primary btn-sm" href={`/runs/${row.run_id}`}>
                    Open the run
                  </Link>
                </div>
              ) : null}
            </Panel>
          ) : null}

          {status === "rejected" ? (
            <p className="note mt-8" role="status">
              This proposal was rejected {fmtStamp(row.updated_at)}. Nothing was called.{" "}
              <Link className="underline underline-offset-4" href="/research">
                Start another
              </Link>
              .
            </p>
          ) : null}

          {!selecting && actionError ? <ErrorNote message={actionError} /> : null}
        </>
      ) : null}
    </AppShell>
  );
}
