import { parseTs, toLocalInput, toOffsetIso } from "@/lib/format";
import type { Availability } from "@/lib/types";

/** One availability window as the <input type="datetime-local"> pair the forms edit. */
export type Slot = { start: string; end: string };

export function toSlots(avail: Availability[] | undefined): Slot[] {
  if (!Array.isArray(avail)) return [];
  return avail.map((a) => {
    const s = parseTs(a.start);
    const e = parseTs(a.end);
    return {
      start: s === null ? "" : toLocalInput(new Date(s)),
      end: e === null ? "" : toLocalInput(new Date(e)),
    };
  });
}

export function fromSlots(slots: Slot[]): Availability[] {
  return slots
    .filter((s) => s.start && s.end)
    .map((s) => ({ start: toOffsetIso(new Date(s.start)), end: toOffsetIso(new Date(s.end)) }));
}
