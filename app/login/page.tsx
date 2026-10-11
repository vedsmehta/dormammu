"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { AuthShell, Progress } from "@/components/shell";
import { ErrorNote, Field } from "@/components/ui";
import { ApiError, getVendors, signup } from "@/lib/api";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";

type Mode = "signin" | "signup";

const MIN_PASSWORD = 8;

/** Pull the JSON "detail" out of a FastAPI error body, else fall back to the raw text. */
function apiMessage(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === "string") return parsed.detail;
    } catch {
      // not JSON; fall through
    }
    return err.body.slice(0, 200) || `request failed (${err.status})`;
  }
  if (err instanceof Error) return err.message;
  return "something went wrong";
}

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = email.trim().length > 0 && password.length >= MIN_PASSWORD && !busy;

  /** Once a session cookie exists, send first-timers to onboarding and everyone else home. */
  const routeAfterSignIn = async () => {
    let dest = "/";
    try {
      const vendors = await getVendors();
      if (vendors.length === 0) dest = "/onboarding";
    } catch {
      // The API being unreachable must not block sign-in; the home page links to onboarding.
    }
    router.replace(dest);
    router.refresh();
  };

  const signIn = async (): Promise<boolean> => {
    const supabase = getSupabaseBrowser();
    if (!supabase) return false;
    const { error: err } = await supabase.auth.signInWithPassword({
      email: email.trim().toLowerCase(),
      password,
    });
    if (err) {
      setError(err.message);
      return false;
    }
    return true;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") {
        try {
          await signup(email.trim().toLowerCase(), password, displayName.trim() || undefined);
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) {
            setError("That account already exists. Switch to sign in.");
          } else {
            setError(apiMessage(err));
          }
          return;
        }
      }
      if (await signIn()) await routeAfterSignIn();
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setError(null);
  };

  return (
    <AuthShell>
      <Progress step={0} />

      {!isSupabaseConfigured() ? (
        <>
          <h1>Dev mode</h1>
          <p>NEXT_PUBLIC_SUPABASE_URL is empty, so there is no sign-in. The Python service uses DEV_USER_ID for every request.</p>
          <Link className="btn btn-primary btn-block" href="/">
            Go to the app
          </Link>
        </>
      ) : (
        <form onSubmit={submit}>
          <h1>{mode === "signin" ? "Sign in" : "Create an account"}</h1>
          <p>{mode === "signin" ? "Your email and password." : "Pick a password. You are signed in right away, nothing to confirm."}</p>

          <Field label="Email" hint="Have a university email? Use it. It doubles as proof for student rates.">
            <input
              type="email"
              required
              autoFocus
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@university.edu"
            />
          </Field>
          <Field label="Password" hint={mode === "signup" ? `At least ${MIN_PASSWORD} characters.` : undefined}>
            <input
              type="password"
              required
              minLength={MIN_PASSWORD}
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </Field>
          {mode === "signup" ? (
            <Field label="Name the agent calls on behalf of" hint="Optional, shown on your profile.">
              <input type="text" autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Maya Patel" />
            </Field>
          ) : null}
          <ErrorNote message={error} />
          <button type="submit" className="btn btn-primary btn-block" disabled={!canSubmit}>
            {busy ? (mode === "signin" ? "Signing in…" : "Creating your account…") : mode === "signin" ? "Sign in" : "Create account"}
          </button>
          <p className="auth-alt">
            {mode === "signin" ? (
              <>
                New here?{" "}
                <button type="button" onClick={() => switchMode("signup")}>
                  Create an account
                </button>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <button type="button" onClick={() => switchMode("signin")}>
                  Sign in
                </button>
              </>
            )}
          </p>
        </form>
      )}
    </AuthShell>
  );
}
