import Link from "next/link";
import type { CSSProperties } from "react";

import { Dashboard } from "@/components/home/Dashboard";
import { LandingFan } from "@/components/home/LandingFan";
import { Clouds } from "@/components/shell";
import { Brand } from "@/components/ui";
import { isSupabaseConfigured } from "@/lib/supabase/client";
import { getSupabaseServer } from "@/lib/supabase/server";
import { COHORTS } from "@/lib/types";

export const dynamic = "force-dynamic";

const STEPS = [
  {
    n: "01",
    title: "It learns your profile",
    body: "Student, or none of these. Your profile decides which discounts it asks for and what proof a vendor will want. It only claims what is on your profile.",
  },
  {
    n: "02",
    title: "It finds the best deals",
    body: "One request sends out every call at once. Each call asks for the rates you qualify for, so you see prices most people never get quoted.",
  },
  {
    n: "03",
    title: "You choose",
    body: "Options come back ranked while the calls are still live. Nothing is signed, booked or canceled until you approve it.",
  },
  {
    n: "04",
    title: "It executes and negotiates",
    body: "It calls back to lock in what you picked, and pushes on the price using the negotiation harness.",
  },
];

const HANDLES = [
  {
    title: "Compare quotes",
    body: "Internet, renters insurance, movers. Several vendors called in parallel and compared side by side.",
  },
  {
    title: "Book appointments",
    body: "Doctors, dentists and repair shops that only book by phone. Dormammu checks when you are free and locks in a time.",
  },
  {
    title: "Negotiate and cancel",
    body: "Calls to ask for a better rate, or to cancel the service you stopped using months ago.",
  },
];

const HARNESS = [
  ["Reaches the right desk", "It gets to the retention team, where the discounts are, instead of general customer service."],
  ["Asks for your rate first", "The rate your profile qualifies for is asked for on every call, before any deal is closed."],
  ["Asks for the best price, then waits", "It asks what the lowest available rate is and stays quiet. That silence costs nothing and moves numbers."],
  ["Stays honest", "It says it is an AI first. It only cites real advertised prices and never invents a competing offer, a hardship or a problem with the service."],
  ["Knows when to stop", "Two escalations at most. Any offer that comes with conditions is brought back to you, not accepted."],
];

const MARQUEE = ["internet quotes", "renters insurance", "a dentist near campus", "movers", "the ISP you forgot to cancel", "the bill that went up"];

const d = (s: string) => ({ "--d": s }) as CSSProperties;

