"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand, Button, ErrorNote, Field, inputClass, Panel } from "@/components/ui";
import { UserChip } from "@/components/UserChip";
import { ApiError, getProfile, getVendors, upsertProfile, upsertVendors } from "@/lib/api";
import { parseTs, toLocalInput, toOffsetIso } from "@/lib/format";
import type { Availability, Prefs, Track, Vendor } from "@/lib/types";

// The demo roster. Phones stay blank here and are typed in through this form, never committed.
const DEFAULT_VENDORS: Vendor[] = [
  { key: "movers_a", name: "Pack & Go Movers", track: "quote", phone: "", is_demo: true },
  { key: "movers_b", name: "BearHug Moving", track: "quote", phone: "", is_demo: true },
  { key: "movers_c", name: "Golden Gate Movers", track: "quote", phone: "", is_demo: true },
  { key: "dentist", name: "Dr. Chen Dental", track: "schedule", phone: "", is_demo: true },
  { key: "internet_retention", name: "Comcast Retention", track: "negotiate", phone: "", is_demo: true },
];

const TRACKS: Track[] = ["quote", "schedule", "negotiate"];

type Slot = { start: string; end: string };

function toSlots(avail: Availability[] | undefined): Slot[] {
  if (!Array.isArray(avail)) return [];
  return avail.map((a) => {
    const s = parseTs(a.start);
    const e = parseTs(a.end);
    return {
      start: s === null ? "" : toLocalInput(new Date(s)),
      end: e === null ? "" : toLocalInput(new Date(e)),
    };
  });
}

function fromSlots(slots: Slot[]): Availability[] {
  return slots
    .filter((s) => s.start && s.end)
    .map((s) => ({ start: toOffsetIso(new Date(s.start)), end: toOffsetIso(new Date(s.end)) }));
}

function mergeRoster(fromApi: Vendor[]): Vendor[] {
  if (fromApi.length === 0) return DEFAULT_VENDORS.map((v) => ({ ...v }));
  const rows = fromApi.map((v) => ({ ...v, phone: v.phone ?? "", is_demo: Boolean(v.is_demo) }));
  for (const d of DEFAULT_VENDORS) {
    if (rows.length >= 5) break;
    if (!rows.some((r) => r.key === d.key)) rows.push({ ...d });
  }
  return rows;
}

function describe(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export function OnboardingForm() {
  const router = useRouter();
  const [loaded, setLoaded] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [cohort, setCohort] = useState("student");
  const [budget, setBudget] = useState<string>("");
  const [slots, setSlots] = useState<Slot[]>([]);
  const [otherPrefs, setOtherPrefs] = useState<Prefs>({});
  const [vendors, setVendors] = useState<Vendor[]>(DEFAULT_VENDORS.map((v) => ({ ...v })));
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
        const { budget_ceiling_usd, availability, ...rest } = prefs;
        setBudget(budget_ceiling_usd === null || budget_ceiling_usd === undefined ? "" : String(budget_ceiling_usd));
        setSlots(toSlots(availability));
        setOtherPrefs(rest);
      } else if (p.status === "rejected") {
        notes.push(`profile not loaded (${describe(p.reason)})`);
      }
      if (v.status === "fulfilled") setVendors(mergeRoster(Array.isArray(v.value) ? v.value : []));
      else notes.push(`vendors not loaded (${describe(v.reason)}); showing the demo defaults`);
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

  const updateSlot = (i: number, patch: Partial<Slot>) =>
    setSlots((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

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
      router.push("/");
    } catch (err) {
      setError(describe(err));
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-8 sm:px-6">
      <header className="mb-8 flex items-center justify-between gap-4">
        <Brand />
        <div className="flex items-center gap-4">
          <Link href="/" className="font-mono text-xs text-muted hover:text-fg">
            back
          </Link>
          <UserChip />
        </div>
      </header>

      <form onSubmit={save} className="space-y-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Who is calling, and who gets called</h1>
          <p className="mt-1 text-sm text-muted">
            Phone numbers live only in your roster table. They never go into git, and the UI masks them.
          </p>
          {loadNote ? <p className="mt-2 font-mono text-xs text-warn">{loadNote}</p> : null}
          {!loaded ? <p className="mt-2 font-mono text-xs text-dim">loading…</p> : null}
        </div>

        <Panel className="space-y-5 p-5">
          <h2 className="font-mono text-xs uppercase tracking-widest text-muted">Profile</h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="display name">
              <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputClass} />
            </Field>
            <Field label="your phone" hint="for transfers and callbacks">
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+1 415 555 0100"
                className={inputClass}
              />
            </Field>
            <Field label="cohort">
              <select value={cohort} onChange={(e) => setCohort(e.target.value)} className={inputClass}>
                <option value="student">student</option>
              </select>
            </Field>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
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
          <div>
            <div className="mb-2 flex items-center justify-between">
              <span className="font-mono text-xs uppercase tracking-wide text-muted">availability</span>
              <button
                type="button"
                onClick={() => setSlots((s) => [...s, { start: "", end: "" }])}
                className="text-xs text-accent hover:underline"
              >
                + add window
              </button>
            </div>
            {slots.length === 0 ? (
              <p className="text-xs text-dim">No windows yet. The scheduling track uses these to pick a slot.</p>
            ) : (
              <ul className="space-y-2">
                {slots.map((s, i) => (
                  <li key={i} className="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
                    <input
                      type="datetime-local"
                      value={s.start}
                      onChange={(e) => updateSlot(i, { start: e.target.value })}
                      className={`${inputClass} font-mono`}
                    />
                    <input
                      type="datetime-local"
                      value={s.end}
                      onChange={(e) => updateSlot(i, { end: e.target.value })}
                      className={`${inputClass} font-mono`}
                    />
                    <button
                      type="button"
                      onClick={() => setSlots((rows) => rows.filter((_, idx) => idx !== i))}
                      className="px-2 text-xs text-dim hover:text-bad"
                      aria-label="remove window"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Panel>

        <Panel className="space-y-4 p-5">
          <div className="flex items-center justify-between">
            <h2 className="font-mono text-xs uppercase tracking-widest text-muted">Roster</h2>
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
                        className={`${inputClass} font-mono`}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <input value={v.name} onChange={(e) => updateVendor(i, { name: e.target.value })} className={inputClass} />
                    </td>
                    <td className="py-1.5 pr-2">
                      <select
                        value={v.track}
                        onChange={(e) => updateVendor(i, { track: e.target.value as Track })}
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
                        className={`${inputClass} font-mono`}
                      />
                    </td>
                    <td className="py-1.5 pr-2 text-center">
                      <input
                        type="checkbox"
                        checked={v.is_demo}
                        onChange={(e) => updateVendor(i, { is_demo: e.target.checked })}
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
            Only rows in this table are dialable. The router picks by key and never sees a number. Today
            every number here belongs to someone in the room.
          </p>
        </Panel>

        <ErrorNote message={error} />
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? "saving…" : "Save and continue"}
          </Button>
          <Link href="/" className="text-xs text-dim hover:text-fg">
            skip
          </Link>
        </div>
      </form>
    </main>
  );
}
