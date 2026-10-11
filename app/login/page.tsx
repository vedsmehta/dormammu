"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Brand, Button, ErrorNote, Field, inputClass, Panel } from "@/components/ui";
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
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <div>
                <h1 className="text-lg font-semibold">{mode === "signin" ? "Sign in" : "Create account"}</h1>
                <p className="mt-1 text-sm text-muted">
                  {mode === "signin"
                    ? "Email and password."
                    : "Pick a password. You are signed in right away, nothing to confirm."}
                </p>
              </div>
              <Field label="email">
                <input
                  type="email"
                  required
                  autoFocus
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@stanford.edu"
                  className={inputClass}
                />
              </Field>
              <Field
                label="password"
                hint={mode === "signup" ? `at least ${MIN_PASSWORD} characters` : undefined}
              >
                <input
                  type="password"
                  required
                  minLength={MIN_PASSWORD}
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={inputClass}
                />
              </Field>
              {mode === "signup" ? (
                <Field label="name" hint="optional, shown on your profile">
                  <input
                    type="text"
                    autoComplete="name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              ) : null}
              <ErrorNote message={error} />
              <Button type="submit" disabled={!canSubmit} className="w-full">
                {busy
                  ? mode === "signin"
                    ? "signing in…"
                    : "creating account…"
                  : mode === "signin"
                    ? "Sign in"
                    : "Create account"}
              </Button>
              <p className="text-center text-xs text-dim">
                {mode === "signin" ? (
                  <>
                    New here?{" "}
                    <button
                      type="button"
                      className="text-accent hover:underline"
                      onClick={() => switchMode("signup")}
                    >
                      Create an account
                    </button>
                  </>
                ) : (
                  <>
                    Already have an account?{" "}
                    <button
                      type="button"
                      className="text-accent hover:underline"
                      onClick={() => switchMode("signin")}
                    >
                      Sign in
                    </button>
                  </>
                )}
              </p>
            </form>
          )}
        </Panel>
      </div>
    </main>
  );
}
