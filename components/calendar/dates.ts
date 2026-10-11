// Plain date math on 'YYYY-MM-DD' day keys, displayed in America/Los_Angeles. No library.
// Keys are moved with UTC arithmetic so DST never shifts a day; instants are keyed through Intl.

export const TZ = "America/Los_Angeles";

const KEY_FMT = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
const TIME_FMT = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
const LONG_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
const MONTH_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "long", year: "numeric" });
const SHORT_FMT = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Instant (epoch ms) -> the LA calendar day it falls on. */
export function dayKeyOf(ms: number): string {
  return KEY_FMT.format(new Date(ms));
}

/** Instant -> '2:30pm' in LA. */
export function timeOf(ms: number): string {
  return TIME_FMT.format(new Date(ms)).replace(/\s(AM|PM)$/i, (m) => m.trim().toLowerCase());
}

export function todayKey(now: number = Date.now()): string {
  return dayKeyOf(now);
}

export function parseKey(key: string): [number, number, number] | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(key);
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

/** Key -> UTC midnight ms, used only for arithmetic and ordering. */
export function keyToUtc(key: string): number | null {
  const p = parseKey(key);
  if (!p) return null;
  return Date.UTC(p[0], p[1] - 1, p[2]);
}

function utcToKey(ms: number): string {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function addDays(key: string, n: number): string {
  const ms = keyToUtc(key);
  if (ms === null) return key;
  return utcToKey(ms + n * 86400000);
}

export function addMonths(key: string, n: number): string {
  const p = parseKey(key);
  if (!p) return key;
  return utcToKey(Date.UTC(p[0], p[1] - 1 + n, 1));
}

/** Monday = 0 … Sunday = 6. */
export function weekdayMon0(key: string): number {
  const ms = keyToUtc(key);
  if (ms === null) return 0;
  return (new Date(ms).getUTCDay() + 6) % 7;
}

/** Monday through Sunday of the week containing `key`. */
export function weekOf(key: string): string[] {
  const monday = addDays(key, -weekdayMon0(key));
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

/** Six Monday-first rows covering the month that `key` falls in. */
export function monthGrid(key: string): string[][] {
  const p = parseKey(key);
  if (!p) return [];
  const first = utcToKey(Date.UTC(p[0], p[1] - 1, 1));
  const start = addDays(first, -weekdayMon0(first));
  return Array.from({ length: 6 }, (_, w) => Array.from({ length: 7 }, (_, d) => addDays(start, w * 7 + d)));
}

export function sameMonth(a: string, b: string): boolean {
  return a.slice(0, 7) === b.slice(0, 7);
}

export function monthTitle(key: string): string {
  const ms = keyToUtc(key);
  return ms === null ? key : MONTH_FMT.format(new Date(ms));
}

/** 'Mon, Oct 12' */
export function fmtKeyLong(key: string): string {
  const ms = keyToUtc(key);
  return ms === null ? key : LONG_FMT.format(new Date(ms));
}

/** 'Oct 12' */
export function fmtKeyShort(key: string): string {
  const ms = keyToUtc(key);
  return ms === null ? key : SHORT_FMT.format(new Date(ms));
}

export function dayNumber(key: string): number {
  return parseKey(key)?.[2] ?? 0;
}

/** '14:30' -> '2:30pm' for the manual booking label. */
export function fmtClock(hhmm: string): string {
  const m = /^(\d{1,2}):(\d{2})/.exec(hhmm);
  if (!m) return hhmm;
  const h = Number(m[1]);
  const suffix = h >= 12 ? "pm" : "am";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12}:${m[2]}${suffix}`;
}
