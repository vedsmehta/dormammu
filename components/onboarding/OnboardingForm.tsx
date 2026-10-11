"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AvailabilityEditor } from "@/components/profile/AvailabilityEditor";
import { Button, ErrorNote, Field, inputClass, Panel } from "@/components/ui";
import { getProfile, getVendors, upsertProfile, upsertVendors } from "@/lib/api";
import { describeError } from "@/lib/format";
import { COHORTS, DEFAULT_CALLER_NAME, type Prefs, type Track, type Vendor } from "@/lib/types";

// The demo roster. Phones stay blank here and are typed in through this form, never committed.
const DEFAULT_VENDORS: Vendor[] = [
  { key: "movers_a", name: "Pack & Go Movers", track: "quote", phone: "", is_demo: true },
  { key: "movers_b", name: "BearHug Moving", track: "quote", phone: "", is_demo: true },
  { key: "movers_c", name: "Golden Gate Movers", track: "quote", phone: "", is_demo: true },
  { key: "dentist", name: "Dr. Chen Dental", track: "schedule", phone: "", is_demo: true },
  { key: "internet_retention", name: "Comcast Retention", track: "negotiate", phone: "", is_demo: true },
];

const TRACKS: Track[] = ["quote", "schedule", "negotiate"];

function mergeRoster(fromApi: Vendor[]): Vendor[] {
  if (fromApi.length === 0) return DEFAULT_VENDORS.map((v) => ({ ...v }));
  const rows = fromApi.map((v) => ({ ...v, phone: v.phone ?? "", is_demo: Boolean(v.is_demo) }));
  for (const d of DEFAULT_VENDORS) {
    if (rows.length >= 5) break;
    if (!rows.some((r) => r.key === d.key)) rows.push({ ...d });
  }
  return rows;
}

