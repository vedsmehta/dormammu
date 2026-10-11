import Link from "next/link";

import { StatusChip } from "@/components/ui";
import { fmtStamp } from "@/lib/format";
import type { RunListItem } from "@/lib/types";

function callCount(run: RunListItem): string {
  if (typeof run.n_calls === "number") return String(run.n_calls);
  if (Array.isArray(run.calls)) return String(run.calls.length);
  return "·";
}

export function RunList({ runs }: { runs: RunListItem[] | null }) {
  if (runs === null) return <p className="font-mono text-xs text-dim">loading…</p>;
  if (runs.length === 0) return <p className="text-sm text-muted">No runs yet. Type a request above.</p>;

  return (
    <ul className="divide-y divide-line rounded-lg border border-line bg-panel">
      {runs.map((run) => (
        <li key={run.id}>
          <Link
            href={`/runs/${run.id}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-panel-2 sm:grid-cols-[minmax(0,1fr)_6rem_9rem_4rem]"
          >
            <span className="truncate text-sm">{run.request}</span>
            <span className="sm:justify-self-start">
              <StatusChip status={run.status} />
            </span>
            <span className="tnum font-mono text-xs text-muted">{fmtStamp(run.created_at)}</span>
            <span className="tnum text-right font-mono text-xs text-muted">{callCount(run)} calls</span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
