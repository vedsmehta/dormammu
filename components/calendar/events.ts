// Turn calls, ledger rows and availability windows into dated calendar events. Pure; no clock.

import type { CallWithRequest } from "@/lib/api";
import { parseTs } from "@/lib/format";
import type { Availability, LedgerRow } from "@/lib/types";

import { addDays, dayKeyOf, keyToUtc, timeOf } from "./dates";

export type CalKind = "appointment" | "pencilled" | "due" | "quote" | "avail";

export interface CalEvent {
  id: string;
  /** LA day key the event is drawn on. */
  day: string;
  kind: CalKind;
  title: string;
  /** Second line: phone, amount, window. */
  detail: string | null;
  /** '2:30pm' or a range for availability; null for all-day ledger dates. */
  time: string | null;
  href: string | null;
  seeded: boolean;
  source: "call" | "ledger" | "profile";
  /** Ordering within a day. */
  at: number;
}

export const KIND_LABEL: Record<CalKind, string> = {
  appointment: "Appointment",
  pencilled: "Pencilled",
  due: "Due",
  quote: "Quote valid until",
  avail: "Available",
};

function isSeeded(...texts: (string | null | undefined)[]): boolean {
  return texts.some((t) => typeof t === "string" && /seed/i.test(t));
}

/** Schedule-track calls with a slot: confirmed draws solid, unconfirmed draws as pencilled. */
export function callEvents(calls: CallWithRequest[]): CalEvent[] {
  const out: CalEvent[] = [];
  for (const c of calls) {
    if (c.track !== "schedule" || !c.outcome || c.outcome.track !== "schedule" || !c.outcome.slot_iso) continue;
    const ms = parseTs(c.outcome.slot_iso);
    if (ms === null) continue;
    out.push({
      id: `call:${c.id}`,
      day: dayKeyOf(ms),
      kind: c.outcome.confirmed ? "appointment" : "pencilled",
      title: c.target_name,
      detail: c.target_phone || null,
      time: timeOf(ms),
      href: `/calls/${encodeURIComponent(c.id)}?run=${encodeURIComponent(c.run_id)}`,
      seeded: isSeeded(c.error, c.request),
      source: "call",
      at: ms,
    });
  }
  return out;
}

function ledgerHref(r: LedgerRow): string {
  if (!r.source_call) return "/ledger";
  return r.run_id
    ? `/calls/${encodeURIComponent(r.source_call)}?run=${encodeURIComponent(r.run_id)}`
    : `/calls/${encodeURIComponent(r.source_call)}`;
}

/** appointment on effective_on; promo_expiry and renewal due on expires_on; quote valid until expires_on. */
export function ledgerEvents(rows: LedgerRow[]): CalEvent[] {
  const out: CalEvent[] = [];
  for (const r of rows) {
    let day: string | null;
    let kind: CalKind;
    if (r.kind === "appointment") {
      day = r.effective_on ?? r.expires_on;
      kind = "appointment";
    } else if (r.kind === "quote") {
      day = r.expires_on;
      kind = "quote";
    } else {
      day = r.expires_on;
      kind = "due";
    }
    if (!day) continue;
    const key = day.slice(0, 10);
    const at = keyToUtc(key);
    if (at === null) continue;
    const amount = r.amount_usd !== null && Number.isFinite(r.amount_usd) ? `$${r.amount_usd}` : null;
    out.push({
      id: `ledger:${r.id}`,
      day: key,
      kind,
      title: r.label,
      detail: [r.kind.replace("_", " "), amount].filter(Boolean).join(" · ") || null,
      time: null,
      href: ledgerHref(r),
      seeded: isSeeded(r.label),
      source: "ledger",
      at,
    });
  }
  return out;
}

/** Each availability window, one shaded block per LA day it touches. */
export function availabilityEvents(avail: Availability[] | undefined): CalEvent[] {
  if (!Array.isArray(avail)) return [];
  const out: CalEvent[] = [];
  avail.forEach((w, i) => {
    const s = parseTs(w.start);
    const e = parseTs(w.end);
    if (s === null || e === null || e < s) return;
    const firstDay = dayKeyOf(s);
    const lastDay = dayKeyOf(e);
    let day = firstDay;
    for (let guard = 0; guard < 31; guard += 1) {
      const isFirst = day === firstDay;
      const isLast = day === lastDay;
      out.push({
        id: `avail:${i}:${day}`,
        day,
        kind: "avail",
        title: "Available",
        detail: null,
        time: `${isFirst ? timeOf(s) : "start"} to ${isLast ? timeOf(e) : "end of day"}`,
        href: "#availability",
        seeded: false,
        source: "profile",
        at: isFirst ? s : (keyToUtc(day) ?? s),
      });
      if (isLast) break;
      day = addDays(day, 1);
    }
  });
  return out;
}

export function groupByDay(events: CalEvent[]): Map<string, CalEvent[]> {
  const map = new Map<string, CalEvent[]>();
  for (const e of events) {
    const list = map.get(e.day) ?? [];
    list.push(e);
    map.set(e.day, list);
  }
  for (const list of map.values()) list.sort((a, b) => a.at - b.at || a.kind.localeCompare(b.kind));
  return map;
}
