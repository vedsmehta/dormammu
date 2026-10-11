"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorNote } from "@/components/ui";
import { createRun } from "@/lib/api";
import { describeError } from "@/lib/format";

const PLACEHOLDER = "What do you need handled?";

// The example chips from the prototype's ask box. They only fill the input; nothing is sent until
// "Handle it".
const EXAMPLES: { label: string; fill: string }[] = [
  { label: "Moving in", fill: "I'm moving into my first apartment near campus on the 1st, handle it." },
  { label: "Lower a bill", fill: "My phone bill is too high, get it down." },
  { label: "Book a dentist", fill: "Find me a dentist near campus this week." },
  { label: "Cancel something", fill: "Cancel the subscriptions I don't use." },
];

/**
 * The ask box. POST /api/py/runs awaits the fan-out (plan + place calls), so the run id only exists
 * once it returns; the button shows that wait, then we route to the run.
 */
export function RequestForm({ autoFocus = true }: { autoFocus?: boolean }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const request = text.trim();
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const run = await createRun(request);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-col gap-2 rounded-2xl border border-line bg-panel p-2 focus-within:border-mint/60 sm:flex-row sm:items-center sm:rounded-full sm:pl-5">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PLACEHOLDER}
          aria-label={PLACEHOLDER}
          autoFocus={autoFocus}
          autoComplete="off"
          disabled={busy}
          className="min-w-0 flex-1 bg-transparent px-3 py-3 text-lg text-fg placeholder:text-dim focus:outline-none disabled:opacity-60 sm:px-0"
        />
        <button
          type="submit"
          disabled={busy || text.trim().length === 0}
          className="rounded-full bg-mint px-5 py-3 text-base font-medium text-bg transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? (
            <span className="font-mono text-sm">
              dispatching<span className="animate-blink">…</span>
            </span>
          ) : (
            "Handle it"
          )}
        </button>
      </div>
      <div className="flex flex-wrap gap-2" aria-label="Examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.label}
            type="button"
            disabled={busy}
            onClick={() => setText(ex.fill)}
            aria-pressed={text === ex.fill}
            className={`rounded-full border px-3 py-1.5 text-xs transition ${
              text === ex.fill
                ? "border-mint bg-mint text-bg"
                : "border-line bg-panel text-muted hover:border-muted hover:text-fg"
            }`}
          >
            {ex.label}
          </button>
        ))}
      </div>
      {busy ? (
        <p className="font-mono text-xs text-muted">
          planning the roster and placing calls; the run opens as soon as the fan-out is out
        </p>
      ) : null}
      <ErrorNote message={error} />
    </form>
  );
}
