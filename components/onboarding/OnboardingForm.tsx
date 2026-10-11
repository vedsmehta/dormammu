"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AvailabilityEditor, checkSlots } from "@/components/profile/AvailabilityEditor";
import { AuthShell, Progress } from "@/components/shell";
import { ErrorNote, Field, FieldError } from "@/components/ui";
import { getProfile, getVendors, upsertProfile, upsertVendors } from "@/lib/api";
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

import { checkNumber, checkPhone, checkZip, collect, countNote, focusFirstInvalid, normalizePhone } from "@/lib/validate";

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

/** Street, city, state, zip as four underline fields. */
function AddressFields({ legend, value, onChange, zipError }: { legend: string; value: AddressDraft; onChange: (a: AddressDraft) => void; zipError?: string }) {
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
      <Field label="ZIP" tight error={zipError}>
        <input type="text" value={value.zip} onChange={(e) => set({ zip: e.target.value })} autoComplete="postal-code" inputMode="numeric" className="num" />
      </Field>
    </fieldset>
  );
}

/** A row in "what you pay for now". A row with no name would be dropped, so say so instead. */
function checkProvider(p: ProviderDraft): string | null {
  const started = Boolean(p.monthly_usd.trim() || p.contract_end);
  if (!p.name.trim()) return started ? "Add the provider's name, or remove this row." : null;
  return checkNumber(p.monthly_usd, { min: 0, what: "The monthly cost" });
}

/** True when a roster row has a name and key but no phone yet: allowed, but not saved or dialable. */
const lacksPhone = (v: Vendor) => Boolean(v.name.trim() && v.key.trim() && !v.phone.trim());

