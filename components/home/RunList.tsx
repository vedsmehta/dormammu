import Link from "next/link";

import { Dot } from "@/components/ui";
import { fmtStamp, tidy } from "@/lib/format";
import type { RunListItem } from "@/lib/types";

function callCount(run: RunListItem): string {
  if (typeof run.n_calls === "number") return `${run.n_calls} ${run.n_calls === 1 ? "call" : "calls"}`;
  if (Array.isArray(run.calls)) return `${run.calls.length} ${run.calls.length === 1 ? "call" : "calls"}`;
  return "·";
}

// A run's status in the dot vocabulary: live runs pulse, failed ones take the amber warn dot.
function dotState(status: RunListItem["status"]): string {
  switch (status) {
    case "running":
      return "talking";
    case "dispatching":
      return "ringing";
    case "failed":
      return "failed";
    default:
      return "done";
  }
}

const STATUS_WORD: Record<RunListItem["status"], string> = {
  dispatching: "Dispatching",
  running: "Running",
  complete: "Complete",
  failed: "Failed",
};

/** Recent requests as .mini rows, like the dashboard's "Recent calls" list. */
export function RunList({ runs }: { runs: RunListItem[] | null }) {
  if (runs === null) return <p className="dash-note">Loading…</p>;
  if (runs.length === 0) return <p>No runs yet. Type a request above.</p>;

  return (
    <div className="list">
      {runs.map((run) => (
        <Link key={run.id} href={`/runs/${run.id}`} className="mini">
          <Dot state={dotState(run.status)} />
          <span className="who" title={tidy(run.request)}>
            {tidy(run.request)}
          </span>
          <span className="st">
            {STATUS_WORD[run.status] ?? run.status} · {callCount(run)}
          </span>
          <span className="res num">{fmtStamp(run.created_at)}</span>
        </Link>
      ))}
    </div>
  );
}
