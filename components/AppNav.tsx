"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/ui";
import { initials } from "@/lib/format";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/ledger", label: "Ledger" },
  { href: "/calendar", label: "Calendar" },
  { href: "/insights", label: "Insights" },
  { href: "/evidence", label: "Evidence" },
  { href: "/profile", label: "Profile" },
];

// The tab bar icons from the prototype's app pages, in the same order as LINKS.
const ICONS = [
  "M4 11l8-7 8 7v8a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z",
  "M6 4h3l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v3a2 2 0 0 1-2 2A15 15 0 0 1 4 6a2 2 0 0 1 2-2z",
  "M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01",
  "M4 20v-7M10 20V5M16 20v-10M22 20H2",
  "M12 3l8 3v6c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V6zM9 12l2 2 4-4",
  "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20a8 8 0 0 1 16 0",
];

// Pages that carry their own chrome (the auth flow) and never show the signed-in nav.
const HIDDEN_ON = ["/login", "/auth", "/onboarding"];

/**
 * The signed-in nav from the prototype's app pages: brand, glass pill of links, avatar. The server
 * layout passes the initial session state so the first paint is right; the browser client then
 * follows sign-in and sign-out events. In dev mode (no NEXT_PUBLIC_SUPABASE_URL) it always renders.
 * It sits over the top edge of each page's sky header (.app-nav), and on small screens the same
 * links repeat as the fixed bottom tab bar.
 */
export function AppNav({ initialSignedIn, email: initialEmail }: { initialSignedIn: boolean; email: string | null }) {
  const pathname = usePathname();
  const router = useRouter();
  const devMode = !isSupabaseConfigured();
  const [signedIn, setSignedIn] = useState(devMode || initialSignedIn);
  const [email, setEmail] = useState<string | null>(initialEmail);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const supabase = getSupabaseBrowser();
    if (!supabase) return;
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setSignedIn(Boolean(session));
      setEmail(session?.user.email ?? null);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  const hidden = HIDDEN_ON.some((p) => pathname === p || pathname.startsWith(p + "/"));
  if (hidden || !signedIn) return null;

  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    const supabase = getSupabaseBrowser();
    if (supabase) await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  const current = (href: string) => (href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(href + "/"));

  return (
    <>
      <div className="app-nav">
        <nav className="wrap nav rise" aria-label="Main">
          <Brand />
          <div className="nav-links">
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} aria-current={current(l.href) ? "page" : undefined}>
                {l.label}
              </Link>
            ))}
          </div>
          <div className="nav-end">
            {devMode ? (
              <span className="glass-pill sm" title="NEXT_PUBLIC_SUPABASE_URL is empty: no auth, Python uses DEV_USER_ID">
                dev mode · no auth
              </span>
            ) : (
              <button type="button" onClick={signOut} disabled={busy}>
                {busy ? "Signing out…" : "Sign out"}
              </button>
            )}
            <Link className="avatar" href="/profile" aria-label={email ? `${email}, profile` : "Profile"} title={email ?? undefined}>
              {initials(null, email)}
            </Link>
          </div>
        </nav>
      </div>
      <nav className="tabbar" aria-label="Sections">
        {LINKS.map((l, i) => (
          <Link key={l.href} href={l.href} aria-current={current(l.href) ? "page" : undefined}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d={ICONS[i]} />
            </svg>
            {l.label}
          </Link>
        ))}
      </nav>
    </>
  );
}
