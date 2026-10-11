"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { AuthShell, Progress } from "@/components/shell";
import { ErrorNote, Field } from "@/components/ui";
import { ApiError, getVendors, signup } from "@/lib/api";
import { describeError } from "@/lib/format";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";
import { checkEmail, checkPassword, collect, focusFirstInvalid, type Errors } from "@/lib/validate";

type Mode = "signin" | "signup";

const MIN_PASSWORD = 8;

/** Supabase's sign-in errors, reworded so they say what to do next. */
function signInMessage(message: string): string {
  if (/invalid login credentials/i.test(message)) return "That email and password don't match an account. Check both, or create an account.";
  if (/email not confirmed/i.test(message)) return "That email has not been confirmed yet.";
  if (/rate limit|too many/i.test(message)) return "Too many attempts. Wait a minute, then try again.";
  if (/fetch|network/i.test(message)) return "Could not reach the sign-in service. Check your connection, then try again.";
  return message;
}

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<Errors>({});
  const formRef = useRef<HTMLFormElement>(null);

  /** Typing in a field clears its message; the next submit checks it again. */
  const clear = (key: string) => setErrors((prev) => (key in prev ? Object.fromEntries(Object.entries(prev).filter(([k]) => k !== key)) : prev));

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
      setError(signInMessage(err.message));
      return false;
    }
    return true;
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    // Sign-in only needs a password to be present; the length rule is for new accounts.
    const found = collect({
      email: checkEmail(email),
      password: checkPassword(password, mode === "signup" ? MIN_PASSWORD : 1),
      displayName: mode === "signup" && displayName.trim().length > 120 ? "Keep the name under 120 characters." : null,
    });
    setErrors(found);
    if (Object.keys(found).length) {
      setError(null);
      focusFirstInvalid(formRef.current);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "signup") {
        try {
          await signup(email.trim().toLowerCase(), password, displayName.trim() || undefined);
        } catch (err) {
          if (err instanceof ApiError && err.status === 409) {
            setErrors({ email: "An account with this email already exists. Sign in instead." });
            focusFirstInvalid(formRef.current);
          } else {
            setError(describeError(err));
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
    setErrors({});
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
        <form ref={formRef} onSubmit={submit} noValidate>
          <h1>{mode === "signin" ? "Sign in" : "Create an account"}</h1>
          <p>{mode === "signin" ? "Your email and password." : "Pick a password. You are signed in right away, nothing to confirm."}</p>

          <Field label="Email" hint="Have a university email? Use it. It doubles as proof for student rates." error={errors.email}>
            <input
              type="email"
              required
              autoFocus
              autoComplete="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                clear("email");
              }}
              placeholder="you@university.edu"
            />
          </Field>
          <Field label="Password" hint={mode === "signup" ? `At least ${MIN_PASSWORD} characters.` : undefined} error={errors.password}>
            <input
              type="password"
              required
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                clear("password");
              }}
              placeholder="••••••••"
            />
          </Field>
          {mode === "signup" ? (
            <Field label="Name the agent calls on behalf of" hint="Optional, shown on your profile." error={errors.displayName}>
              <input type="text" autoComplete="name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Maya Patel" />
            </Field>
          ) : null}
          <ErrorNote message={error} />
          <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
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
