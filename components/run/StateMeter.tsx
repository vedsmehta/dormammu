import type { Call, CallState } from "@/lib/types";

// Dots filled per state. planned..done maps onto five dots; failed shows all five in red.
const FILL: Record<CallState, number> = {
  planned: 0,
  dialing: 1,
  ringing: 2,
  talking: 3,
  extracting: 4,
  done: 5,
  failed: 5,
};

const DOT_TONE: Record<CallState, string> = {
  planned: "bg-dim",
  dialing: "bg-accent",
  ringing: "bg-warn",
  talking: "bg-ok",
  extracting: "bg-violet",
  done: "bg-ok",
  failed: "bg-bad/60",
};

export function StateMeter({ state }: { state: CallState }) {
  const filled = FILL[state] ?? 0;
  const tone = DOT_TONE[state] ?? "bg-dim";
  return (
    <div className="flex items-center gap-1" aria-label={`state ${state}`}>
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          className={`h-2 w-2 rounded-full transition-colors duration-300 ${
            i < filled ? tone : "border border-line bg-transparent"
          }`}
        />
      ))}
    </div>
  );
}

/** The state word. ringing pulses amber; talking snaps in as a solid green pill. */
export function StateWord({ call }: { call: Call }) {
  switch (call.state) {
    case "planned":
      return <span className="text-dim">planned</span>;
    case "dialing":
      return (
        <span className="text-accent">
          dialing<span className="animate-blink">…</span>
        </span>
      );
    case "ringing":
      return (
        <span className="inline-flex items-center gap-2 text-warn">
          <span className="h-2.5 w-2.5 rounded-full bg-warn animate-ring-pulse" />
          ringing
        </span>
      );
    case "talking":
      return (
        <span
          key="talking"
          className="animate-pop inline-flex items-center gap-1.5 rounded bg-ok px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-black"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-black/70" />
          talking
        </span>
      );
    case "extracting":
      return (
        <span className="text-violet">
          extracting<span className="animate-blink">…</span>
        </span>
      );
    case "done":
      return <span className="text-ok">done ✓</span>;
    case "failed":
      return (
        <span className="text-bad/80">
          failed
          <span className="ml-1 font-mono text-xs text-bad/60">{call.termination_type ?? call.error ?? ""}</span>
        </span>
      );
    default:
      return <span className="text-dim">{String(call.state)}</span>;
  }
}
