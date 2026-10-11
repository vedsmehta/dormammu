"use client";

import { useEffect, useState, type CSSProperties } from "react";

/**
 * Shown while POST /research is in flight (20 to 60 s). There is one request and no progress channel,
 * so the steps are the engine's fixed order (docs/RESEARCH.md), not a live tracker; the only moving
 * part is the elapsed clock, which is this browser's.
 */
export function ResearchProgress({ request }: { request: string }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const started = Date.now();
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <section className="note rise" role="status" aria-live="polite" style={{ "--d": ".1s" } as CSSProperties}>
      <p className="flex flex-wrap items-center gap-3 font-semibold">
        <span className="dot talking" aria-hidden="true" />
        Researching
        <span className="num ml-auto text-[14px] font-normal opacity-75">
          {elapsed}s · usually 20 to 60 s
        </span>
      </p>
      <q className="mt-2 block text-[14px] opacity-80">{request}</q>
      <ol className="mt-3 grid gap-1.5 text-[15px]">
        <li>Reading your profile and the vendors on your roster.</li>
        <li>Searching the web for movers, internet, a dentist and your current providers, as your request and profile name them.</li>
        <li>Checking each source for a phone number, plans and discounts. A number is kept only when it appears on a page that was opened.</li>
        <li>Checking the regulations for the destination state.</li>
      </ol>
      <p className="mt-3 text-[14px] opacity-75">Nothing is called until you approve the proposal.</p>
    </section>
  );
}
