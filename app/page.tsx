import Link from "next/link";

import { Dashboard } from "@/components/home/Dashboard";
import { Brand } from "@/components/ui";
import { isSupabaseConfigured } from "@/lib/supabase/client";
import { getSupabaseServer } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

function Landing() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-xl space-y-8">
        <Brand />
        <div className="space-y-4">
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">
            Five phone calls. One sentence. Forty seconds.
          </h1>
          <p className="text-base text-muted">
            Dormammu fans a request out to the people you would otherwise call one at a time, says
            it is an AI before anything else, and brings back the ranked answers while the calls are
            still live.
          </p>
        </div>
        <Link
          href="/login"
          className="inline-flex items-center rounded-md bg-fg px-4 py-2 text-sm font-medium text-bg hover:bg-white"
        >
          Sign in with a magic link
        </Link>
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
