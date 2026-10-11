"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type CSSProperties } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AvailabilityEditor } from "@/components/profile/AvailabilityEditor";
import { AppShell } from "@/components/shell";
import { Button, ErrorNote, Field, Loading } from "@/components/ui";
import { getProfile, upsertProfile } from "@/lib/api";
import { describeError, fmtDay, initials, maskPhone } from "@/lib/format";
import { getSupabaseBrowser, isSupabaseConfigured } from "@/lib/supabase/client";
import { COHORTS, DEFAULT_CALLER_NAME, type Prefs, type Profile } from "@/lib/types";

const d = (s: string) => ({ "--d": s }) as CSSProperties;

/** A two-column section from profile.html: heading and lede on the left, the fields on the right. */
function Section({ title, lede, children }: { title: string; lede?: string; children: React.ReactNode }) {
  return (
    <section className="psec">
      <div>
        <h2>{title}</h2>
        {lede ? <p>{lede}</p> : null}
      </div>
      <div>{children}</div>
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

  const head = (
    <div className="who-head">
      <span className="face rise" aria-hidden="true">
        {initials(displayName || profile?.display_name, email)}
      </span>
      <div>
        <h1 className="rise" style={d(".1s")}>
          {headName}
        </h1>
        <p className="rise" style={d(".2s")}>
          {cohortCfg.label}
          {profile?.created_at ? ` · member since ${fmtDay(profile.created_at.slice(0, 10))}` : ""}
          {phone ? ` · ${maskPhone(phone)}` : ""}
        </p>
      </div>
    </div>
  );

  return (
    <AppShell head={head}>
      <ErrorNote message={loadError} />
      {!profile && !loadError ? <Loading>Loading profile…</Loading> : null}

      <form onSubmit={save}>
        <Section title="About you" lede="Who the agent says it is calling for.">
          <div className="fields-2">
            <Field label="Name">
              <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="name" />
            </Field>
            <Field label="Mobile" hint="For transfers and callbacks. Shown masked everywhere else.">
              <input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 415 555 0100" autoComplete="tel" />
            </Field>
          </div>
          {email ? (
            <dl className="facts" style={{ marginTop: 18 }}>
              <div>
                <dt>Email</dt>
                <dd>
                  {email} <span className="verified">Signed in</span>
                </dd>
              </div>
            </dl>
          ) : null}
        </Section>

        <Section title="Eligibility" lede="The agent only claims what is true about you. This is exactly what it is told to ask for.">
          <Field label="Profile" hint="Only the student cohort ships today. More are config, not code, on the Python side.">
            <select value={cohort} onChange={(e) => setCohort(e.target.value)}>
              {COHORTS.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
              {!COHORTS.some((c) => c.key === cohort) ? <option value={cohort}>{cohort} (unknown, treated as student)</option> : null}
            </select>
          </Field>
          <p className="rules-h">Asked for on every call</p>
          <ul className="asks">
            {cohortCfg.asks.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          <p className="hint" style={{ marginTop: 12, fontSize: 14, color: "var(--ink-2)" }}>
            Proof vendors may want: {cohortCfg.proof}.
          </p>
        </Section>

        <Section title="On the phone" lede="How the agent introduces itself, and the limit it works within.">
          <div className="fields-2">
            <Field label="Name the agent uses on calls" hint={`Blank means ${DEFAULT_CALLER_NAME}. It always says it is an AI first.`}>
              <input type="text" value={callerName} onChange={(e) => setCallerName(e.target.value)} placeholder={DEFAULT_CALLER_NAME} />
            </Field>
            <Field label="Budget ceiling (USD)" hint="The agent brings anything above this back to you instead of accepting.">
              <input type="number" min={0} step={1} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="900" className="num" />
            </Field>
          </div>
        </Section>

        <Section title="Availability" lede="Used when booking appointments by phone.">
          <Field label="Preferred times" hint="Free text the agent can read out, like “weekday afternoons”. Press Enter to add.">
            <input
              type="text"
              value={timeDraft}
              onChange={(e) => setTimeDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addTime();
                }
              }}
              placeholder="weekday afternoons"
            />
          </Field>
          {times.length ? (
            <ul className="asks" style={{ marginTop: 14 }}>
              {times.map((t) => (
                <li key={t}>
                  {t}
                  <button type="button" className="rm" style={{ marginLeft: 8, padding: 0 }} onClick={() => setTimes(times.filter((x) => x !== t))} aria-label={`remove ${t}`}>
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <AvailabilityEditor slots={slots} onChange={setSlots} />
        </Section>

        <Section title="Account">
          <dl className="facts">
            <div>
              <dt>Calls</dt>
              <dd>
                <span>Everything it has called</span>
                <Link href="/calls">See calls</Link>
              </dd>
            </div>
            <div>
              <dt>Ledger</dt>
              <dd>
                <span>Dated facts from the calls</span>
                <Link href="/ledger">Open ledger</Link>
              </dd>
            </div>
            <div>
              <dt>Roster</dt>
              <dd>
                <span>Who it may call</span>
                <Link href="/onboarding">Edit vendors</Link>
              </dd>
            </div>
          </dl>
          {isSupabaseConfigured() ? (
            <Button variant="line" size="sm" onClick={signOut} className="signout">
              Sign out
            </Button>
          ) : (
            <p className="dash-note">dev mode · no auth · the Python side uses DEV_USER_ID</p>
          )}
        </Section>

        <div className="savebar">
          <Button type="submit" disabled={busy || !profile} size="sm">
            {busy ? "Saving…" : "Save profile"}
          </Button>
          {savedAt ? <span className="saved-at">Saved at {savedAt}</span> : null}
          <ErrorNote message={error} />
        </div>
      </form>
    </AppShell>
  );
}
