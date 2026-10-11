import { fmtDurationWords } from "@/lib/format";
import type { AggregateView } from "@/lib/types";

export function RunFooter({
  aggregate,
  wallSeconds,
  active,
  nCalls,
}: {
  aggregate: AggregateView | null;
  wallSeconds: number | null;
  active: boolean;
  nCalls: number;
}) {
  const a = aggregate;
  const mult = a?.multiplier ?? null;
  const overhead = a?.dial_overhead_seconds;
  const seq = a?.sequential_estimate_seconds ?? null;
  const assumption = `assumes ${overhead ?? "?"} s dial overhead per sequential call`;

  return (
    <footer className="border-t border-line bg-panel-2 px-4 py-3">
      <div className="tnum flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-sm">
        <span className="text-fg">{fmtDurationWords(wallSeconds)} wall clock</span>
        <span className="text-dim">·</span>
        <span className="text-fg">{fmtDurationWords(a?.talk_seconds)} talking</span>
        <span className="text-dim">·</span>
        <span className={mult !== null ? "text-ok" : "text-dim"} title={assumption}>
          {mult !== null ? `${mult.toFixed(1)}× faster (est.)` : active ? "speedup pending (est.)" : "no estimate"}
        </span>
        <span className="text-dim">·</span>
        <span className={a && a.n_simulated > 0 ? "text-warn" : "text-dim"}>
          {a?.n_simulated ?? 0} of {a?.n_calls ?? nCalls} simulated
        </span>
      </div>
      <p className="mt-1 font-mono text-[11px] text-dim">
        {assumption}
        {seq !== null ? ` · sequential estimate ${fmtDurationWords(seq)}` : ""}
        {a ? ` · ${a.n_completed} completed, ${a.n_failed} failed, ${a.n_live} live` : ""}
      </p>
    </footer>
  );
}
