"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";

/** Signed-in email with a sign-out control. Shows a dev-mode marker when auth is off. */
export function UserChip({ email: initialEmail = null }: { email?: string | null }) {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(initialEmail);

  useEffect(() => {
    const supabase = getSupabaseBrowser();
    if (!supabase) return;
    let cancelled = false;
    supabase.auth.getSession().then(({ data }) => {
      if (!cancelled && data.session?.user.email) setEmail(data.session.user.email);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!isSupabaseConfigured()) {
    return (
      <span className="font-mono text-xs text-warn" title="NEXT_PUBLIC_SUPABASE_URL is empty: no auth, Python uses DEV_USER_ID">
        dev mode · no auth
      </span>
    );
  }

  const signOut = async () => {
    const supabase = getSupabaseBrowser();
    if (supabase) await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  return (
    <span className="flex items-center gap-3 font-mono text-xs text-muted">
      <span className="truncate max-w-[14rem]">{email ?? "signed in"}</span>
      <button type="button" onClick={signOut} className="text-dim hover:text-fg">
        sign out
      </button>
    </span>
  );
}
