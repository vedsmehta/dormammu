"use client";

import type { Slot } from "@/components/profile/availability";

/** The availability windows list shared by onboarding and the profile page. */
export function AvailabilityEditor({ slots, onChange }: { slots: Slot[]; onChange: (slots: Slot[]) => void }) {
  const update = (i: number, patch: Partial<Slot>) => onChange(slots.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  return (
    <fieldset className="field">
      <legend>When are you free for appointments?</legend>
      {slots.length === 0 ? <p className="hint">No windows yet. The scheduling track books inside these.</p> : null}
      <ul className="windows">
        {slots.map((s, i) => (
          <li key={i}>
            <label className="field tight">
              <span className="lbl">From</span>
              <input type="datetime-local" className="input" value={s.start} onChange={(e) => update(i, { start: e.target.value })} aria-label="window start" />
            </label>
            <label className="field tight">
              <span className="lbl">To</span>
              <input type="datetime-local" className="input" value={s.end} onChange={(e) => update(i, { end: e.target.value })} aria-label="window end" />
            </label>
            <button type="button" className="rm" onClick={() => onChange(slots.filter((_, idx) => idx !== i))} aria-label="remove window">
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="add" onClick={() => onChange([...slots, { start: "", end: "" }])}>
        + Add a window
      </button>
    </fieldset>
  );
}