/** The three-step indicator from the prototype's auth sheets. */
function Progress({ step }: { step: 0 | 1 | 2 }) {
  const steps = ["Profile", "Roster", "Ready"];
  return (
    <ol className="flex items-center gap-3 font-mono text-xs" aria-label="Setup progress">
      {steps.map((label, i) => (
        <li key={label} className="flex items-center gap-2">
          {i > 0 ? <span className="h-px w-6 bg-line" aria-hidden="true" /> : null}
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              i < step ? "bg-fg" : i === step ? "bg-mint shadow-[0_0_0_4px_rgba(134,247,226,0.3)]" : "border border-dim"
            }`}
            aria-hidden="true"
          />
          <span className={i === step ? "font-semibold text-fg" : i < step ? "text-muted" : "text-dim"} aria-current={i === step ? "step" : undefined}>
            {label}
          </span>
        </li>
      ))}
    </ol>
  );
}

export function OnboardingForm() {
  const [loaded, setLoaded] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [cohort, setCohort] = useState("student");
  const [callerName, setCallerName] = useState("");
  const [budget, setBudget] = useState<string>("");
  const [slots, setSlots] = useState<Slot[]>([]);
  const [otherPrefs, setOtherPrefs] = useState<Prefs>({});
  const [vendors, setVendors] = useState<Vendor[]>(DEFAULT_VENDORS.map((v) => ({ ...v })));
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getProfile(), getVendors()]).then(([p, v]) => {
      if (cancelled) return;
      const notes: string[] = [];
      if (p.status === "fulfilled" && p.value) {
        const prof = p.value;
        setDisplayName(prof.display_name ?? "");
        setPhone(prof.phone ?? "");
        setCohort(prof.cohort || "student");
        const prefs = prof.prefs ?? {};
        const { budget_ceiling_usd, availability, caller_name, ...rest } = prefs;
        setBudget(budget_ceiling_usd === null || budget_ceiling_usd === undefined ? "" : String(budget_ceiling_usd));
        setSlots(toSlots(availability));
        setCallerName(typeof caller_name === "string" ? caller_name : "");
        setOtherPrefs(rest);
      } else if (p.status === "rejected") {
        notes.push(`profile not loaded (${describeError(p.reason)})`);
      }
      if (v.status === "fulfilled") setVendors(mergeRoster(Array.isArray(v.value) ? v.value : []));
      else notes.push(`vendors not loaded (${describeError(v.reason)}); showing the demo defaults`);
      setLoadNote(notes.length ? notes.join(" · ") : null);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const updateVendor = (i: number, patch: Partial<Vendor>) =>
    setVendors((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  const addVendor = () =>
    setVendors((rows) => [...rows, { key: "", name: "", track: "quote", phone: "", is_demo: true }]);

  const removeVendor = (i: number) => setVendors((rows) => rows.filter((_, idx) => idx !== i));

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
        availability: fromSlots(slots),
        caller_name: callerName.trim() || null,
      };
      await upsertProfile({
        display_name: displayName.trim() || null,
        phone: phone.trim() || null,
        cohort,
        prefs,
      });
      const roster = vendors
        .filter((v) => v.key.trim() && v.name.trim())
        .map((v) => ({
          key: v.key.trim(),
          name: v.name.trim(),
          track: v.track,
          phone: v.phone.trim(),
          is_demo: Boolean(v.is_demo),
        }));
      await upsertVendors(roster);
      setSaved(true);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  // ready.html: the post-save confirmation.
  if (saved) {
    return (
      <main className="mx-auto w-full max-w-xl flex-1 px-4 py-12 sm:px-6">
        <Progress step={2} />
        <div className="mt-8 grid h-16 w-16 place-items-center rounded-full bg-mint font-display text-2xl text-bg" aria-hidden="true">
          ✓
        </div>
        <h1 className="mt-6 font-display text-3xl font-semibold tracking-tight sm:text-4xl">You&rsquo;re set</h1>
        <p className="mt-3 text-muted">
          Your profile and roster are saved. Tell Dormammu what you need handled and it starts calling. It says it is an
          AI before anything else, on every call.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link href="/" className="rounded-full bg-mint px-5 py-3 text-base font-medium text-bg hover:bg-white">
            Open Dormammu
          </Link>
          <button type="button" onClick={() => setSaved(false)} className="text-sm text-muted hover:text-fg">
            Edit again
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8 sm:px-6">
      <Progress step={0} />

      <form onSubmit={save} className="mt-6 space-y-8">
        <div>
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">Set up your profile</h1>
          <p className="mt-2 text-muted">
            This is what the agent knows about you when it calls. Your profile decides which discounts it asks for, and
            it only claims what you tell it here. Phone numbers live only in your roster table; they never go into git,
            and the UI masks them.
          </p>
          {loadNote ? <p className="mt-2 font-mono text-xs text-warn">{loadNote}</p> : null}
          {!loaded ? <p className="mt-2 font-mono text-xs text-dim">loading…</p> : null}
        </div>

        <Panel className="space-y-5 p-5">
          <h2 className="font-mono text-xs uppercase tracking-widest text-teal">Profile</h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="name the agent calls on behalf of">
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} autoComplete="name" />
            </Field>
            <Field label="your phone" hint="for transfers and callbacks">
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1 415 555 0100"
                autoComplete="tel"
                className={`${inputClass} font-mono`}
              />
            </Field>
          </div>

          <fieldset>
            <legend className="mb-2 block font-mono text-xs uppercase tracking-wide text-muted">which of these describes you?</legend>
            <div className="flex flex-wrap gap-2">
              {COHORTS.map((c) => (
                <label key={c.key} className="relative">
                  <input
                    type="radio"
                    name="cohort"
                    value={c.key}
                    checked={cohort === c.key}
                    onChange={() => setCohort(c.key)}
                    className="peer absolute inset-0 cursor-pointer opacity-0"
                  />
                  <span className="block rounded-full border border-line px-4 py-2 text-sm text-muted transition peer-checked:border-fg peer-checked:bg-fg peer-checked:text-bg peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-mint">
                    {c.label}
                  </span>
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-dim">
              Only the student cohort ships today. On every call the agent asks for: {COHORTS[0].asks.join(", ")}. Proof vendors may
              want: {COHORTS[0].proof}.
            </p>
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="name the agent uses on calls" hint={`Blank means ${DEFAULT_CALLER_NAME}. It always says it is an AI first.`}>
              <input value={callerName} onChange={(e) => setCallerName(e.target.value)} placeholder={DEFAULT_CALLER_NAME} className={inputClass} />
            </Field>
            <Field label="budget ceiling (usd)">
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

          <AvailabilityEditor slots={slots} onChange={setSlots} />
        </Panel>

        <Panel className="space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-mono text-xs uppercase tracking-widest text-teal">Roster</h2>
            <button type="button" onClick={addVendor} className="text-xs text-accent hover:underline">
              + add row
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] text-sm">
              <thead>
                <tr className="font-mono text-[11px] uppercase tracking-wide text-muted">
                  <th className="pb-2 pr-2 text-left font-normal">key</th>
                  <th className="pb-2 pr-2 text-left font-normal">name</th>
                  <th className="pb-2 pr-2 text-left font-normal">track</th>
                  <th className="pb-2 pr-2 text-left font-normal">phone</th>
                  <th className="pb-2 pr-2 text-left font-normal">demo</th>
                  <th className="pb-2" />
                </tr>
              </thead>
              <tbody>
                {vendors.map((v, i) => (
                  <tr key={i} className="border-t border-line">
                    <td className="py-1.5 pr-2">
                      <input
                        value={v.key}
                        onChange={(e) => updateVendor(i, { key: e.target.value })}
                        aria-label="vendor key"
                        className={`${inputClass} font-mono`}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        value={v.name}
                        onChange={(e) => updateVendor(i, { name: e.target.value })}
                        aria-label="vendor name"
                        className={inputClass}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <select
                        value={v.track}
                        onChange={(e) => updateVendor(i, { track: e.target.value as Track })}
                        aria-label="track"
                        className={inputClass}
                      >
                        {TRACKS.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-1.5 pr-2">
                      <input
                        value={v.phone}
                        onChange={(e) => updateVendor(i, { phone: e.target.value })}
                        placeholder="+1…"
                        autoComplete="off"
                        aria-label="vendor phone"
                        className={`${inputClass} font-mono`}
                      />
                    </td>
                    <td className="py-1.5 pr-2 text-center">
                      <input
                        type="checkbox"
                        checked={v.is_demo}
                        onChange={(e) => updateVendor(i, { is_demo: e.target.checked })}
                        aria-label="demo vendor"
                        className="h-4 w-4 accent-[var(--color-accent)]"
                      />
                    </td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        onClick={() => removeVendor(i)}
                        className="px-2 text-xs text-dim hover:text-bad"
                        aria-label="remove vendor"
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-dim">
            Only rows in this table are dialable. The router picks by key and never sees a number. Today every number
            here belongs to someone in the room.
          </p>
        </Panel>

        <ErrorNote message={error} />
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy} variant="mint">
            {busy ? "saving…" : "Finish setup"}
          </Button>
          <Link href="/" className="text-xs text-dim hover:text-fg">
            skip
          </Link>
        </div>
      </form>
    </main>
  );
}
