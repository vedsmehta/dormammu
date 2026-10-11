"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AvailabilityEditor } from "@/components/profile/AvailabilityEditor";
import { Button, ErrorNote, Field, inputClass, Loading } from "@/components/ui";
import { getProfile, upsertProfile } from "@/lib/api";
import { describeError, fmtDay, initials, maskPhone } from "@/lib/format";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";
import { COHORTS, DEFAULT_CALLER_NAME, type Prefs, type Profile } from "@/lib/types";

/** A two-column section from profile.html: heading and lede on the left, the fields on the right. */
function Section({ title, lede, children }: { title: string; lede?: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-5 border-t border-line py-8 first:border-t-0 first:pt-0 md:grid-cols-[1fr_2fr] md:gap-10">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight sm:text-2xl">{title}</h2>
        {lede ? <p className="mt-2 max-w-xs text-sm text-muted">{lede}</p> : null}
      </div>
      <div className="space-y-5">{children}</div>
    </section>
  );
}

export function ProfileForm() {
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [cohort, setCohort] = useState("student");
  const [callerName, setCallerName] = useState("");
  const [budget, setBudget] = useState("");
  const [times, setTimes] = useState<string[]>([]);
  const [timeDraft, setTimeDraft] = useState("");
  const [slots, setSlots] = useState<Slot[]>([]);
  const [otherPrefs, setOtherPrefs] = useState<Prefs>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getProfile()
      .then((p) => {
        if (cancelled) return;
        setProfile(p);
        setDisplayName(p.display_name ?? "");
        setPhone(p.phone ?? "");
        setCohort(p.cohort || "student");
        const prefs = p.prefs ?? {};
        const { budget_ceiling_usd, availability, preferred_times, caller_name, ...rest } = prefs;
        setBudget(budget_ceiling_usd === null || budget_ceiling_usd === undefined ? "" : String(budget_ceiling_usd));
        setSlots(toSlots(availability));
        setTimes(Array.isArray(preferred_times) ? preferred_times.filter((t) => typeof t === "string") : []);
        setCallerName(typeof caller_name === "string" ? caller_name : "");
        setOtherPrefs(rest);
        setLoadError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(describeError(e));
      });
    const supabase = getSupabaseBrowser();
    if (supabase) {
      supabase.auth.getSession().then(({ data }) => {
        if (!cancelled) setEmail(data.session?.user.email ?? null);
      });
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const addTime = () => {
    const t = timeDraft.trim();
    if (!t) return;
    if (!times.includes(t)) setTimes([...times, t]);
    setTimeDraft("");
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const budgetNum = budget.trim() === "" ? null : Number(budget);
      const prefs: Prefs = {
        ...otherPrefs,
        budget_ceiling_usd: budgetNum !== null && Number.isFinite(budgetNum) ? budgetNum : null,
        preferred_times: times,
        availability: fromSlots(slots),
        caller_name: callerName.trim() || null,
      };
      const saved = await upsertProfile({
        display_name: displayName.trim() || null,
        phone: phone.trim() || null,
        cohort,
        prefs,
      });
      setProfile(saved);
      setSavedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    const supabase = getSupabaseBrowser();
    if (supabase) await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  };

  const cohortCfg = COHORTS.find((c) => c.key === cohort) ?? COHORTS[0];
  const headName = displayName.trim() || profile?.display_name || "Your profile";

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
      <header className="animate-rise flex items-center gap-5 py-6 sm:gap-7 sm:py-10">
        <span
          className="grid h-20 w-20 shrink-0 place-items-center rounded-full bg-mint font-display text-2xl font-semibold text-bg sm:h-28 sm:w-28 sm:text-4xl"
          aria-hidden="true"
        >
          {initials(displayName || profile?.display_name, email)}
        </span>
        <div className="min-w-0">
          <h1 className="truncate font-display text-3xl font-semibold tracking-tight sm:text-5xl">{headName}</h1>
          <p className="mt-2 text-sm text-muted sm:text-base">
            {cohortCfg.label}
            {profile?.created_at ? ` · member since ${fmtDay(profile.created_at.slice(0, 10))}` : ""}
            {phone ? ` · ${maskPhone(phone)}` : ""}
          </p>
        </div>
      </header>

      <ErrorNote message={loadError} />
      {!profile && !loadError ? <Loading>loading profile…</Loading> : null}

      <form onSubmit={save}>
        <Section title="About you" lede="Who the agent says it is calling for.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="name">
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} autoComplete="name" />
            </Field>
            <Field label="mobile" hint="For transfers and callbacks. Shown masked everywhere else.">
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1 415 555 0100"
                autoComplete="tel"
                className={`${inputClass} font-mono`}
              />
            </Field>
          </div>
          {email ? (
            <p className="text-sm text-muted">
              <span className="font-mono text-[11px] uppercase tracking-wide text-dim">email · </span>
              {email}
            </p>
          ) : null}
        </Section>

        <Section
          title="Eligibility"
          lede="The agent only claims what is true about you. This is exactly what it is told to ask for."
        >
          <Field label="profile" hint="Only the student cohort ships today. More are config, not code, on the Python side.">
            <select value={cohort} onChange={(e) => setCohort(e.target.value)} className={inputClass}>
              {COHORTS.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
              {!COHORTS.some((c) => c.key === cohort) ? <option value={cohort}>{cohort} (unknown, treated as student)</option> : null}
            </select>
          </Field>
          <div>
            <p className="text-sm text-muted">Asked for on every call</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {cohortCfg.asks.map((a) => (
                <li key={a} className="rounded-full border border-teal/50 px-3 py-1.5 text-sm">
                  {a}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-dim">Proof vendors may want: {cohortCfg.proof}.</p>
          </div>
        </Section>

        <Section title="On the phone" lede="How the agent introduces itself, and the limit it works within.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="name the agent uses on calls" hint={`Blank means ${DEFAULT_CALLER_NAME}. It always says it is an AI first.`}>
              <input
                value={callerName}
                onChange={(e) => setCallerName(e.target.value)}
                placeholder={DEFAULT_CALLER_NAME}
                className={inputClass}
              />
            </Field>
            <Field label="budget ceiling (usd)" hint="The agent brings anything above this back to you instead of accepting.">
              <input
                type="number"
                min={0}
                step={1}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="900"
                className={`${inputClass} tnum font-mono`}
              />
            </Field>
          </div>
        </Section>

        <Section title="Availability" lede="Used when booking appointments by phone.">
          <div>
            <span className="mb-1 block font-mono text-xs uppercase tracking-wide text-muted">preferred times</span>
            <div className="flex gap-2">
              <input
                value={timeDraft}
                onChange={(e) => setTimeDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTime();
                  }
                }}
                placeholder="weekday afternoons"
                className={inputClass}
              />
              <Button variant="ghost" onClick={addTime} disabled={!timeDraft.trim()}>
                Add
              </Button>
            </div>
            {times.length ? (
              <ul className="mt-2 flex flex-wrap gap-2">
                {times.map((t) => (
                  <li key={t} className="flex items-center gap-1 rounded-full bg-panel-2 px-3 py-1 text-sm">
                    {t}
                    <button
                      type="button"
                      onClick={() => setTimes(times.filter((x) => x !== t))}
                      className="ml-1 text-dim hover:text-bad"
                      aria-label={`remove ${t}`}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-dim">Free text the agent can read out, like &ldquo;weekday afternoons&rdquo;.</p>
            )}
          </div>
          <AvailabilityEditor slots={slots} onChange={setSlots} />
        </Section>

        <Section title="Account">
          <ul className="divide-y divide-line text-sm">
            <li className="flex items-center justify-between py-2">
              <span className="text-muted">Calls</span>
              <Link href="/calls" className="text-teal underline underline-offset-4 hover:text-fg">
                See calls
              </Link>
            </li>
            <li className="flex items-center justify-between py-2">
              <span className="text-muted">Ledger</span>
              <Link href="/ledger" className="text-teal underline underline-offset-4 hover:text-fg">
                Open ledger
              </Link>
            </li>
            <li className="flex items-center justify-between py-2">
              <span className="text-muted">Roster</span>
              <Link href="/onboarding" className="text-teal underline underline-offset-4 hover:text-fg">
                Edit vendors
              </Link>
            </li>
          </ul>
          {isSupabaseConfigured() ? (
            <Button variant="ghost" onClick={signOut}>
              Sign out
            </Button>
          ) : (
            <p className="font-mono text-xs text-warn">dev mode · no auth · the Python side uses DEV_USER_ID</p>
          )}
        </Section>

        <div className="sticky bottom-0 -mx-4 mt-2 flex flex-wrap items-center gap-3 border-t border-line bg-bg/90 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <Button type="submit" disabled={busy || !profile} variant="mint">
            {busy ? "saving…" : "Save profile"}
          </Button>
          {savedAt ? <span className="font-mono text-xs text-ok">saved at {savedAt}</span> : null}
          <ErrorNote message={error} />
        </div>
      </form>
    </main>
  );
}
