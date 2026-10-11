import { EM, fmtDay, fmtSlot, fmtUsd } from "@/lib/format";
import type { Call } from "@/lib/types";

export type OutcomeSummary = {
  /** The headline: an amount, a slot, an action, or the live state word. */
  main: string;
  /** Caveats, the extraction note, or the failure reason. */
  sub: string | null;
  tone: "fg" | "ok" | "warn" | "bad" | "muted";
};

/**
 * One line per call for list views. Everything comes from the outcome json or the call row; when
 * there is nothing yet the headline is the state, never a placeholder figure.
 */
export function summarizeOutcome(call: Call): OutcomeSummary {
  if (call.state === "failed") {
    return { main: "Failed", sub: call.termination_type ?? call.error ?? null, tone: "bad" };
  }
  const o = call.outcome;
  if (!o) {
    switch (call.state) {
      case "planned":
        return { main: "Planned", sub: null, tone: "muted" };
      case "dialing":
        return { main: "Dialing", sub: null, tone: "muted" };
      case "ringing":
        return { main: "Ringing", sub: null, tone: "warn" };
      case "talking":
        return { main: "Talking now", sub: null, tone: "ok" };
      case "extracting":
        return { main: "Reading the transcript", sub: null, tone: "muted" };
      default:
        return { main: "No result", sub: null, tone: "muted" };
    }
  }
  const track = o.track ?? call.track;
  if (track === "quote") {
    const q = o as Extract<typeof o, { track: "quote" }>;
    const caveats = q.caveats?.length ? q.caveats.join("; ") : null;
    if (q.amount_usd === null || q.amount_usd === undefined) {
      return { main: "No quote", sub: q.note ?? caveats, tone: "muted" };
    }
    return { main: `${fmtUsd(q.amount_usd)}${q.unit ? ` ${q.unit}` : ""}`, sub: caveats ?? q.note ?? null, tone: "fg" };
  }
  if (track === "schedule") {
    const s = o as Extract<typeof o, { track: "schedule" }>;
    if (!s.slot_iso) return { main: "No slot", sub: s.note ?? null, tone: "muted" };
    return {
      main: fmtSlot(s.slot_iso),
      sub: s.confirmed ? (s.note ? `Booked. ${s.note}` : "Booked") : (s.note ? `Unconfirmed. ${s.note}` : "Unconfirmed"),
      tone: s.confirmed ? "fg" : "warn",
    };
  }
  if (track === "negotiate") {
    const n = o as Extract<typeof o, { track: "negotiate" }>;
    const action = n.action ?? "none";
    const word = action === "reduced" ? "Reduced" : action === "canceled" ? "Canceled" : action === "refused" ? "Refused" : "No change";
    const bits: string[] = [];
    if (n.saved_usd !== null && n.saved_usd !== undefined) bits.push(`saved ${fmtUsd(n.saved_usd)}`);
    if (n.effective_date) bits.push(`from ${fmtDay(n.effective_date)}`);
    if (n.note) bits.push(n.note);
    return {
      main: word,
      sub: bits.length ? bits.join(" · ") : null,
      tone: action === "reduced" || action === "canceled" ? "ok" : action === "refused" ? "warn" : "muted",
    };
  }
  return { main: EM, sub: null, tone: "muted" };
}

export const TONE_CLASS: Record<OutcomeSummary["tone"], string> = {
  fg: "text-fg",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  muted: "text-muted",
};

/** Where a call's detail page lives. There is no per-call endpoint, so the run id rides along. */
export function callHref(call: Pick<Call, "id" | "run_id">): string {
  return `/calls/${encodeURIComponent(call.id)}?run=${encodeURIComponent(call.run_id)}`;
}