/** A roster row. Fully blank rows are ignored; a half-filled one says what is missing. */
function checkVendor(v: Vendor, all: Vendor[]): string | null {
  const name = v.name.trim();
  const key = v.key.trim();
  const phone = v.phone.trim();
  if (!name && !key && !phone) return null;
  if (!name) return "Add the vendor's name, or remove this row.";
  if (!key) return "Add a short key for this vendor, like movers_a.";
  if (!/^[a-z0-9_-]+$/i.test(key)) return "The key can only use letters, numbers, _ and -.";
  if (all.filter((o) => o.key.trim().toLowerCase() === key.toLowerCase()).length > 1) return "Two vendors share this key. Each key must be different.";
  return checkPhone(phone);
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
  const [submitted, setSubmitted] = useState(false);
  const formRef = useRef<HTMLFormElement>(null);

  const validate = () =>
    collect({
      displayName: displayName.trim().length > 120 ? "Keep the name under 120 characters." : null,
      phone: checkPhone(phone),
      currentZip: checkZip(currentAddress.zip),
      newZip: checkZip(newAddress.zip),
      household: checkNumber(householdSize, { min: 1, whole: true, what: "The household size" }),
      budget: checkNumber(budget, { min: 0, what: "The budget ceiling" }),
      ...Object.fromEntries(providers.map((p, i) => [`prov.${i}`, checkProvider(p)])),
      ...Object.fromEntries(vendors.map((v, i) => [`ven.${i}`, checkVendor(v, vendors)])),
      ...checkSlots(slots),
    });
  // Nothing is flagged until the first save attempt; after that, messages follow the typing.
  const errors = submitted ? validate() : {};

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

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setSubmitted(true);
    if (Object.keys(validate()).length) {
      setError(null);
      focusFirstInvalid(formRef.current);
      return;
    }
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
        phone: normalizePhone(phone),
        cohort,
        prefs,
      });
      // The service requires a phone on every vendor, so rows without one stay in the form only.
      const roster = vendors
        .filter((v) => v.key.trim() && v.name.trim() && v.phone.trim())
        .map((v) => ({
          key: v.key.trim(),
          name: v.name.trim(),
          track: v.track,
          phone: normalizePhone(v.phone) ?? v.phone.trim(),
          is_demo: Boolean(v.is_demo),
        }));
      if (roster.length) await upsertVendors(roster);
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

      <form ref={formRef} onSubmit={save} noValidate>
        <h1>Set up your profile</h1>
        <p>
          This is what the agent knows about you before it researches or calls anyone. Your profile decides which discounts it asks for,
          and it only claims what you tell it here. Phone numbers never go into git, and the UI masks them.
        </p>
        {loadNote ? <p className="note warn">{loadNote}</p> : null}
        {!loaded ? <p className="dash-note">Loading…</p> : null}

        <div className="fields-2">
          <Field label="Name the agent calls on behalf of" error={errors.displayName}>
            <input type="text" value={displayName} onChange={(e) => setDisplayName(e.target.value)} autoComplete="name" placeholder="Maya Patel" />
          </Field>
          <Field label="Your mobile" hint="For transfers and callbacks." error={errors.phone}>
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
        <Field label="Proof of student status" hint="What the agent can say is on file, e.g. “school.edu email on file”.">
          <input type="text" value={studentProof} onChange={(e) => setStudentProof(e.target.value)} placeholder="school.edu email on file" />
        </Field>

        <AddressFields legend="Where you live now" value={currentAddress} onChange={setCurrentAddress} zipError={errors.currentZip} />
        <AddressFields legend="Where you are moving" value={newAddress} onChange={setNewAddress} zipError={errors.newZip} />

        <div className="fields-2">
          <Field label="Move date">
            <input type="date" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} className="num" />
          </Field>
          <Field label="People in the household" error={errors.household}>
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
                  <input className="input" value={p.name} onChange={(e) => updateProvider(i, { name: e.target.value })} aria-label="provider name" aria-invalid={(Boolean(errors[`prov.${i}`]) && !p.name.trim()) || undefined} />
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
                  <input className="input num" type="number" min={0} step={1} value={p.monthly_usd} onChange={(e) => updateProvider(i, { monthly_usd: e.target.value })} aria-label="monthly cost" aria-invalid={(Boolean(errors[`prov.${i}`]) && Boolean(p.name.trim())) || undefined} />
                </label>
                <label>
                  <span className="lbl">Contract ends</span>
                  <input className="input num" type="date" value={p.contract_end} onChange={(e) => updateProvider(i, { contract_end: e.target.value })} aria-label="contract end" />
                </label>
                <span />
                <button type="button" className="rm" onClick={() => removeProvider(i)} aria-label="remove provider">
                  ×
                </button>
                <FieldError message={errors[`prov.${i}`]} />
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
          <Field label="Budget ceiling (USD)" error={errors.budget}>
            <input type="number" min={0} step={1} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="900" className="num" />
          </Field>
        </div>

        <AvailabilityEditor slots={slots} onChange={setSlots} errors={errors} />

        <fieldset className="field">
          <legend>I already know who to call</legend>
          <p className="hint">
            Optional. Only rows here are dialable: the router picks by key and never sees a number. Today every number belongs to someone
            in the room.
          </p>
          <ul className="roster">
            {vendors.map((v, i) => {
              const err = errors[`ven.${i}`];
              // Point at the first thing wrong in the row: name, then key, then phone.
              const bad = !err ? null : !v.name.trim() ? "name" : /key/i.test(err) ? "key" : "phone";
              return (
              <li key={i}>
                <label>
                  <span className="lbl">Name</span>
                  <input className="input" value={v.name} onChange={(e) => updateVendor(i, { name: e.target.value })} aria-label="vendor name" aria-invalid={bad === "name" || undefined} />
                </label>
                <label>
                  <span className="lbl">Key</span>
                  <input className="input" value={v.key} onChange={(e) => updateVendor(i, { key: e.target.value })} aria-label="vendor key" aria-invalid={bad === "key" || undefined} />
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
                  <input className="input num" value={v.phone} onChange={(e) => updateVendor(i, { phone: e.target.value })} placeholder="+1…" autoComplete="off" aria-label="vendor phone" aria-invalid={bad === "phone" || undefined} />
                </label>
                <label className="demo">
                  <input type="checkbox" checked={v.is_demo} onChange={(e) => updateVendor(i, { is_demo: e.target.checked })} aria-label="demo vendor" />
                  Demo
                </label>
                <button type="button" className="rm" onClick={() => removeVendor(i)} aria-label="remove vendor">
                  ×
                </button>
                {err ? <FieldError message={err} /> : lacksPhone(v) ? <span className="hint">No phone yet, so this vendor will not be saved or called.</span> : null}
              </li>
              );
            })}
          </ul>
          <button type="button" className="add" onClick={addVendor}>
            + Add a vendor
          </button>
        </fieldset>

        <ErrorNote message={error ?? countNote(errors)} />
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
