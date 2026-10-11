"use client";

import { useRouter } from "next/navigation";
import { useState, type CSSProperties } from "react";

import { ErrorNote } from "@/components/ui";
import { research } from "@/lib/api";
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
 * The ask box from dashboard.html. POST /api/py/research looks up options first and dials nobody; the
 * button shows that wait, then we route to the proposal, where the user approves who gets called.
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
      const proposal = await research(request);
      router.push(`/proposals/${proposal.id}`);
    } catch (err) {
      setError(describeError(err));
      setBusy(false);
    }
  };

  return (
    <>
      <form onSubmit={submit} className="askbox rise" style={{ "--d": ".2s" } as CSSProperties}>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={PLACEHOLDER}
          aria-label={PLACEHOLDER}
          autoFocus={autoFocus}
          autoComplete="off"
          disabled={busy}
        />
        <button type="submit" className="btn btn-primary" disabled={busy || text.trim().length === 0}>
          {busy ? "Researching…" : "Handle it"}
        </button>
      </form>
      <div className="chips rise" style={{ "--d": ".3s" } as CSSProperties} aria-label="Examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex.label}
            type="button"
            className="glass-pill"
            disabled={busy}
            onClick={() => setText(ex.fill)}
            aria-pressed={text === ex.fill}
          >
            {ex.label}
          </button>
        ))}
      </div>
      {busy ? <p className="note">Researching providers, prices and discounts for your profile. This can take a minute. Nobody is called until you approve.</p> : null}
      <ErrorNote message={error} />
    </>
  );
}
