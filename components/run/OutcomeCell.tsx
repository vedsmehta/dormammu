import { EM, fmtDay, fmtSlot, fmtUsd } from "@/lib/format";
import type { Call } from "@/lib/types";

const MAX_CALL_SECONDS = 180;

/** How far a live call is through its 180s budget, as a slim bar in the prototype's .bar shape. */
function TalkBar({ elapsed }: { elapsed: number | null }) {
  const pct = Math.min(100, Math.max(0, ((elapsed ?? 0) / MAX_CALL_SECONDS) * 100));
  return (
    <span className="talkbar" role="img" aria-label={`talking, ${Math.round(pct)}% of the call budget`}>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}

/** The .got cell of a run row: the result in the display face, with the note as a small em line. */
export function OutcomeCell({ call, best, elapsed }: { call: Call; best: boolean; elapsed: number | null }) {
  if (call.state === "failed") {
    return <em className="t-warn">{call.termination_type ?? call.error ?? "No result"}</em>;
  }
  if (call.state === "talking") return <TalkBar elapsed={elapsed} />;
  if (call.state === "extracting" && !call.outcome) {
    return <em className="t-texting">Reading the transcript…</em>;
  }

  const o = call.outcome;
  if (!o) return <span className="plain">{EM}</span>;
  const track = o.track ?? call.track;

  if (track === "quote") {
    const q = o as Extract<typeof o, { track: "quote" }>;
    if (q.amount_usd === null || q.amount_usd === undefined) {
      return (
        <>
          <span className="plain">No quote</span>
          {q.note ? <em>{q.note}</em> : null}
        </>
      );
    }
    const note = [best ? "Best so far" : null, q.caveats?.length ? q.caveats.join("; ") : null, q.note].filter(Boolean).join(" · ");
    return (
      <>
        {fmtUsd(q.amount_usd)}
        {q.unit ? <span className="plain">{` ${q.unit}`}</span> : null}
        {note ? <em title={note}>{note}</em> : null}
      </>
    );
  }

  if (track === "schedule") {
    const s = o as Extract<typeof o, { track: "schedule" }>;
    if (!s.slot_iso) {
      return (
        <>
          <span className="plain">No slot</span>
          {s.note ? <em>{s.note}</em> : null}
        </>
      );
    }
    const note = [s.confirmed ? "Booked" : "Unconfirmed", s.note].filter(Boolean).join(". ");
    return (
      <>
        {fmtSlot(s.slot_iso)}
        <em className={s.confirmed ? undefined : "t-warn"}>{note}</em>
      </>
    );
  }

  if (track === "negotiate") {
    const n = o as Extract<typeof o, { track: "negotiate" }>;
    const action = n.action ?? "none";
    const word = action === "reduced" ? "Reduced" : action === "canceled" ? "Canceled" : action === "refused" ? "Refused" : "No change";
    const bits: string[] = [];
    if (n.saved_usd !== null && n.saved_usd !== undefined) bits.push(`saved ${fmtUsd(n.saved_usd)}`);
    if (n.effective_date) bits.push(`from ${fmtDay(n.effective_date)}`);
    if (n.note) bits.push(n.note);
    return (
      <>
        <span className={action === "refused" ? "t-warn" : action === "none" ? "plain" : undefined}>{word}</span>
        {bits.length ? <em>{bits.join(" · ")}</em> : null}
      </>
    );
  }

  return <span className="plain">{EM}</span>;
}
