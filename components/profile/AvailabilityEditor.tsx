"use client";

import type { Slot } from "@/components/profile/availability";
import { inputClass } from "@/components/ui";

/** The availability windows list shared by onboarding and the profile page. */
export function AvailabilityEditor({ slots, onChange }: { slots: Slot[]; onChange: (slots: Slot[]) => void }) {
  const update = (i: number, patch: Partial<Slot>) => onChange(slots.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-xs uppercase tracking-wide text-muted">availability windows</span>
        <button
          type="button"
          onClick={() => onChange([...slots, { start: "", end: "" }])}
          className="text-xs text-accent hover:underline"
        >
          + add window
        </button>
      </div>
      {slots.length === 0 ? (
        <p className="text-xs text-dim">No windows yet. The scheduling track books inside these.</p>
      ) : (
        <ul className="space-y-2">
          {slots.map((s, i) => (
            <li key={i} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
              <input
                type="datetime-local"
                value={s.start}
                onChange={(e) => update(i, { start: e.target.value })}
                aria-label="window start"
                className={`${inputClass} font-mono`}
              />
              <input
                type="datetime-local"
                value={s.end}
                onChange={(e) => update(i, { end: e.target.value })}
                aria-label="window end"
                className={`${inputClass} font-mono`}
              />
              <button
                type="button"
                onClick={() => onChange(slots.filter((_, idx) => idx !== i))}
                className="px-2 text-xs text-dim hover:text-bad"
                aria-label="remove window"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
