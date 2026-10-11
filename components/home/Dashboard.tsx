"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { LedgerTimeline } from "@/components/home/LedgerTimeline";
import { RequestForm } from "@/components/home/RequestForm";
import { RunList } from "@/components/home/RunList";
import { Brand, ErrorNote } from "@/components/ui";
import { UserChip } from "@/components/UserChip";
import { ApiError, getLedger, getRuns } from "@/lib/api";
import type { LedgerRow, RunListItem } from "@/lib/types";

function describe(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "Not authorized by the API (401). Sign in again or set DEV_USER_ID on the Python side.";
    return e.message;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

export function Dashboard({ email }: { email: string | null }) {
  const [runs, setRuns] = useState<RunListItem[] | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getRuns(), getLedger()]).then(([r, l]) => {
      if (cancelled) return;
      const problems: string[] = [];
      if (r.status === "fulfilled") setRuns(r.value);
      else problems.push(`runs: ${describe(r.reason)}`);
      if (l.status === "fulfilled") setLedger(l.value);
      else problems.push(`ledger: ${describe(l.reason)}`);
      setError(problems.length ? problems.join(" · ") : null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 sm:px-6">
      <header className="mb-10 flex items-center justify-between gap-4">
        <Brand />
        <div className="flex items-center gap-4">
          <Link href="/onboarding" className="font-mono text-xs text-muted hover:text-fg">
            edit roster
          </Link>
          <UserChip email={email} />
        </div>
      </header>

      <RequestForm />

      <div className="mt-4">
        <ErrorNote message={error} />
      </div>

      <section className="mt-12">
        <div className="mb-3 flex items-baseline justify-between">
          <h2 className="font-mono text-xs uppercase tracking-widest text-muted">Runs</h2>
          <Link href="/onboarding" className="text-xs text-dim hover:text-fg">
            roster →
          </Link>
        </div>
        <RunList runs={runs} />
      </section>

      <section className="mt-12">
        <h2 className="mb-3 font-mono text-xs uppercase tracking-widest text-muted">Ledger</h2>
        <LedgerTimeline rows={ledger} />
      </section>
    </main>
  );
}
