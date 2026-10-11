// Client-side checks for the input forms. Each returns a message to show under the field, or null
// when the value is fine. Blank is fine unless the caller says the field is required.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

export type Errors = Record<string, string>;

export function checkEmail(value: string): string | null {
  const v = value.trim();
  if (!v) return "Enter your email.";
  if (!EMAIL_RE.test(v)) return "That doesn't look like an email. Try name@school.edu.";
  return null;
}

export function checkPassword(value: string, min: number): string | null {
  if (!value) return "Enter your password.";
  if (value.length < min) return `Use at least ${min} characters (${value.length} so far).`;
  return null;
}

/**
 * Phone as typed -> E.164, or null when it can't be one. Ten digits are read as a US number;
 * anything else needs its country code.
 */
export function normalizePhone(value: string): string | null {
  const v = value.trim();
  if (!/^\+?[\d\s().-]+$/.test(v)) return null;
  const digits = v.replace(/\D/g, "");
  if (v.startsWith("+")) return digits.length >= 8 && digits.length <= 15 ? `+${digits}` : null;
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  return null;
}

export function checkPhone(value: string): string | null {
  if (!value.trim()) return null;
  return normalizePhone(value) ? null : "Enter a full phone number, like +1 415 555 0100.";
}

export function checkZip(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  return /^\d{5}(-\d{4})?$/.test(v) ? null : "ZIP is 5 digits, like 94305.";
}

/** A blank-or-number field. `whole` also rejects decimals. */
export function checkNumber(value: string, opts: { min: number; whole?: boolean; what: string }): string | null {
  const v = value.trim();
  if (!v) return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return `${opts.what} must be a number.`;
  if (n < opts.min) return `${opts.what} can't be less than ${opts.min}.`;
  if (opts.whole && !Number.isInteger(n)) return `${opts.what} must be a whole number.`;
  return null;
}

/** One availability window: both ends set, and the end after the start. */
export function checkWindow(start: string, end: string): string | null {
  if (!start && !end) return "Set a start and an end, or remove this window.";
  if (!start) return "Set when this window starts.";
  if (!end) return "Set when this window ends.";
  if (new Date(end).getTime() <= new Date(start).getTime()) return "The end must be after the start.";
  return null;
}

/** Drop the keys whose check passed, so `Object.keys(errors).length` is the error count. */
export function collect(checks: Record<string, string | null>): Errors {
  const out: Errors = {};
  for (const [k, v] of Object.entries(checks)) if (v) out[k] = v;
  return out;
}

/** After a failed submit: move focus to the first field that was marked invalid. */
export function focusFirstInvalid(form: HTMLElement | null): void {
  requestAnimationFrame(() => {
    const el = form?.querySelector<HTMLElement>('[aria-invalid="true"]');
    el?.focus();
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}

export function countNote(errors: Errors): string | null {
  const n = Object.keys(errors).length;
  if (n === 0) return null;
  return n === 1 ? "One field needs fixing before this can be saved." : `${n} fields need fixing before this can be saved.`;
}
