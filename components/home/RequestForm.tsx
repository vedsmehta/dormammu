"use client";

import { useRouter } from "next/navigation";
import { useState, type CSSProperties } from "react";

import { ErrorNote } from "@/components/ui";
import { research } from "@/lib/api";
import { describeError } from "@/lib/format";

const PLACEHOLDER = "What do you need handled?";
const RESEARCH_PLACEHOLDER = "What should Dormammu research?";

// The example chips from the prototype's ask box. They only fill the input; nothing is sent until
// one of the two buttons is pressed.
const EXAMPLES: { label: string; fill: string }[] = [
  { label: "Moving in", fill: "I'm moving into my first apartment near campus on the 1st, handle it." },
  { label: "Lower a bill", fill: "My phone bill is too high, get it down." },
  { label: "Book a dentist", fill: "Find me a dentist near campus this week." },
  { label: "Cancel something", fill: "Cancel the subscriptions I don't use." },
];

type Busy = null | "research" | "roster";
type Failure = { from: Busy; request: string; message: string };

const d = (s: string) => ({ "--d": s }) as CSSProperties;

/**
 * The ask box from dashboard.html. POST /api/py/research looks up options first and dials nobody; the
 * button shows that wait, then we route to the proposal, where the user approves who gets called.
 */
export function RequestForm({ autoFocus = true, variant = "home" }: { autoFocus?: boolean; variant?: "home" | "research" }) {
  const router = useRouter();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<Failure | null>(null);

  const request = text.trim();
  const locked = busy !== null;

  const researchFirst = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!request || locked) return;
    setBusy("research");
    setFailure(null);
    try {
      const proposal = await research(request);
      router.push(`/proposals/${proposal.id}`);
    } catch (err) {
      setFailure({ from: "roster", request: which, message: describeError(err) });
      setBusy(null);
    }
  };

  return (
    <>
      <form onSubmit={researchFirst} className="askbox rise" style={d(".2s")}>
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={variant === "research" ? RESEARCH_PLACEHOLDER : PLACEHOLDER}
          aria-label={variant === "research" ? RESEARCH_PLACEHOLDER : PLACEHOLDER}
          autoFocus={autoFocus}
          autoComplete="off"
          disabled={locked}
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