/** Signed-out landing: frontend/index.html, with every call to action going to /login. */
function Landing() {
  return (
    <>
      <header className="sky sky-deep">
        <Clouds variant="hero" />
        <div className="wrap">
          <nav className="nav rise" aria-label="Main">
            <Brand />
            <div className="nav-links">
              <a href="#how">How it works</a>
              <a href="#handles">What it handles</a>
              <a href="#negotiation">Negotiation</a>
              <a href="#ledger">Ledger</a>
            </div>
            <div className="nav-end">
              <Link href="/login">Sign in</Link>
              <Link className="btn btn-primary btn-sm" href="/login">
                Get started
              </Link>
            </div>
          </nav>

          <div className="hero">
            <h1 className="rise" style={d(".1s")}>
              One request.
              <br />
              Every call at once.
            </h1>
            <p className="lede rise" style={d(".25s")}>
              Dormammu knows who it is calling for. It calls every vendor at once to find the best deals you qualify for
              and shows you the options while the calls are still live. It says it is an AI before anything else. Once you
              approve, it closes the deal and negotiates the price for you.
            </p>
            <LandingFan />
          </div>
        </div>
      </header>

      <main className="sheet">
        <div className="statement">
          <div className="wrap">
            <p className="reveal">
              Finding a good deal means calling around. <strong>Getting it means knowing what to ask for, and negotiating.</strong>{" "}
              Dormammu does both, and nothing goes ahead until you say yes.
            </p>
          </div>
        </div>

        <div className="marquee" aria-hidden="true">
          <div>
            {[...MARQUEE, ...MARQUEE].map((m, i) => (
              <span key={i}>{m}</span>
            ))}
          </div>
        </div>

        <section className="section" id="how">
          <div className="wrap">
            <span className="eyebrow">How it works</span>
            <h2 className="reveal">It finds the deals. You approve. It closes them.</h2>
            <ol className="steps-list">
              {STEPS.map((s) => (
                <li key={s.n} className="reveal">
                  <span className="n">{s.n}</span>
                  <h3>{s.title}</h3>
                  <p>{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="section" id="handles">
          <div className="wrap">
            <span className="eyebrow">What it handles</span>
            <ul className="handles">
              {HANDLES.map((h) => (
                <li key={h.title} className="reveal">
                  <h3>{h.title}</h3>
                  <p>{h.body}</p>
                </li>
              ))}
            </ul>
            <p className="handles-note reveal">
              Nobody picks up? Dormammu carries the same conversation over text instead of leaving a &ldquo;call us back&rdquo;.
            </p>
          </div>
        </section>

        <section className="section" id="negotiation">
          <div className="wrap">
            <span className="eyebrow">The negotiation harness</span>
            <h2 className="reveal">It negotiates the way the research says works.</h2>
            <ul className="handles tight">
              {HARNESS.map(([title, body]) => (
                <li key={title} className="reveal">
                  <h3>{title}</h3>
                  <p>{body}</p>
                </li>
              ))}
            </ul>
            <p className="handles-note reveal">
              The harness is a step-by-step playbook built from published research on bill negotiation and tested before it
              calls for you.
            </p>
          </div>
        </section>

        <section className="section" id="ledger">
          <div className="wrap duo">
            <div className="copy reveal">
              <span className="eyebrow">The ledger</span>
              <h2>It remembers every account, so you don&rsquo;t have to.</h2>
              <p>
                Each call writes down what was agreed: the vendor, the rate, when the promo ends, when it renews. That list is
                yours, in one place.
              </p>
              <p>When a date comes up, Dormammu asks first. You say yes, it makes the call.</p>
            </div>
            <div className="thread" aria-label="Example conversation">
              <small className="reveal">Eleven months later</small>
              <p className="bubble reveal">
                Your Sutro Fiber promo ends in 21 days. Your bill goes from $50 to $70 a month. Want me to call and ask them to
                keep the rate?
              </p>
              <p className="bubble me reveal">Yes, call them.</p>
              <p className="bubble reveal">Done. They kept you at $50 for another 12 months. You will need to re-verify your .edu email.</p>
              <small className="reveal">Example. The vendor and prices are made up.</small>
            </div>
          </div>
        </section>

        <section className="section" id="profile">
          <div className="wrap duo">
            <div className="copy reveal">
              <span className="eyebrow">Your profile</span>
              <h2>It asks for what you actually qualify for.</h2>
              <p>
                Many discounts exist but only get applied when someone asks. Dormammu knows your profile, asks on every call,
                and only ever claims what is true about you.
              </p>
              <p>You can read exactly what the agent is told to ask for.</p>
            </div>
            <ul className="asks reveal" aria-label="Asked for on every call">
              {COHORTS[0].asks.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </div>
        </section>
      </main>

      <footer className="sky sky-dusk closing">
        <Clouds variant="closing" />
        <div className="wrap">
          <h2 className="reveal">Hand off the phone calls.</h2>
          <p className="reveal">Sign in with your email and password, or create an account.</p>
          <div className="hero-cta reveal">
            <Link className="btn btn-primary" href="/login">
              Get started
            </Link>
            <Link className="btn btn-glass" href="/login">
              Sign in
            </Link>
          </div>

          <div className="footer">
            <Brand />
            <nav aria-label="Footer">
              <a href="#how">How it works</a>
              <a href="#ledger">Ledger</a>
              <Link href="/login">Sign in</Link>
            </nav>
            <span className="fine">Built at SpeedHacks, Oct 2026.</span>
          </div>
        </div>
      </footer>
    </>
  );
}

export default async function Home() {
  const devMode = !isSupabaseConfigured();
  let signedIn = devMode;
  let email: string | null = null;

  const supabase = await getSupabaseServer();
  if (supabase) {
    const { data } = await supabase.auth.getClaims();
    if (data?.claims) {
      signedIn = true;
      const claimEmail = (data.claims as { email?: unknown }).email;
      email = typeof claimEmail === "string" ? claimEmail : null;
    }
  }

  if (!signedIn) return <Landing />;
  return <Dashboard email={email} />;
}
