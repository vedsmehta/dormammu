"use client";

import Link from "next/link";
import { useState } from "react";

import { Brand, Button, ErrorNote, Field, inputClass, Panel } from "@/components/ui";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const supabase = getSupabaseBrowser();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    const { error: err } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: { emailRedirectTo: `${window.location.origin}/auth/callback` },
    });
    setBusy(false);
    if (err) {
      setError(err.message);
      return;
    }
    setSent(true);
  };

  return (
    <main className="flex flex-1 flex-col items-center justify-center px-4 py-16">
      <div className="w-full max-w-sm space-y-6">
        <div className="flex items-center justify-between">
          <Brand />
          <Link href="/" className="font-mono text-xs text-dim hover:text-fg">
            back
          </Link>
        </div>
        <Panel className="p-6">
          {!isSupabaseConfigured() ? (
            <div className="space-y-3">
              <h1 className="text-lg font-semibold">Dev mode</h1>
              <p className="text-sm text-muted">
                NEXT_PUBLIC_SUPABASE_URL is empty, so there is no sign-in. The Python service uses
                DEV_USER_ID for every request.
              </p>
              <Link href="/" className="text-sm text-accent hover:underline">
                Go to the app
              </Link>
            </div>
          ) : sent ? (
            <div className="space-y-3">
              <h1 className="text-lg font-semibold">Check your email</h1>
              <p className="text-sm text-muted">
                A magic link went to <span className="font-mono text-fg">{email}</span>. Open it on this
                device to finish signing in.
              </p>
              <button type="button" className="text-xs text-dim hover:text-fg" onClick={() => setSent(false)}>
                use a different address
              </button>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <div>
                <h1 className="text-lg font-semibold">Sign in</h1>
                <p className="mt-1 text-sm text-muted">Magic link. No password.</p>
              </div>
              <Field label="email">
                <input
                  type="email"
                  required
                  autoFocus
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@stanford.edu"
                  className={inputClass}
                />
              </Field>
              <ErrorNote message={error} />
              <Button type="submit" disabled={busy || email.trim().length === 0} className="w-full">
                {busy ? "sending…" : "Send magic link"}
              </Button>
            </form>
          )}
        </Panel>
      </div>
    </main>
  );
}
