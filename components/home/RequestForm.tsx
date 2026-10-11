"use client";

import { useRouter } from "next/navigation";
import { useState, type CSSProperties } from "react";

import { ResearchProgress } from "@/components/research/ResearchProgress";
import { apiDetail } from "@/components/research/support";
import { createRun, research } from "@/lib/api";
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
 * The ask box from dashboard.html, with two paths out of it.
 *
 * "Research first" POSTs /api/py/research: the engine reads the profile and roster, searches, verifies
 * phones, plans, discounts and regulations, and stores a proposal. Nothing is dialed; we route to the
 * proposal for approval. "Call my roster now" is today's POST /api/py/runs, unchanged: the fan-out is
 * awaited, so the run id only exists once it returns, then we route to the run.
 *
 * `variant="research"` (the /research index) keeps only the research path and drops the example chips.
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
      const row = await research(request);
      router.push(`/research/${row.id}`);
    } catch (err) {
      setFailure({ from: "research", request, message: apiDetail(err) });
      setBusy(null);
    }
  };

  const callRoster = async (which: string) => {
    if (!which || locked) return;
    setBusy("roster");
    setFailure(null);
    try {
      const run = await createRun(which);
      router.push(`/runs/${run.id}`);
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
        <button type="submit" className="btn btn-primary" disabled={locked || request.length === 0}>
          {busy === "research" ? "Researching…" : "Research first"}
        </button>
      </form>

      {variant === "home" ? (
        <>
          <div className="rise mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[14px] text-white/75" style={d(".25s")}>
            <button type="button" className="btn btn-glass btn-sm" disabled={locked || request.length === 0} onClick={() => callRoster(request)}>
              {busy === "roster" ? "Dispatching…" : "Call my roster now"}
            </button>
            <span>Skips the research and calls the vendors already on your roster.</span>
          </div>
          <div className="chips rise" style={d(".3s")} aria-label="Examples">
            {EXAMPLES.map((ex) => (
              <button
                key={ex.label}
                type="button"
                className="glass-pill"
                disabled={locked}
                onClick={() => setText(ex.fill)}
                aria-pressed={text === ex.fill}
              >
                {ex.label}
              </button>
            ))}
          </div>
        </>
      ) : null}

      {busy === "research" ? <ResearchProgress request={request} /> : null}
      {busy === "roster" ? (
        <p className="note" role="status">
          Planning the roster and placing calls. The run opens as soon as the fan-out is out.
        </p>
      ) : null}

      {failure ? (
        <div className="note bad" role="alert">
          <p>
            <b>{failure.from === "research" ? "Research did not start." : "The roster run did not start."}</b> {failure.message}
          </p>
          {failure.from === "research" ? (
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button type="button" className="btn btn-line btn-sm" disabled={locked} onClick={() => callRoster(failure.request)}>
                Call my roster now instead
              </button>
              <span className="text-[14px] opacity-80">Same request, no research: the vendors on your roster are called directly.</span>
            </div>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
