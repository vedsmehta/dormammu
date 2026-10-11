"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/shell";
import { Button, ErrorNote, Loading, Tag } from "@/components/ui";
import { approveProposal, getProposal, rejectProposal } from "@/lib/api";
import { describeError, maskPhone } from "@/lib/format";
import { trackLabel, type Proposal, type ProposalItem, type ProposalPlan } from "@/lib/types";

const usd = (n: number) => `$${Number.isInteger(n) ? n : n.toFixed(2)}`;

function planText(p: ProposalPlan): string {
  const parts = [p.name, p.monthly_usd != null ? `${usd(p.monthly_usd)}/mo` : null, p.one_time_usd != null ? `${usd(p.one_time_usd)} one time` : null, p.term];
  return parts.filter(Boolean).join(" · ");
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const dialable = (it: ProposalItem) => Boolean(it.vendor?.phone);

/** One researched option: the vendor, what was found, what the agent will ask for, and its sources. */
function Item({ item, checked, locked, onToggle }: { item: ProposalItem; checked: boolean; locked: boolean; onToggle: () => void }) {
  const plans = [...(item.plans ?? []), ...(item.plan ? [item.plan] : [])].map(planText).filter(Boolean);
  const discounts = (item.discounts ?? []).filter((d) => d.label);
  const cites = (item.citations ?? []).filter((c) => c.url);
  const can = dialable(item);
  return (
    <label className={`opt${can ? "" : " off"}`}>
      <input type="checkbox" checked={checked} disabled={!can || locked} onChange={onToggle} />
      <span className="opt-body">
        <span className="who">
          {item.vendor?.name ?? "Unnamed vendor"}
          {item.recommended ? <Tag>Recommended</Tag> : null}
          <Tag tone="line">{item.source === "research" ? "Found online" : "Your roster"}</Tag>
          {item.vendor?.is_demo ? <Tag tone="warn">Demo line</Tag> : null}
        </span>
        <small>
          {trackLabel(item.track)} · {can ? maskPhone(item.vendor?.phone ?? "") : (item.skip_reason ?? "no verified phone, so it cannot be called")}
        </small>
        {plans.map((p) => (
          <span key={p} className="line">
            {p}
          </span>
        ))}
        {discounts.map((d) => (
          <span key={d.label} className="line t-talking">
            {d.label}
            {d.amount ? `: ${d.amount}` : ""}
            {d.eligibility ? ` (${d.eligibility})` : ""}
          </span>
        ))}
        {item.why ? <span className="line">{item.why}</span> : null}
        {item.strategy?.asks?.length ? <span className="line muted">It will ask for: {item.strategy.asks.join(", ")}.</span> : null}
        {cites.length ? (
          <span className="line muted">
            Sources:{" "}
            {cites.slice(0, 4).map((c, i) => (
              <a key={i} href={c.url ?? "#"} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                {c.title || host(c.url ?? "")}
              </a>
            ))}
          </span>
        ) : null}
      </span>
    </label>
  );
}

/**
 * The research result. Options are grouped by category with the recommended, callable ones ticked;
 * approving sends the calls and opens the run. Nothing on this screen has been dialed.
 */
export function ProposalView({ proposalId }: { proposalId: string }) {
  const router = useRouter();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProposal(proposalId)
      .then((p) => {
        if (cancelled) return;
        setProposal(p);
        const items = p.proposal?.items ?? [];
        const rec = items.filter((it) => dialable(it) && it.recommended).map((it) => it.id);
        setPicked(new Set(p.status === "proposed" ? rec : p.approved_item_ids));
      })
      .catch((e: unknown) => !cancelled && setError(describeError(e)));
    return () => {
      cancelled = true;
    };
  }, [proposalId]);

  const body = proposal?.proposal;
  const groups = useMemo(() => {
    const out = new Map<string, ProposalItem[]>();
    for (const it of body?.items ?? []) {
      const key = (it.category ?? "other").toLowerCase();
      out.set(key, [...(out.get(key) ?? []), it]);
    }
    return [...out.entries()];
  }, [body]);
  const labels = new Map((body?.categories ?? []).map((c) => [c.key, c.label ?? c.key]));
  const open = proposal?.status === "proposed";

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const approve = async () => {
    if (!proposal || busy || picked.size === 0) return;
    setBusy("approve");
    setError(null);
    try {
      const res = await approveProposal(proposal.id, [...picked]);
      router.push(`/runs/${res.run.id}`);
    } catch (e) {
      setError(describeError(e));
      setBusy(null);
    }
  };

  const reject = async () => {
    if (!proposal || busy) return;
    setBusy("reject");
    try {
      setProposal(await rejectProposal(proposal.id));
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(null);
    }
  };

  const head = (
    <div className="page-title">
      <h1 className="rise">Here&rsquo;s what I found</h1>
      <p className="rise">{proposal ? `“${proposal.request}”` : "Loading the research…"}</p>
    </div>
  );

  return (
    <AppShell head={head}>
      {!proposal && !error ? <Loading>Loading the research…</Loading> : null}
      {proposal && body ? (
        <>
          {body.summary ? <p className="lead">{body.summary}</p> : null}
          <p className="dash-note">
            {body.web_search ? `Searched the web${body.search_count ? ` (${body.search_count} searches)` : ""}` : "Web search did not run"}
            {body.model ? ` · ${body.model}` : ""}
            {body.seconds ? ` · ${Math.round(body.seconds)}s` : ""} · nothing has been called yet
          </p>
          {(body.errors ?? []).length ? (
            <p className="note warn">
              Some research steps did not finish, so this list may be shorter than it should be: {(body.errors ?? []).slice(0, 3).join("; ")}
            </p>
          ) : null}

          {groups.length === 0 ? <p>The research found no options for this request.</p> : null}
          {groups.map(([key, items]) => (
            <section key={key} className="rgroup">
              <h3>{labels.get(key) ?? key}</h3>
              <div className="opts">
                {items.map((it) => (
                  <Item key={it.id} item={it} checked={picked.has(it.id)} locked={!open || busy !== null} onToggle={() => toggle(it.id)} />
                ))}
              </div>
            </section>
          ))}

          {(body.regulations ?? []).length ? (
            <section className="rgroup">
              <h3>Rules the agent follows on these calls</h3>
              <ul className="rules">
                {(body.regulations ?? []).filter((r) => r.applies !== false).map((r, i) => (
                  <li key={i}>
                    <strong>{r.rule ?? r.topic}</strong>
                    {r.what_we_do ? <span> {r.what_we_do}</span> : r.note && r.topic ? <span> {r.note}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          <div className="savebar">
            {open ? (
              <>
                <Button onClick={approve} disabled={busy !== null || picked.size === 0}>
                  {busy === "approve" ? "Placing calls…" : picked.size === 0 ? "Pick who to call" : `Call ${picked.size} to confirm and negotiate`}
                </Button>
                <Button variant="line" size="sm" onClick={reject} disabled={busy !== null}>
                  Not now
                </Button>
              </>
            ) : proposal.run_id ? (
              <Link className="btn btn-primary" href={`/runs/${proposal.run_id}`}>
                See the calls
              </Link>
            ) : (
              <span className="saved-at">This research was {proposal.status}. Nothing was called.</span>
            )}
            <ErrorNote message={error} />
          </div>
        </>
      ) : (
        <ErrorNote message={error} />
      )}
    </AppShell>
  );
}
