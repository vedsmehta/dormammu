"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AvailabilityEditor } from "@/components/profile/AvailabilityEditor";
import { AuthShell, Progress } from "@/components/shell";
import { ErrorNote, Field } from "@/components/ui";
import { getProfile, getVendors, research, upsertProfile, upsertVendors } from "@/lib/api";
import { describeError } from "@/lib/format";
import {
  BEDROOMS,
  COHORTS,
  DEFAULT_CALLER_NAME,
  PROVIDER_CATEGORIES,
  type Address,
  type Bedrooms,
  type CurrentProvider,
  type Prefs,
  type ProviderCategory,
  type Track,
  type Vendor,
} from "@/lib/types";

// The demo roster. Phones stay blank here and are typed in through this form, never committed.
const DEFAULT_VENDORS: Vendor[] = [
  { key: "movers_a", name: "Pack & Go Movers", track: "quote", phone: "", is_demo: true },
  { key: "movers_b", name: "BearHug Moving", track: "quote", phone: "", is_demo: true },
  { key: "movers_c", name: "Golden Gate Movers", track: "quote", phone: "", is_demo: true },
  { key: "dentist", name: "Dr. Chen Dental", track: "schedule", phone: "", is_demo: true },
  { key: "internet_retention", name: "Comcast Retention", track: "negotiate", phone: "", is_demo: true },
];

const TRACKS: Track[] = ["quote", "schedule", "negotiate"];

type AddressDraft = { street: string; city: string; state: string; zip: string };
type ProviderDraft = { category: ProviderCategory; name: string; monthly_usd: string; contract_end: string };

const EMPTY_ADDRESS: AddressDraft = { street: "", city: "", state: "", zip: "" };

function toAddressDraft(a: Address | null | undefined): AddressDraft {
  return { street: a?.street ?? "", city: a?.city ?? "", state: a?.state ?? "", zip: a?.zip ?? "" };
}

function fromAddressDraft(a: AddressDraft): Address | null {
  const out: Address = { street: a.street.trim() || null, city: a.city.trim() || null, state: a.state.trim() || null, zip: a.zip.trim() || null };
  return Object.values(out).some(Boolean) ? out : null;
}

function toProviderDrafts(list: CurrentProvider[] | undefined): ProviderDraft[] {
  if (!Array.isArray(list)) return [];
  return list.map((p) => ({
    category: PROVIDER_CATEGORIES.includes(p.category) ? p.category : "other",
    name: p.name ?? "",
    monthly_usd: p.monthly_usd === null || p.monthly_usd === undefined ? "" : String(p.monthly_usd),
    contract_end: p.contract_end ?? "",
  }));
}

