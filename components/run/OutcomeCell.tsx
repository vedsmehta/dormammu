import { Chip } from "@/components/ui";
import { EM, fmtDay, fmtSlot, fmtUsd } from "@/lib/format";
import type { Call } from "@/lib/types";

const MAX_CALL_SECONDS = 180;

/** The ▓▓▓▓░░ bar from the §4.11 mock: how far a live call is through its 180s budget. */
function TalkBar({ elapsed }: { elapsed: number | null }) {
  const pct = Math.min(100, Math.max(0, ((elapsed ?? 0) / MAX_CALL_SECONDS) * 100));
  const lit = Math.round((pct / 100) * 10);
  return (
    <div className="flex items-center gap-2" aria-label="talking">
      <div className="flex gap-0.5">
        {Array.from({ length: 10 }, (_, i) => (
          <span key={i} className={`h-3 w-2 rounded-[1px] ${i < lit ? "bg-ok animate-bar-glow" : "bg-line"}`} />
        ))}
      </div>
      <span className="font-mono text-[11px] text-ok/80">live</span>
    </div>
  );
}

function Note({ text }: { text: string | null | undefined }) {
  if (!text) return null;
  return <div className="truncate text-xs text-dim" title={text}>{text}</div>;
}

export function OutcomeCell({ call, best, elapsed }: { call: Call; best: boolean; elapsed: number | null }) {
  if (call.state === "failed") {
    return (
      <span className="font-mono text-xs text-bad/70">{call.termination_type ?? call.error ?? "no result"}</span>
    );
  }
  if (call.state === "talking") return <TalkBar elapsed={elapsed} />;
  if (call.state === "extracting" && !call.outcome) {
    return <span className="font-mono text-xs text-violet/80">reading the transcript…</span>;
  }

  const o = call.outcome;
  if (!o) return <span className="text-dim">{EM}</span>;
  const track = o.track ?? call.track;

  if (track === "quote") {
    const q = o as Extract<typeof o, { track: "quote" }>;
    if (q.amount_usd === null || q.amount_usd === undefined) {
      return (
        <div className="min-w-0">
          <span className="text-dim">{EM}</span>
          <Note text={q.note} />
        </div>
      );
    }
    return (
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="tnum font-mono text-base text-fg">
            {fmtUsd(q.amount_usd)}
            {q.unit ? <span className="ml-1 text-xs text-muted">{q.unit}</span> : null}
          </span>
          {best ? <Chip tone="ok">◀ best</Chip> : null}
        </div>
        {q.caveats?.length ? (
          <div className="truncate text-xs text-dim" title={q.caveats.join("; ")}>
            {q.caveats.join("; ")}
          </div>
        ) : null}
        <Note text={q.note} />
      </div>
    );
  }

  if (track === "schedule") {
    const s = o as Extract<typeof o, { track: "schedule" }>;
    if (!s.slot_iso) {
      return (
        <div className="min-w-0">
          <span className="text-dim">{EM}</span>
          <Note text={s.note} />
        </div>
      );
    }
    return (
      <div className="min-w-0">
        <span className="tnum font-mono text-base text-fg">
          {fmtSlot(s.slot_iso)}
          {s.confirmed ? <span className="ml-1 text-ok">✓</span> : <span className="ml-1 text-xs text-warn">unconfirmed</span>}
        </span>
        <Note text={s.note} />
      </div>
    );
  }

  if (track === "negotiate") {
    const n = o as Extract<typeof o, { track: "negotiate" }>;
    const action = n.action ?? "none";
    const tone = action === "reduced" || action === "canceled" ? "text-ok" : action === "refused" ? "text-warn" : "text-muted";
    return (
      <div className="min-w-0">
        <span className="font-mono text-base">
          <span className={tone}>{action}</span>
          {n.saved_usd !== null && n.saved_usd !== undefined ? (
            <span className="tnum ml-2 text-fg">saved {fmtUsd(n.saved_usd)}</span>
          ) : null}
          {n.effective_date ? <span className="ml-2 text-xs text-muted">from {fmtDay(n.effective_date)}</span> : null}
        </span>
        <Note text={n.note} />
      </div>
    );
  }

  return <span className="text-dim">{EM}</span>;
}
