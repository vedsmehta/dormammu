import { Dashboard } from "@/components/home/Dashboard";
import { PresentationLanding } from "@/components/home/PresentationLanding";
import { isSupabaseConfigured } from "@/lib/supabase/client";
import { getSupabaseServer } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

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

  if (!signedIn) return <PresentationLanding />;
  return <Dashboard email={email} />;
}