function fromProviderDrafts(list: ProviderDraft[]): CurrentProvider[] {
  return list
    .filter((p) => p.name.trim())
    .map((p) => {
      const amount = p.monthly_usd.trim() === "" ? null : Number(p.monthly_usd);
      return {
        category: p.category,
        name: p.name.trim(),
        monthly_usd: amount !== null && Number.isFinite(amount) ? amount : null,
        contract_end: p.contract_end || null,
      };
    });
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

/** Researched vendors with a phone confirmed on a cited page, as roster rows. */
async function fetchVendors(city: string, state: string): Promise<Vendor[]> {
  const where = [city, state].filter(Boolean).join(", ");
  const row = await research(`I'm moving to ${where}: find movers, internet, renters insurance and a dentist`);
  const seen = new Set<string>();
  return (row.proposal?.items ?? [])
    .filter((it) => it.phone_verified && !it.skip_reason && it.vendor.phone)
    .sort((a, b) => Number(b.recommended) - Number(a.recommended))
    .filter((it) => (seen.has(it.vendor.phone!) ? false : (seen.add(it.vendor.phone!), true)))
    .map((it) => ({ key: it.vendor.key, name: it.vendor.name, track: it.track, phone: it.vendor.phone!, is_demo: false }));
}

/** Street, city, state, zip as four underline fields. */
function AddressFields({ legend, value, onChange }: { legend: string; value: AddressDraft; onChange: (a: AddressDraft) => void }) {
  const set = (patch: Partial<AddressDraft>) => onChange({ ...value, ...patch });
  return (
    <fieldset className="field">
      <legend>{legend}</legend>
      <Field label="Street" tight>
        <input type="text" value={value.street} onChange={(e) => set({ street: e.target.value })} autoComplete="street-address" />
      </Field>
      <div className="fields-2">
        <Field label="City" tight>
          <input type="text" value={value.city} onChange={(e) => set({ city: e.target.value })} autoComplete="address-level2" />
        </Field>
        <Field label="State" tight>
          <input type="text" value={value.state} onChange={(e) => set({ state: e.target.value })} autoComplete="address-level1" />
        </Field>
      </div>
      <Field label="ZIP" tight>
        <input type="text" value={value.zip} onChange={(e) => set({ zip: e.target.value })} autoComplete="postal-code" inputMode="numeric" className="num" />
      </Field>
    </fieldset>
  );
}

export function OnboardingForm() {
  const [loaded, setLoaded] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [phone, setPhone] = useState("");
  const [cohort, setCohort] = useState("student");
  const [studentProof, setStudentProof] = useState("");
  const [callerName, setCallerName] = useState("");
  const [budget, setBudget] = useState<string>("");
  const [slots, setSlots] = useState<Slot[]>([]);
  const [currentAddress, setCurrentAddress] = useState<AddressDraft>(EMPTY_ADDRESS);
  const [newAddress, setNewAddress] = useState<AddressDraft>(EMPTY_ADDRESS);
  const [moveDate, setMoveDate] = useState("");
  const [bedrooms, setBedrooms] = useState<Bedrooms | "">("");
  const [householdSize, setHouseholdSize] = useState("");
  const [providers, setProviders] = useState<ProviderDraft[]>([]);
  const [dental, setDental] = useState("");
  const [health, setHealth] = useState("");
  const [otherPrefs, setOtherPrefs] = useState<Prefs>({});
  const [vendors, setVendors] = useState<Vendor[]>(DEFAULT_VENDORS.map((v) => ({ ...v })));
  const [loadNote, setLoadNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [filling, setFilling] = useState(false);
  const [fillNote, setFillNote] = useState<string | null>(null);
  const [filledFor, setFilledFor] = useState("");

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
        const {
          budget_ceiling_usd,
          availability,
          caller_name,
          current_address,
          new_address,
          move_date,
          bedrooms: beds,
          household_size,
          current_providers,
          insurance,
          student_proof,
          ...rest
        } = prefs;
        setBudget(budget_ceiling_usd === null || budget_ceiling_usd === undefined ? "" : String(budget_ceiling_usd));
        setSlots(toSlots(availability));
        setCallerName(typeof caller_name === "string" ? caller_name : "");
        setCurrentAddress(toAddressDraft(current_address));
        setNewAddress(toAddressDraft(new_address));
        setMoveDate(typeof move_date === "string" ? move_date : "");
        setBedrooms(beds && BEDROOMS.includes(beds) ? beds : "");
        setHouseholdSize(household_size === null || household_size === undefined ? "" : String(household_size));
        setProviders(toProviderDrafts(current_providers));
        setDental(insurance?.dental ?? "");
        setHealth(insurance?.health ?? "");
        setStudentProof(typeof student_proof === "string" ? student_proof : "");
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
  const addVendor = () => setVendors((rows) => [...rows, { key: "", name: "", track: "quote", phone: "", is_demo: true }]);
  const removeVendor = (i: number) => setVendors((rows) => rows.filter((_, idx) => idx !== i));

  const updateProvider = (i: number, patch: Partial<ProviderDraft>) =>
    setProviders((rows) => rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const addProvider = () => setProviders((rows) => [...rows, { category: "internet", name: "", monthly_usd: "", contract_end: "" }]);
  const removeProvider = (i: number) => setProviders((rows) => rows.filter((_, idx) => idx !== i));

  /** Prefill names and numbers from research for the city you are moving to. Keeps rows already typed. */
  const autofill = async (city = newAddress.city.trim(), state = newAddress.state.trim()) => {
    if (!city || filling) return;
    setFilling(true);
    setFillNote(null);
    try {
      const found = await fetchVendors(city, state);
      setFilledFor(city.toLowerCase());
      if (found.length === 0) {
        setFillNote(`No vendors with a verified phone found for ${city} yet. You can add them below.`);
        return;
      }
      setVendors((rows) => {
        const kept = rows.filter((r) => r.phone.trim());
        const phones = new Set(kept.map((r) => r.phone.replace(/\D/g, "")));
        return [...kept, ...found.filter((f) => !phones.has(f.phone.replace(/\D/g, "")))];
      });
      setFillNote(`Filled in ${found.length} vendors for ${city}, each phone checked against a cited source.`);
    } catch (err) {
      setFillNote(`Couldn't fetch vendors (${describeError(err)}). You can add them below.`);
    } finally {
      setFilling(false);
    }
  };

  // Once the destination city is known and nobody has a phone yet, fetch them without being asked.
  useEffect(() => {
    const city = newAddress.city.trim();
    if (!loaded || city.length < 3 || filledFor === city.toLowerCase()) return;
    if (vendors.some((v) => v.phone.trim())) return;
    const t = setTimeout(() => autofill(city, newAddress.state.trim()), 900);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, newAddress.city, newAddress.state, filledFor]);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const budgetNum = budget.trim() === "" ? null : Number(budget);
      const household = householdSize.trim() === "" ? null : Number(householdSize);
      const prefs: Prefs = {
        ...otherPrefs,
        budget_ceiling_usd: budgetNum !== null && Number.isFinite(budgetNum) ? budgetNum : null,
        availability: fromSlots(slots),
        caller_name: callerName.trim() || null,
        current_address: fromAddressDraft(currentAddress),
        new_address: fromAddressDraft(newAddress),
        move_date: moveDate || null,
        bedrooms: bedrooms || null,
        household_size: household !== null && Number.isFinite(household) ? household : null,
        current_providers: fromProviderDrafts(providers),
        insurance: dental.trim() || health.trim() ? { dental: dental.trim() || null, health: health.trim() || null } : null,
        student_proof: studentProof.trim() || null,
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
      <AuthShell>
        <Progress step={3} />
        <div className="big-mark" aria-hidden="true">
          ✓
        </div>
        <h1>You&rsquo;re set</h1>
        <p>
          Your profile is saved. Tell Dormammu what you need handled and it starts researching, then calling. It says it is an AI before
          anything else, on every call.
        </p>
        <Link className="btn btn-primary btn-block" href="/">
          Open Dormammu
        </Link>
        <p className="auth-alt">
          <button type="button" onClick={() => setSaved(false)}>
            Edit again
          </button>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell wide>
      <Progress step={1} />

      <form onSubmit={save}>
        <h1>Set up your profile</h1>
        <p>
          This is what the agent knows about you before it researches or calls anyone. Your profile decides which discounts it asks for,
          and it only claims what you tell it here. Phone numbers never go into git, and the UI masks them.
        </p>
        {loadNote ? <p className="note warn">{loadNote}</p> : null}
        {!loaded ? <p className="dash-note">Loading…</p> : null}

        <div className="fields-2">
          <Field label="Name the agent calls on behalf of">
            <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="name" placeholder="Maya Patel" />
          </Field>
          <Field label="Your mobile" hint="For transfers and callbacks.">
            <input type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+1 415 555 0100" autoComplete="tel" />
          </Field>
        </div>

        <fieldset className="field">
          <legend>Which of these describes you?</legend>
          <div className="pills">
            {COHORTS.map((c) => (
              <label key={c.key} className="pill">
                <input type="radio" name="cohort" value={c.key} checked={cohort === c.key} onChange={() => setCohort(c.key)} />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
          <p className="hint">
            Only the student cohort ships today. On every call the agent asks for: {COHORTS[0].asks.join(", ")}. Proof vendors may want:{" "}
            {COHORTS[0].proof}.
          </p>
        </fieldset>
        <Field label="Proof of student status" hint="What the agent can say is on file, e.g. “stanford.edu email on file”.">
          <input type="text" value={studentProof} onChange={(e) => setStudentProof(e.target.value)} placeholder="stanford.edu email on file" />
        </Field>

        <AddressFields legend="Where you live now" value={currentAddress} onChange={setCurrentAddress} />
        <AddressFields legend="Where you are moving" value={newAddress} onChange={setNewAddress} />

        <div className="fields-2">
          <Field label="Move date">
            <input type="date" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} className="num" />
          </Field>
          <Field label="People in the household">
            <input type="number" min={1} step={1} value={householdSize} onChange={(e) => setHouseholdSize(e.target.value)} placeholder="1" className="num" />
          </Field>
        </div>
        <fieldset className="field">
          <legend>Bedrooms</legend>
          <div className="pills">
            {BEDROOMS.map((b) => (
              <label key={b} className="pill">
                <input type="radio" name="bedrooms" value={b} checked={bedrooms === b} onChange={() => setBedrooms(b)} />
                <span>{b === "studio" ? "Studio" : b}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <fieldset className="field">
          <legend>What you pay for now</legend>
          <p className="hint">Internet, phone, gym, insurance, utilities, streaming. The research stage compares against these.</p>
          <ul className="roster">
            {providers.map((p, i) => (
              <li key={i}>
                <label>
                  <span className="lbl">Name</span>
                  <input className="input" value={p.name} onChange={(e) => updateProvider(i, { name: e.target.value })} aria-label="provider name" />
                </label>
                <label>
                  <span className="lbl">Category</span>
                  <select className="input" value={p.category} onChange={(e) => updateProvider(i, { category: e.target.value as ProviderCategory })} aria-label="category">
                    {PROVIDER_CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="lbl">$ per month</span>
                  <input className="input num" type="number" min={0} step={1} value={p.monthly_usd} onChange={(e) => updateProvider(i, { monthly_usd: e.target.value })} aria-label="monthly cost" />
                </label>
                <label>
                  <span className="lbl">Contract ends</span>
                  <input className="input num" type="date" value={p.contract_end} onChange={(e) => updateProvider(i, { contract_end: e.target.value })} aria-label="contract end" />
                </label>
                <span />
                <button type="button" className="rm" onClick={() => removeProvider(i)} aria-label="remove provider">
                  ×
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="add" onClick={addProvider}>
            + Add something you pay for
          </button>
        </fieldset>

        <div className="fields-2">
          <Field label="Dental insurance" hint="Free text, as you would say it on the phone.">
            <input type="text" value={dental} onChange={(e) => setDental(e.target.value)} placeholder="Delta Dental PPO through school" />
          </Field>
          <Field label="Health insurance">
            <input type="text" value={health} onChange={(e) => setHealth(e.target.value)} placeholder="Cardinal Care" />
          </Field>
        </div>

        <div className="fields-2">
          <Field label="Name the agent uses on calls" hint={`Blank means ${DEFAULT_CALLER_NAME}. It always says it is an AI first.`}>
            <input type="text" value={callerName} onChange={(e) => setCallerName(e.target.value)} placeholder={DEFAULT_CALLER_NAME} />
          </Field>
          <Field label="Budget ceiling (USD)">
            <input type="number" min={0} step={1} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="900" className="num" />
          </Field>
        </div>

        <AvailabilityEditor slots={slots} onChange={setSlots} />

        <fieldset className="field">
          <legend>Who Dormammu will call</legend>
          <p className="hint">
            Filled in automatically from research for the city you are moving to: only numbers found on a cited page are kept. Edit or add
            rows if you like; the router picks by key and never sees a number.
          </p>
          <p className="hint">
            <button type="button" className="add" onClick={() => autofill()} disabled={filling || !newAddress.city.trim()}>
              {filling ? "Finding vendors…" : newAddress.city.trim() ? `↻ Find vendors in ${newAddress.city.trim()}` : "Enter the city you are moving to first"}
            </button>
          </p>
          {fillNote ? <p className="note">{fillNote}</p> : null}
          <ul className="roster">
            {vendors.map((v, i) => (
              <li key={i}>
                <label>
                  <span className="lbl">Name</span>
                  <input className="input" value={v.name} onChange={(e) => updateVendor(i, { name: e.target.value })} aria-label="vendor name" />
                </label>
                <label>
                  <span className="lbl">Key</span>
                  <input className="input" value={v.key} onChange={(e) => updateVendor(i, { key: e.target.value })} aria-label="vendor key" />
                </label>
                <label>
                  <span className="lbl">Track</span>
                  <select className="input" value={v.track} onChange={(e) => updateVendor(i, { track: e.target.value as Track })} aria-label="track">
                    {TRACKS.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="lbl">Phone</span>
                  <input className="input num" value={v.phone} onChange={(e) => updateVendor(i, { phone: e.target.value })} placeholder="+1…" autoComplete="off" aria-label="vendor phone" />
                </label>
                <label className="demo">
                  <input type="checkbox" checked={v.is_demo} onChange={(e) => updateVendor(i, { is_demo: e.target.checked })} aria-label="demo vendor" />
                  Demo
                </label>
                <button type="button" className="rm" onClick={() => removeVendor(i)} aria-label="remove vendor">
                  ×
                </button>
              </li>
            ))}
          </ul>
          <button type="button" className="add" onClick={addVendor}>
            + Add a vendor
          </button>
        </fieldset>

        <ErrorNote message={error} />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? "Saving…" : "Finish setup"}
        </button>
        <p className="auth-alt">
          <Link href="/">Skip for now</Link>
        </p>
      </form>
    </AuthShell>
  );
}
