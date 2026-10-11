"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/ui";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/calls", label: "Calls" },
  { href: "/ledger", label: "Ledger" },
  { href: "/profile", label: "Profile" },
];

// Pages that carry their own chrome and never show the signed-in nav.
const HIDDEN_ON = ["/login", "/auth"];

/**
 * The persistent signed-in nav from the prototype's app pages. The server layout passes the
 * initial session state so the first paint is right; the browser client then follows sign-in and
 * sign-out events. In dev mode (no NEXT_PUBLIC_SUPABASE_URL) it always renders.
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
    <nav aria-label="Main" className="border-b border-line bg-bg/80 backdrop-blur">
      <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <Brand />
        <div className="flex items-center gap-1 rounded-full border border-line bg-panel px-1 py-1 text-sm">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={l.href}
              aria-current={current(l.href) ? "page" : undefined}
              className={`rounded-full px-3 py-1 transition ${
                current(l.href) ? "bg-fg font-medium text-bg" : "text-muted hover:text-fg"
              }`}
            >
              {l.label}
            </Link>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-3 font-mono text-xs text-muted">
          {devMode ? (
            <span className="text-warn" title="NEXT_PUBLIC_SUPABASE_URL is empty: no auth, Python uses DEV_USER_ID">
              dev mode · no auth
            </span>
          ) : (
            <>
              <span className="hidden max-w-[14rem] truncate sm:inline">{email ?? "signed in"}</span>
              <button type="button" onClick={signOut} disabled={busy} className="text-dim hover:text-fg disabled:opacity-50">
                {busy ? "signing out…" : "sign out"}
              </button>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
