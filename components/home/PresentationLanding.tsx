"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Clouds } from "@/components/shell";
import { Brand } from "@/components/ui";
import { LandingFan } from "@/components/home/LandingFan";
import report from "@/public/eval-report.json";
import "./PresentationLanding.css";


const chapters = ["The idea", "The move", "The research", "The call", "Your decision", "The proof"];
const ids = ["idea", "move", "research", "call", "decision", "proof"];
const options = [
  { name: "Neighborhood Dental", type: "DENTIST", price: "$120", unit: "new-patient visit", detail: "Student rate requested · Thursday afternoon", action: "Call to book your appointment" },
  { name: "Campus Checking", type: "BANK", price: "$0", unit: "monthly fee if eligible", detail: "Verify student eligibility and account requirements", action: "Guide you through the bank’s secure application" },
  { name: "Sutro Fiber", type: "INTERNET", price: "$45", unit: "/ month · 12-month promo", detail: "Ask about installation fees and the renewal rate", action: "Call to arrange your internet plan" },
];

export function PresentationLanding() {
  const [active, setActive] = useState(0);
  const [presenting, setPresenting] = useState(false);
  const [selected, setSelected] = useState<string[]>([options[0].name]);
  const [approved, setApproved] = useState(false);
  const [line, setLine] = useState(0);
  const go = (index: number) => document.getElementById(ids[Math.max(0, Math.min(ids.length - 1, index))])?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });

  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) if (entry.isIntersecting) setActive(ids.indexOf(entry.target.id));
    }, { rootMargin: "-25% 0px -45% 0px" });
    ids.forEach((id) => { const el = document.getElementById(id); if (el) observer.observe(el); });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!presenting) return;
    const key = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable=true]")) return;
      if (["ArrowRight", "ArrowDown", "PageDown", " "].includes(event.key)) { event.preventDefault(); go(active + 1); }
      if (["ArrowLeft", "ArrowUp", "PageUp"].includes(event.key)) { event.preventDefault(); go(active - 1); }
      if (event.key === "Escape") setPresenting(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [active, presenting]);

  return (
    <div className={"pitch-landing"}>
      <a className={"pitch-skip"} href="#idea">Skip to presentation</a>
      <div className="sky sky-deep pitch-opening">
      <Clouds variant="hero" />
      <header className={"pitch-header"}>
        <Brand />
        <nav aria-label="Landing page"><a href="#research">How it works</a><a href="#proof">The proof</a></nav>
        <div className={"pitch-headerActions"}><button onClick={() => { setPresenting(!presenting); if (!presenting) go(active); }}>{presenting ? "Exit presentation" : "Present ↗"}</button><Link href="/login" className={"pitch-darkButton"}>Open app ↗</Link></div>
      </header>

        <section id="idea" className={`${"pitch-section"} ${"pitch-hero"}`}>
          <div className={"pitch-heroCopy"}><p className={"pitch-eyebrow"}><span className={"pitch-statusDot"} /> YOUR LIFE, WITHOUT THE HOLD MUSIC</p>
            <h1>New city.<br />Same you.<br /><em>We’ll make the calls.</em></h1>
            <p className={"pitch-lede"}>An AI concierge that researches your options, calls to confirm the details, and asks for a better deal. You choose. It takes care of the next step.</p>
            <div className={"pitch-actions"}><button className={"pitch-primary"} onClick={() => go(1)}>Follow one move <span>↗</span></button><a href="#proof" className={"pitch-textLink"}>Show me the proof →</a></div>
            <p className={"pitch-powered"}>BUILT AT SPEEDHACKS <span> / </span> VOICE BY <b>Dial</b> <span> / </span> ANALYTICS BY <b>Keewano</b></p>
          </div>
          <LandingFan />
          <div className={"pitch-heroBottom"}><span>Less life admin. More life.</span><a href="#move">SCROLL TO FOLLOW THE STORY ↓</a><span>01 / 06</span></div>
        </section>

      </div>
      <main>
        <section id="move" className={`${"pitch-section"} ${"pitch-move"}`}>
          <div><p className={"pitch-eyebrow"}>01 / START WITH YOUR LIFE</p><h2>Moving is exciting.<br /><em>The admin isn’t.</em></h2><p className={"pitch-lede"}>Your needs don’t fit in a search box. Your budget, current providers, student status, and schedule should travel with you.</p><div className={"pitch-tags"}><span>Student budget</span><span>Existing providers</span><span>New neighborhood</span><span>Your availability</span></div></div>
          <div className={"pitch-requestCard"}><div className={"pitch-cardTop"}><span>YOUR REQUEST</span><span>GA → SF</span></div><blockquote>“I’m moving from Georgia to San Francisco. I currently have a dentist and a bank back home. Find the best options for me as a student — and help me get set up.”</blockquote><div className={"pitch-requestResponse"}><span className={"pitch-miniMark"}>d.</span><p>Let’s find a dentist near you, compare student banking options, and get your new place connected.</p></div><small>Example journey · banking is a proposed extension of the current demo.</small></div>
        </section>

        <section id="research" className={`${"pitch-section"} ${"pitch-research"}`}>
          <div className={"pitch-sectionHeading"}><div><p className={"pitch-eyebrow"}>02 / RESEARCH BEFORE THE RING</p><h2>Find the options.<br /><em>Then check the fine print.</em></h2></div><p>Search the web for published prices and eligibility. Keep the sources. Treat anything missing as a question for the call.</p></div>
          <div className={"pitch-researchGrid"}>{[{ n: "01", title: "Understand you", body: "Bring your destination, existing services, budget, and student eligibility into every search.", chips: ["San Francisco", "Student", "Near campus"] }, { n: "02", title: "Find the evidence", body: "Research provider websites, attach citations, and verify phone numbers against source pages.", chips: ["Published price", "Source URL", "Verified number"] }, { n: "03", title: "Prepare the questions", body: "Ask about the all-in price, student discounts, availability, and any conditions the website leaves out.", chips: ["Hidden fees?", "Student rate?", "Promo expiry?"] }].map((item) => <article key={item.n}><span className={"pitch-stepNumber"}>{item.n}</span><h3>{item.title}</h3><p>{item.body}</p><div className={"pitch-tags"}>{item.chips.map((chip) => <span key={chip}>{chip}</span>)}</div></article>)}</div>
          <div className={"pitch-noteStrip"}><span>↳</span><p><b>A published price is a starting point.</b> A confirmed quote includes the fees, conditions, and what you actually qualify for.</p></div>
        </section>

        <section id="call" className={`${"pitch-section"} ${"pitch-call"}`}>
          <div><p className={"pitch-eyebrow"}>03 / DIAL DOES THE TALKING</p><h2>The best question?<br /><em>“Can you do better?”</em></h2><p className={"pitch-lede"}>Dormammu calls with context. It identifies itself as AI, confirms the quote, asks about eligible discounts, and brings the answer back to you.</p><ul className={"pitch-callPoints"}><li><span>01</span> Confirm the actual price and conditions</li><li><span>02</span> Ask for a student rate or fee waiver</li><li><span>03</span> Capture the answer in a readable transcript</li></ul><p className={"pitch-small"}>Current demo supports two concurrent real calls; overflow is labeled simulated.</p></div>
          <div className={"pitch-transcript"}><div className={"pitch-cardTop"}><b>Dial / call walkthrough</b><span>ILLUSTRATIVE</span></div><div className={"pitch-wave"} aria-hidden="true">{Array.from({ length: 44 }, (_, i) => <i key={i} style={{ height: `${12 + ((i * 17) % 41)}px` }} />)}</div><div className={"pitch-dialogue"} aria-live="polite"><p><small>DORMAMMU</small>Hi, I’m an AI assistant calling on behalf of a student moving to San Francisco. What’s your new-patient price?</p>{line >= 1 && <p><small>DENTAL OFFICE</small>The exam and cleaning are $150, before any additional treatment.</p>}{line >= 2 && <p className={"pitch-highlight"}><small>DORMAMMU</small>Is there a student discount? And does that price include X-rays?</p>}{line >= 3 && <p><small>DENTAL OFFICE</small>With student ID, it’s $120 including the initial X-rays. We have Thursday afternoon available.</p>}</div><button className={"pitch-transcriptButton"} onClick={() => setLine((previous) => previous === 3 ? 0 : previous + 1)}>{line === 3 ? "Replay walkthrough ↺" : "Next exchange →"}</button><small className={"pitch-small"}>Scripted example, not a live recording. Provider and prices are fictional.</small></div>
        </section>

        <section id="decision" className={`${"pitch-section"} ${"pitch-decision"}`}>
          <div className={"pitch-sectionHeading"}><div><p className={"pitch-eyebrow"}>04 / YOU HAVE THE FINAL SAY</p><h2>“Here’s what I found.<br /><em>Which ones do you want?”</em></h2></div><p>Choose what works for you. Then authorize the follow-up: book the appointment, arrange the plan, or complete a secure provider application.</p></div>
          <p className={"pitch-small"}>Interactive concept demo · all providers, prices, and availability below are fictional.</p>
          <div className={"pitch-optionGrid"}>{options.map((option) => <label key={option.name} className={`${"pitch-option"} ${selected.includes(option.name) ? "pitch-selected" : ""}`}><div className={"pitch-cardTop"}><span>{option.type}</span><input type="checkbox" checked={selected.includes(option.name)} onChange={() => { setApproved(false); setSelected((prev) => prev.includes(option.name) ? prev.filter((name) => name !== option.name) : [...prev, option.name]); }} /></div><h3>{option.name}</h3><strong className={"pitch-price"}>{option.price}</strong><span>{option.unit}</span><p>{option.detail}</p><div className={"pitch-optionAction"}>↳ {option.action}</div></label>)}</div>
          <div className={"pitch-approval"}><p role="status">{approved ? `Demo approval recorded for ${selected.length} ${selected.length === 1 ? "option" : "options"}. Next: confirm details and complete the authorized follow-up. No calls or bookings were made.` : "Your choice comes before any commitment."}</p><button disabled={!selected.length} className={"pitch-primary"} onClick={() => setApproved(true)}>{approved ? "Preview approved ✓" : `Preview approval (${selected.length}) →`}</button></div>
          <p className={"pitch-small"}>Appointments require a confirmed slot. Bank identity checks and final account opening stay with you and the provider.</p>
        </section>

        <section id="proof" className={`${"pitch-section"} ${"pitch-proof"}`}>
          <div className={"pitch-sectionHeading"}><div><p className={"pitch-eyebrow"}>05 / BUILT TO SHOW ITS WORK</p><h2>Real integrations.<br /><em>Inspectable evidence.</em></h2></div><p>Follow the chain from a phone call to a structured outcome to an event. Keep measured results separate from the product vision.</p></div>
          <div className={"pitch-metrics"}><div><strong>{report.n}</strong><span>fixture transcripts evaluated</span></div><div><strong>{Math.round(report.exact_rate * 100)}<em>%</em></strong><span>match on scored fields*</span></div><div><strong>{Object.keys(report.by_track).length}</strong><span>tracks: quote, schedule, negotiate</span></div><div><strong>{report.errors}</strong><span>extraction errors in this report</span></div></div>
          <div className={"pitch-proofGrid"}><article><div className={"pitch-cardTop"}><h3>Dial<span>↗</span></h3><span>VOICE EXECUTION</span></div><p>Outbound calls, status tracking, and transcripts feed the extraction and comparison workflow.</p><div className={"pitch-eventFlow"}><span>Dispatch</span> → <span>Transcript</span> → <span>Outcome</span></div><Link href="/calls">Inspect calls in the app ↗</Link></article><article><div className={"pitch-cardTop"}><h3>Keewano<span>↗</span></h3><span>EVENT ANALYTICS</span></div><p>The integration sends lifecycle events and typed metrics for call duration, outcomes, and run timing. Postgres retains the source record.</p><div className={"pitch-eventFlow"}><span>Event</span> → <span>Typed metric</span> → <span>Analysis</span></div><Link href="/evidence">Inspect integration evidence ↗</Link><small>Live delivery depends on a valid project API key; this page does not assert successful ingestion.</small></article></div>
          <p className={"pitch-footnote"}>*Saved fixture evaluation · {report.model} · October 11, 2026. Amount tolerance: $1; appointment scoring checks day and hour, not timezone offset. Caveats are not scored. This is not a live-call success rate. <a href="/eval-report.json" target="_blank" rel="noreferrer">Read the full report ↗</a> · App evidence requires sign-in.</p>
          <div className={"pitch-finalCta"}><h3>You make the move.<br /><em>We’ll handle the calls.</em></h3><Link className={"pitch-primary"} href="/login">Try Dormammu ↗</Link></div>
        </section>
      </main>
      <footer className={"pitch-footer"}><span>dormammu. / SpeedHacks 2026</span><button onClick={() => go(0)}>Back to the beginning ↑</button></footer>
      <nav className={"pitch-chapterNav"} aria-label="Presentation chapters"><button aria-label="Previous chapter" disabled={active === 0} onClick={() => go(active - 1)}>←</button><div>{chapters.map((chapter, index) => <button key={chapter} aria-label={`${index + 1}: ${chapter}`} aria-current={active === index ? "step" : undefined} onClick={() => go(index)}><span />{active === index && <b>{chapter}</b>}</button>)}</div><span className={"pitch-counter"}>{String(active + 1).padStart(2, "0")} / 06</span><button aria-label="Next chapter" disabled={active === 5} onClick={() => go(active + 1)}>→</button></nav>
      {presenting && <div className={"pitch-keyHint"}>PRESENTING · ← → to navigate · Esc to exit</div>}
    </div>
  );
}
