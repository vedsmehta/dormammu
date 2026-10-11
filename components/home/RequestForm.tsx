"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorNote } from "@/components/ui";
import { createRun } from "@/lib/api";

const PLACEHOLDER = "I'm moving to SF next month, handle it";

/**
 * The single-line request box. POST /api/py/runs awaits the fan-out (plan + place calls), so the
 * run id only exists once it returns; the button shows that wait, then we route to the run.
 */
export function RequestForm() {
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
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PLACEHOLDER}
          autoFocus
          disabled={busy}
          className="flex-1 rounded-lg border border-line bg-panel px-4 py-4 text-lg text-fg placeholder:text-dim focus:border-accent focus:outline-none disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || text.trim().length === 0}
          className="rounded-lg bg-fg px-5 py-4 text-base font-medium text-bg transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50"
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
      {busy ? (
        <p className="font-mono text-xs text-muted">
          planning the roster and placing calls; the run opens as soon as the fan-out is out
        </p>
      ) : null}
      <ErrorNote message={error} />
    </form>
  );
}
