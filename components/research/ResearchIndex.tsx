"use client";

import Link from "next/link";
import { useEffect, useState, type CSSProperties } from "react";

import { RequestForm } from "@/components/home/RequestForm";
import { statusDot, statusTextTone } from "@/components/research/support";
import { AppShell } from "@/components/shell";
import { Dot, ErrorNote, Loading } from "@/components/ui";
import { getProposals } from "@/lib/api";
import { describeError, fmtStamp, shortId } from "@/lib/format";
import { proposalStatusWord, type ProposalListItem } from "@/lib/types";

const LIMIT = 20;

const d = (s: string) => ({ "--d": s }) as CSSProperties;

/** One .crow per proposal: status dot, the request, where it stands, when it was made. */
function ProposalLine({ p }: { p: ProposalListItem }) {
  const approved = p.approved_item_ids?.length ?? 0;
  return (
    <Link href={`/research/${encodeURIComponent(p.id)}`} className="crow">
      <Dot state={statusDot(p.status)} />
      <span className="who">
        {p.request}
        <small>
          {p.n_items} {p.n_items === 1 ? "item" : "items"}
          {approved > 0 ? ` · ${approved} approved` : ""}
        </small>
      </span>
      <span className="what">
        <span className={statusTextTone(p.status)}>{proposalStatusWord(p.status)}</span>
        {p.run_id ? <small>Run …{shortId(p.run_id)}</small> : null}
      </span>
      <span className="meta num">
        {fmtStamp(p.created_at)}
        <small>Created</small>
      </span>
      <span className="chev" aria-hidden="true">
        →
      </span>
    </Link>
  );
}

/** /research: the user's proposals and a box to start a new one (the same POST /research the dashboard uses). */
export function ResearchIndex() {
  const [rows, setRows] = useState<ProposalListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProposals(LIMIT)
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

  const head = (
    <div className="dash-top">
      <h1 className="rise" style={d(".1s")}>
        Research
      </h1>
      <p className="rise" style={d(".15s")}>
        Dormammu finds who to call, what they charge, what you can claim and what the rules are, then waits for your yes. Nothing is dialed
        until you approve a proposal.
      </p>
      <RequestForm variant="research" autoFocus={false} />
    </div>
  );

  return (
    <AppShell head={head}>
      <section className="sec" aria-labelledby="r1">
        <h2 id="r1">Your proposals</h2>
        <p>Newest first. Open one to read the findings and approve the calls.</p>
        <ErrorNote message={error} />
        {rows === null && !error ? <Loading>Loading proposals…</Loading> : null}
        {rows !== null && rows.length === 0 ? (
          <p className="request-empty mt-5">No proposals yet. Type a request above; research runs before anything is called.</p>
        ) : null}
        {rows !== null && rows.length > 0 ? (
          <div className="mt-5">
            {rows.map((p) => (
              <ProposalLine key={p.id} p={p} />
            ))}
          </div>
        ) : null}
        {rows !== null && rows.length >= LIMIT ? <p className="dash-note">The API lists the {LIMIT} most recent proposals.</p> : null}
      </section>
    </AppShell>
  );
}
