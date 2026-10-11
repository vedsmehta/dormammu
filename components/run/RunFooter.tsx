import { fmtDurationWords } from "@/lib/format";
import type { AggregateView } from "@/lib/types";

/** The figures under the board, in the finished run's .est paragraph style. */
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
  const assumption = `Assumes ${overhead ?? "?"} s of dial overhead per sequential call`;

  return (
    <p className="est">
      <strong className="num">{fmtDurationWords(wallSeconds)} wall clock</strong> · <span className="num">{fmtDurationWords(a?.talk_seconds)} talking</span> ·{" "}
      <span className={mult !== null ? "num t-talking" : "num t-muted"} title={assumption}>
        {mult !== null ? `${mult.toFixed(1)}× faster (est.)` : active ? "speedup pending (est.)" : "no estimate"}
      </span>{" "}
      ·{" "}
      <span className={a && a.n_simulated > 0 ? "num t-warn" : "num"}>
        {a?.n_simulated ?? 0} of {a?.n_calls ?? nCalls} simulated
      </span>
      <br />
      {assumption}
      {seq !== null ? `. Sequential estimate ${fmtDurationWords(seq)}` : ""}
      {a ? `. ${a.n_completed} completed, ${a.n_failed} failed, ${a.n_live} live.` : "."}
    </p>
  );
}
