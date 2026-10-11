import type { Metadata } from "next";

import { AppNav } from "@/components/AppNav";
import { isSupabaseConfigured } from "@/lib/supabase/client";
import { getSupabaseServer } from "@/lib/supabase/server";

import "./globals.css";

export const metadata: Metadata = {
  title: "Dormammu",
  description: "One request. Every call at once. An AI that says so first.",
};

/** Session state for the nav's first paint. Reading cookies makes every route dynamic, which they are anyway. */
async function sessionState(): Promise<{ signedIn: boolean; email: string | null }> {
  if (!isSupabaseConfigured()) return { signedIn: true, email: null };
  const supabase = await getSupabaseServer();
  if (!supabase) return { signedIn: false, email: null };
  const { data } = await supabase.auth.getClaims();
  if (!data?.claims) return { signedIn: false, email: null };
  const claimEmail = (data.claims as { email?: unknown }).email;
  return { signedIn: true, email: typeof claimEmail === "string" ? claimEmail : null };
}

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const { signedIn, email } = await sessionState();
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-bg text-fg font-sans">
        <AppNav initialSignedIn={signedIn} email={email} />
        {children}
      </body>
    </html>
  );
}
