// Pure formatting helpers. Nothing here reads the clock; callers pass timestamps in.

export const EM = "\u2014";

/** '+14155551267' -> '+1 (415) ***-**67'. The video is public; never show a full number. */
export function maskPhone(phone: string | null | undefined): string {
  if (!phone) return EM;
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 0) return EM;
  let national = digits;
  let cc = "";
  if (digits.length === 11 && digits.startsWith("1")) {
    cc = "1";
    national = digits.slice(1);
  } else if (digits.length === 10) {
    cc = "1";
  }
  if (national.length === 10 && cc) {
    return `+${cc} (${national.slice(0, 3)}) ***-**${national.slice(-2)}`;
  }
  return `+${digits.slice(0, 2)} ***-**${digits.slice(-2)}`;
}

/** Seconds -> 'mm:ss'. Null or NaN -> '--:--'. Minutes are not capped at 59. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "--:--";
  const s = Math.max(0, Math.floor(seconds));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

/** Seconds -> '8m 39s' style for prose cells. */
export function fmtDurationWords(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return EM;
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m === 0) return `${r}s`;
  return `${m}m ${String(r).padStart(2, "0")}s`;
}

export function fmtUsd(amount: number | null | undefined): string {
  if (amount === null || amount === undefined || !Number.isFinite(amount)) return EM;
  const whole = Number.isInteger(amount);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: whole ? 0 : 2,
    maximumFractionDigits: whole ? 0 : 2,
  }).format(amount);
}

/**
 * Parse a server timestamp to epoch ms. Postgres gives '+00:00'; a naive Python isoformat has no
 * zone, so treat it as UTC rather than letting the browser assume local time.
 */
export function parseTs(ts: string | null | undefined): number | null {
  if (!ts) return null;
  let s = ts.trim();
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s);
  if (!hasZone && s.includes("T")) s = s + "Z";
  if (!hasZone && !s.includes("T") && s.includes(" ")) s = s.replace(" ", "T") + "Z";
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? ms : null;
}

const SLOT_FMT = new Intl.DateTimeFormat("en-US", { weekday: "short", hour: "numeric", minute: "2-digit" });

/** '2026-10-15T14:30:00-07:00' -> 'Thu 2:30pm'. Unparseable input is returned as is. */
export function fmtSlot(iso: string | null | undefined): string {
  if (!iso) return EM;
  const ms = parseTs(iso);
  if (ms === null) return iso;
  return SLOT_FMT.format(new Date(ms)).replace(/\s(AM|PM)$/i, (m) => m.trim().toLowerCase());
}

const STAMP_FMT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** Timestamp -> 'Oct 11, 1:25 PM'. */
export function fmtStamp(iso: string | null | undefined): string {
  const ms = parseTs(iso);
  if (ms === null) return EM;
  return STAMP_FMT.format(new Date(ms));
}

const DAY_FMT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

