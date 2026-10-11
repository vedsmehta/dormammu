import Link from "next/link";

import { Dashboard } from "@/components/home/Dashboard";
import { Brand } from "@/components/ui";
import { isSupabaseConfigured } from "@/lib/supabase/client";
import { getSupabaseServer } from "@/lib/supabase/server";

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

/** Signed-out landing: the prototype's index.html copy, with every call to action going to /login. */
function Landing() {
  return (
    <main className="flex-1">
      <section className="border-b border-line bg-gradient-to-b from-teal-deep/30 to-bg">
        <div className="mx-auto w-full max-w-5xl px-4 sm:px-6">
          <nav className="flex items-center justify-between py-5" aria-label="Main">
            <Brand />
            <div className="flex items-center gap-4 text-sm">
              <a href="#how" className="hidden text-muted hover:text-fg sm:inline">
                How it works
              </a>
              <a href="#handles" className="hidden text-muted hover:text-fg sm:inline">
                What it handles
              </a>
              <Link href="/login" className="rounded-full bg-mint px-4 py-2 text-sm font-medium text-bg hover:bg-white">
                Sign in
              </Link>
            </div>
          </nav>
          <div className="animate-rise py-16 text-center sm:py-24">
            <h1 className="font-display text-5xl font-semibold leading-[1.02] tracking-tight sm:text-7xl">
              One request.
              <br />
              Every call at once.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-base text-muted sm:text-lg">
              Dormammu knows who it is calling for. It calls every vendor at once to find the best deals you
              qualify for and shows you the options while the calls are still live. It says it is an AI
              before anything else. Once you approve, it closes the deal and negotiates the price for you.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <Link href="/login" className="rounded-full bg-mint px-5 py-3 text-base font-medium text-bg hover:bg-white">
                Get started
              </Link>
              <Link
                href="/login"
                className="rounded-full border border-line bg-panel px-5 py-3 text-base text-fg hover:border-muted"
              >
                Sign in
              </Link>
            </div>
          </div>
        </div>
      </section>

      <div className="mx-auto w-full max-w-5xl px-4 sm:px-6">
        <p className="mx-auto max-w-3xl py-16 text-center font-display text-2xl font-medium leading-snug tracking-tight text-muted sm:text-3xl">
          Finding a good deal means calling around.{" "}
          <strong className="font-medium text-fg">Getting it means knowing what to ask for, and negotiating.</strong>{" "}
          Dormammu does both, and nothing goes ahead until you say yes.
        </p>

        <section id="how" className="border-t border-line py-14">
          <span className="font-mono text-xs uppercase tracking-widest text-teal">How it works</span>
          <h2 className="mt-3 max-w-xl font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            It finds the deals. You approve. It closes them.
          </h2>
          <ol className="mt-8 divide-y divide-line border-y border-line">
            {STEPS.map((s) => (
              <li key={s.n} className="grid gap-3 py-6 sm:grid-cols-[6rem_1fr_2fr] sm:gap-6">
                <span className="font-display text-4xl font-light tracking-tight text-teal">{s.n}</span>
                <h3 className="font-display text-xl font-semibold tracking-tight">{s.title}</h3>
                <p className="text-muted">{s.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section id="handles" className="border-t border-line py-14">
          <span className="font-mono text-xs uppercase tracking-widest text-teal">What it handles</span>
          <ul className="mt-6 divide-y divide-line border-y border-line">
            {HANDLES.map((h) => (
              <li key={h.title} className="grid gap-2 py-6 sm:grid-cols-2 sm:gap-6">
                <h3 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">{h.title}</h3>
                <p className="text-muted">{h.body}</p>
              </li>
            ))}
          </ul>
        </section>

        <section id="negotiation" className="border-t border-line py-14">
          <span className="font-mono text-xs uppercase tracking-widest text-teal">The negotiation harness</span>
          <h2 className="mt-3 max-w-xl font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            It negotiates the way the research says works.
          </h2>
          <ul className="mt-8 divide-y divide-line border-y border-line">
            {HARNESS.map(([title, body]) => (
              <li key={title} className="grid gap-2 py-5 sm:grid-cols-2 sm:gap-6">
                <h3 className="font-display text-xl font-semibold tracking-tight">{title}</h3>
                <p className="text-muted">{body}</p>
              </li>
            ))}
          </ul>
          <p className="mt-6 text-sm text-muted">
            The harness is a step-by-step playbook built from published research on bill negotiation and tested
            before it calls for you.
          </p>
        </section>

        <section id="ledger" className="grid gap-8 border-t border-line py-14 sm:grid-cols-2">
          <div>
            <span className="font-mono text-xs uppercase tracking-widest text-teal">The ledger</span>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight">
              It remembers every account, so you don&rsquo;t have to.
            </h2>
            <p className="mt-4 text-muted">
              Each call writes down what was agreed: the vendor, the rate, when the promo ends, when it renews.
              That list is yours, in one place.
            </p>
            <p className="mt-3 text-muted">When a date comes up, Dormammu asks first. You say yes, it makes the call.</p>
          </div>
          <div>
            <span className="font-mono text-xs uppercase tracking-widest text-teal">Your profile</span>
            <h2 className="mt-3 font-display text-3xl font-semibold tracking-tight">It asks for what you actually qualify for.</h2>
            <p className="mt-4 text-muted">
              Many discounts exist but only get applied when someone asks. Dormammu knows your profile, asks on every
              call, and only ever claims what is true about you. You can read exactly what the agent is told to ask for.
            </p>
          </div>
        </section>

        <section className="border-t border-line py-16 text-center">
          <h2 className="font-display text-3xl font-semibold tracking-tight sm:text-5xl">Hand off the phone calls.</h2>
          <p className="mt-3 text-muted">Sign in with your email and password, or create an account.</p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Link href="/login" className="rounded-full bg-mint px-5 py-3 text-base font-medium text-bg hover:bg-white">
              Get started
            </Link>
            <Link href="/login" className="rounded-full border border-line bg-panel px-5 py-3 text-base text-fg hover:border-muted">
              Sign in
            </Link>
          </div>
          <footer className="mt-16 flex flex-wrap items-center justify-between gap-4 border-t border-line pt-6 text-left text-xs text-dim">
            <Brand />
            <span>Built at SpeedHacks, Oct 2026.</span>
          </footer>
        </section>
      </div>
    </main>
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
