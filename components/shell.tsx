import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

/*
  The page chrome from the prototype: the teal sky with drifting mist, the paper sheet that slides
  up over it, and the auth flow's narrower sheet. Markup follows frontend/*.html one to one.
*/

/** The SVG filter every .cloud references. Rendered once by the layout. */
export function CloudDefs() {
  return (
    <svg className="cloud-defs" aria-hidden="true">
      <filter id="cloud">
        <feTurbulence type="fractalNoise" baseFrequency="0.011" numOctaves="4" seed="7" />
        <feDisplacementMap in="SourceGraphic" scale="150" />
      </filter>
    </svg>
  );
}

type Drift = { thin?: boolean; style: CSSProperties };

// The drift placements from each prototype page, verbatim.
const DRIFTS: Record<"hero" | "closing" | "auth" | "app", Drift[]> = {
  hero: [
    { thin: true, style: { left: "-6%", top: 120, width: 520, height: 150, animationDuration: "80s" } },
    { thin: true, style: { right: "-8%", top: 260, width: 600, height: 170, animationDuration: "95s", animationDirection: "alternate-reverse" } },
    { style: { left: "-12%", bottom: 150, width: 720, height: 210, animationDuration: "70s" } },
    { style: { right: "-14%", bottom: 90, width: 780, height: 230, animationDuration: "88s", animationDirection: "alternate-reverse" } },
    { style: { left: "28%", bottom: -70, width: 680, height: 190, animationDuration: "64s" } },
  ],
  closing: [
    { style: { left: "-10%", top: 240, width: 700, height: 200, animationDuration: "74s" } },
    { style: { right: "-12%", top: 300, width: 760, height: 220, animationDuration: "90s", animationDirection: "alternate-reverse" } },
    { thin: true, style: { left: "30%", bottom: 40, width: 640, height: 170, animationDuration: "66s" } },
  ],
  auth: [
    { style: { left: "-12%", top: 60, width: 640, height: 190, animationDuration: "76s" } },
    { thin: true, style: { right: "-14%", top: 260, width: 720, height: 210, animationDuration: "92s", animationDirection: "alternate-reverse" } },
  ],
  app: [
    { style: { left: "-12%", top: 70, width: 680, height: 200, animationDuration: "76s" } },
    { thin: true, style: { right: "-14%", top: 240, width: 760, height: 220, animationDuration: "92s", animationDirection: "alternate-reverse" } },
  ],
};

export function Clouds({ variant }: { variant: keyof typeof DRIFTS }) {
  return (
    <div className="clouds" aria-hidden="true">
      {DRIFTS[variant].map((d, i) => (
        <div key={i} className={d.thin ? "drift thin" : "drift"} style={d.style}>
          <i className="cloud" />
        </div>
      ))}
    </div>
  );
}

/**
 * A signed-in page: the sky header (the layout's AppNav sits over its top edge), then the paper
 * sheet with the grab handle. `head` is the hero content (.dash-top, .pulse, .page-title, .who-head).
 */
export function AppShell({ head, children, wide }: { head: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <div className="app">
      <header className="sky sky-deep app-head">
        <Clouds variant="app" />
        <div className="wrap">{head}</div>
      </header>
      <main className={`sheet to-end grab sheet-up${wide ? " wide" : ""}`} style={{ "--d": ".3s" } as CSSProperties}>
        <div className="wrap">{children}</div>
      </main>
    </div>
  );
}

/** The auth flow (login.html, onboarding.html, ready.html): centered brand, then the narrow sheet. */
export function AuthShell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="sky sky-deep auth">
      <Clouds variant="auth" />
      <header className="auth-top rise">
        <Link className="brand" href="/">
          Dormammu
        </Link>
        <p>One request. Every call at once.</p>
      </header>
      <main className={`auth-sheet sheet-up${wide ? " wide" : ""}`}>{children}</main>
    </div>
  );
}

const STEPS = ["Sign in", "Profile", "Ready"] as const;

/** The three-step indicator at the top of every auth sheet. */
export function Progress({ step }: { step: 0 | 1 | 2 | 3 }) {
  return (
    <ol className="progress" aria-label="Sign-in progress">
      {STEPS.map((label, i) => (
        <li key={label} className={i < step ? "done" : i === step ? "current" : undefined} aria-current={i === step ? "step" : undefined}>
          {label}
        </li>
      ))}
    </ol>
  );
}

/** The page head on calls, ledger and a single call: optional back link, big title, lede. */
export function PageTitle({
  title,
  lede,
  back,
  children,
}: {
  title: ReactNode;
  lede?: ReactNode;
  back?: { href: string; label: string };
  children?: ReactNode;
}) {
  return (
    <div className="page-title">
      {back ? (
        <Link className="back rise" href={back.href}>
          ← {back.label}
        </Link>
      ) : null}
      <h1 className="rise" style={{ "--d": ".1s" } as CSSProperties}>
        {title}
      </h1>
      {lede ? (
        <p className="rise" style={{ "--d": ".2s" } as CSSProperties}>
          {lede}
        </p>
      ) : null}
      {children}
    </div>
  );
}
