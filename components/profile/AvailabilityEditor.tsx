"use client";

import type { Slot } from "@/components/profile/availability";
import { FieldError } from "@/components/ui";
import { checkWindow, type Errors } from "@/lib/validate";

/** Problems with the windows, keyed "win.<index>", for the form's error map. */
export function checkSlots(slots: Slot[]): Record<string, string | null> {
  return Object.fromEntries(slots.map((s, i) => [`win.${i}`, checkWindow(s.start, s.end)]));
}

/** The availability windows list shared by onboarding and the profile page. */
export function AvailabilityEditor({ slots, onChange, errors = {} }: { slots: Slot[]; onChange: (slots: Slot[]) => void; errors?: Errors }) {
  const update = (i: number, patch: Partial<Slot>) => onChange(slots.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="field">
      <legend>When are you free for appointments?</legend>
      {slots.length === 0 ? <p className="hint">No windows yet. The scheduling track books inside these.</p> : null}
      <ul className="windows">
        {slots.map((s, i) => {
          const err = errors[`win.${i}`];
          // Mark the end that is missing; when both are set the end is the one that is wrong.
          const badStart = Boolean(err) && !s.start;
          const badEnd = Boolean(err) && !badStart;
          return (
          <li key={i}>
            <label className="field tight">
              <span className="lbl">From</span>
              <input type="datetime-local" className="input" value={s.start} onChange={(e) => update(i, { start: e.target.value })} aria-label="window start" aria-invalid={badStart || undefined} />
            </label>
            <label className="field tight">
              <span className="lbl">To</span>
              <input type="datetime-local" className="input" value={s.end} onChange={(e) => update(i, { end: e.target.value })} aria-label="window end" aria-invalid={badEnd || undefined} />
            </label>
            <button type="button" className="rm" onClick={() => onChange(slots.filter((_, idx) => idx !== i))} aria-label="remove window">
              ×
            </button>
            <FieldError message={err} />
          </li>
          );
        })}
      </ul>
      <button type="button" className="add" onClick={() => onChange([...slots, { start: "", end: "" }])}>
        + Add a window
      </button>
    </fieldset>
  );
}