/** 'YYYY-MM-DD' -> 'Sep 12, 2027' without a UTC day shift. */
export function fmtDay(day: string | null | undefined): string {
  if (!day) return EM;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  if (!m) return day;
  return DAY_FMT.format(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
}

/** Local calendar date of a Date as 'YYYY-MM-DD'. */
export function toLocalDay(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Date -> value for an <input type="datetime-local">. */
export function toLocalInput(d: Date): string {
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${toLocalDay(d)}T${hh}:${mm}`;
}

/** Date -> ISO 8601 with the local offset, e.g. '2026-10-15T14:00:00-07:00'. */
export function toOffsetIso(d: Date): string {
  const offMin = -d.getTimezoneOffset();
  const sign = offMin >= 0 ? "+" : "-";
  const abs = Math.abs(offMin);
  const oh = String(Math.floor(abs / 60)).padStart(2, "0");
  const om = String(abs % 60).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${toLocalInput(d)}:${ss}${sign}${oh}:${om}`;
}

export function shortId(id: string | null | undefined, n = 6): string {
  if (!id) return EM;
  return id.slice(-n);
}

/** 'Good morning' / 'Good afternoon' / 'Good evening' for a local Date. */
export function greetingFor(d: Date): string {
  const h = d.getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

/** 'Maya Patel' -> 'MP'; falls back to the first letter of an email, else '?'. */
export function initials(name: string | null | undefined, fallback?: string | null): string {
  const src = (name ?? "").trim() || (fallback ?? "").trim();
  if (!src) return "?";
  const parts = src.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  return src[0].toUpperCase();
}

/**
 * A stored label or request as it should read on screen: without the "[seeded]" marker and the
 * bracketed bookkeeping ("(simulated)", "(30-day validity assumed)", "(11-month promo assumed)").
 * The stored text is untouched; Evidence and Insights still report how much data is seeded.
 */
export function tidy(text: string | null | undefined): string {
  return (text ?? "")
    .replace(/\[seeded\]\s*/gi, "")
    .replace(/\s*\((?:simulated|seeded|\d+-day validity assumed|\d+-month promo (?:assumed|stated on call))\)/gi, "")
    .trim();
}

/** First name from a display name, else the local part of an email, else 'there'. */
export function firstName(name: string | null | undefined, email?: string | null): string {
  const n = (name ?? "").trim();
  if (n) return n.split(/\s+/)[0];
  const e = (email ?? "").trim();
  if (e) return e.split("@")[0];
  return "there";
}

/**
 * The "detail" of a FastAPI error body as one sentence. A 422 carries a list of field problems
 * ({loc, msg}); those are joined as "field: problem". Null when the body is not that shape.
 */
function apiDetail(body: unknown): string | null {
  if (typeof body !== "string" || !body) return null;
  let detail: unknown;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail;
  } catch {
    return null;
  }
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const parts = detail.slice(0, 3).map((d: { loc?: unknown; msg?: unknown }) => {
    const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body" && typeof p === "string") : [];
    const msg = typeof d.msg === "string" ? d.msg.replace(/^Value error, /, "") : "is not valid";
    return loc.length ? `${loc.join(" ").replace(/_/g, " ")}: ${msg}` : msg;
  });
  if (parts.length === 0) return null;
  const more = detail.length - parts.length;
  return parts.join("; ") + (more > 0 ? `; and ${more} more` : "");
}

/**
 * A readable message for any thrown value. An ApiError carries a status and the response body:
 * 401 and 404 get a hint, and anything else shows the service's own explanation, never raw JSON.
 */
export function describeError(e: unknown): string {
  if (e instanceof Error) {
    const { status, body } = e as { status?: unknown; body?: unknown };
    if (status === 401) return "Not authorized by the API (401). Sign in again or set DEV_USER_ID on the Python side.";
    const detail = apiDetail(body);
    if (detail) return detail.charAt(0).toUpperCase() + detail.slice(1);
    if (status === 404) return "Not found (404).";
    if (typeof status === "number" && status >= 500) return `The service hit an error (${status}). Try again in a moment.`;
    if (e instanceof TypeError && /fetch|network/i.test(e.message)) return "Could not reach the service. Check that it is running, then try again.";
    return e.message;
  }
  return String(e);
}

const STAMP_FULL_FMT = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** Timestamp -> 'Oct 15, 2026, 2:30 PM'. */
export function fmtStampFull(iso: string | null | undefined): string {
  const ms = parseTs(iso);
  if (ms === null) return EM;
  return STAMP_FULL_FMT.format(new Date(ms));
}

const DAY_SHORT_FMT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

/** Timestamp -> 'Oct 15', with the year added once it differs from the current one. */
export function fmtDayFromTs(iso: string | null | undefined, now: Date = new Date()): string {
  const ms = parseTs(iso);
  if (ms === null) return EM;
  const d = new Date(ms);
  return d.getFullYear() === now.getFullYear() ? DAY_SHORT_FMT.format(d) : DAY_FMT.format(d);
}
