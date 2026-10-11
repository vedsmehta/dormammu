"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { fromSlots, toSlots, type Slot } from "@/components/profile/availability";
import { AppShell, PageTitle } from "@/components/shell";
import { Button, ErrorNote, Field, Loading, Tag } from "@/components/ui";
import { ApiError, createLedgerRow, fireLedger, getCalls, getLedger, getProfile, upsertProfile, type CallWithRequest } from "@/lib/api";
import { describeError, fmtDay } from "@/lib/format";
import type { LedgerRow, Profile } from "@/lib/types";

import {
  WEEKDAYS,
  addDays,
  addMonths,
  dayNumber,
  fmtClock,
  fmtKeyLong,
  fmtKeyShort,
  monthGrid,
  monthTitle,
  sameMonth,
  todayKey,
  weekOf,
} from "./dates";
import { KIND_LABEL, availabilityEvents, callEvents, groupByDay, ledgerEvents, type CalEvent } from "./events";

type View = "month" | "week";

const MAX_PER_CELL = 3;

function EventChip({ ev }: { ev: CalEvent }) {
  const body = (
    <>
      {ev.time ? <b>{ev.time}</b> : null}
      {ev.kind === "quote" ? "Quote until: " : ev.kind === "due" ? "Due: " : ""}
      {ev.title}
      {ev.seeded ? <span className="seed">seeded</span> : null}
    </>
  );
  const title = `${KIND_LABEL[ev.kind]}: ${ev.title}${ev.detail ? ` · ${ev.detail}` : ""}${ev.seeded ? " (seeded)" : ""}`;
  if (!ev.href) {
    return (
      <span className={`cal-ev ${ev.kind}`} title={title}>
        {body}
      </span>
    );
  }
  if (ev.href.startsWith("#")) {
    return (
      <a className={`cal-ev ${ev.kind}`} href={ev.href} title={title}>
        {body}
      </a>
    );
  }
  return (
    <Link className={`cal-ev ${ev.kind}`} href={ev.href} title={title}>
      {body}
    </Link>
  );
}

function DayCell({ day, today, cursor, events, week }: { day: string; today: string; cursor: string; events: CalEvent[]; week: boolean }) {
  const cls = ["cal-day", day === today ? "today" : "", day < today ? "past" : "", !week && !sameMonth(day, cursor) ? "out" : ""]
    .filter(Boolean)
    .join(" ");
  const shown = week ? events : events.slice(0, MAX_PER_CELL);
  const extra = events.length - shown.length;
  return (
    <div className={cls} aria-label={fmtKeyLong(day)}>
      <span className="cal-num" aria-current={day === today ? "date" : undefined}>
        {dayNumber(day)}
      </span>
      {week ? <small>{fmtKeyShort(day)}</small> : null}
      <div className="cal-evs">
        {shown.map((ev) => (
          <EventChip key={ev.id} ev={ev} />
        ))}
        {extra > 0 ? <span className="cal-more">+{extra} more</span> : null}
      </div>
    </div>
  );
}

function AvailabilityEditor({ profile, onSaved }: { profile: Profile | null; onSaved: (p: Profile) => void }) {
  const [slots, setSlots] = useState<Slot[]>(() => toSlots(profile?.prefs?.availability));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  const edit = (i: number, key: keyof Slot, value: string) => {
    setSlots((prev) => prev.map((s, j) => (j === i ? { ...s, [key]: value } : s)));
    setDirty(true);
    setOk(null);
  };
  const remove = (i: number) => {
    setSlots((prev) => prev.filter((_, j) => j !== i));
    setDirty(true);
    setOk(null);
  };
  const add = () => {
    setSlots((prev) => [...prev, { start: "", end: "" }]);
    setDirty(true);
    setOk(null);
  };

  const save = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const prefs = { ...(profile?.prefs ?? {}), availability: fromSlots(slots) };
      const saved = await upsertProfile({ prefs });
      onSaved(saved);
      setSlots(toSlots(saved.prefs?.availability));
      setDirty(false);
      setOk(`Saved ${fromSlots(slots).length} ${fromSlots(slots).length === 1 ? "window" : "windows"}.`);
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="cal-panel" id="availability" aria-labelledby="cal-avail-h">
      <h3 id="cal-avail-h">Availability</h3>
      <p>The windows the agent may book into. They shade the grid and the agent reads them before offering a slot.</p>
      {slots.length === 0 ? <p className="cal-empty">No windows yet. Add one and the agent stops guessing when you are free.</p> : null}
      {slots.map((s, i) => (
        <div className="cal-win" key={i}>
          <label>
            <span className="lbl" style={{ display: "block", fontSize: 13, color: "var(--ink-2)" }}>
              From
            </span>
            <input className="input" type="datetime-local" value={s.start} onChange={(e) => edit(i, "start", e.target.value)} />
          </label>
          <label>
            <span className="lbl" style={{ display: "block", fontSize: 13, color: "var(--ink-2)" }}>
              To
            </span>
            <input className="input" type="datetime-local" value={s.end} min={s.start || undefined} onChange={(e) => edit(i, "end", e.target.value)} />
          </label>
          <button type="button" className="x" onClick={() => remove(i)} aria-label={`Remove window ${i + 1}`}>
            ×
          </button>
        </div>
      ))}
      <div className="cal-acts">
        <Button variant="line" size="sm" onClick={add} disabled={busy}>
          Add window
        </Button>
        <Button size="sm" onClick={save} disabled={busy || !dirty || !profile}>
          {busy ? "Saving…" : "Save availability"}
        </Button>
        {ok ? <span className="cal-ok">{ok}</span> : null}
      </div>
      {!profile ? <p className="cal-empty">Profile not loaded, so saving is off until it is.</p> : null}
      <ErrorNote message={error} />
    </section>
  );
}

function BookForm({ today, onBooked }: { today: string; onBooked: () => void }) {
  const [label, setLabel] = useState("");
  const [date, setDate] = useState(today);
  const [time, setTime] = useState("");
  const [vendor, setVendor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const submit = async () => {
    if (busy || !label.trim() || !date) return;
    setBusy(true);
    setError(null);
    setNote(null);
    const full = [label.trim(), time ? `at ${fmtClock(time)}` : "", vendor.trim() ? `· ${vendor.trim()}` : ""].filter(Boolean).join(" ");
    try {
      await createLedgerRow({ kind: "appointment", label: full, effective_on: date, expires_on: date, amount_usd: null });
      setNote(`Booked ${full} on ${fmtDay(date)}.`);
      setLabel("");
      setTime("");
      setVendor("");
      onBooked();
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
        setNote("Manual bookings arrive with the next API deploy. Nothing was saved.");
      } else {
        setError(describeError(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="cal-panel cal-form" aria-labelledby="cal-book-h">
      <h3 id="cal-book-h">Book manually</h3>
      <p>Add an appointment the agent did not make. It lands in the ledger as a dated appointment.</p>
      <Field label="What" tight>
        <input type="text" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Dentist cleaning" />
      </Field>
      <div className="fields-2">
        <Field label="Date" tight>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Time (optional)" tight>
          <input type="text" className="input" value={time} onChange={(e) => setTime(e.target.value)} placeholder="14:30" pattern="[0-9]{1,2}:[0-9]{2}" />
        </Field>
      </div>
      <Field label="Vendor (optional)" tight>
        <input type="text" value={vendor} onChange={(e) => setVendor(e.target.value)} placeholder="Palo Alto Dental" />
      </Field>
      <div className="cal-acts">
        <Button size="sm" onClick={submit} disabled={busy || !label.trim() || !date}>
          {busy ? "Booking…" : "Book"}
        </Button>
        {note ? <span className="cal-ok">{note}</span> : null}
      </div>
      <ErrorNote message={error} />
    </section>
  );
}

/**
 * Month and week calendar over real data: confirmed and pencilled slots from schedule calls, dated
 * ledger facts, and the user's availability windows. Nothing is invented; empty cells stay empty.
 */
export function CalendarView() {
  const router = useRouter();
  const [today, setToday] = useState<string>("");
  const [cursor, setCursor] = useState<string>("");
  const [view, setView] = useState<View>("month");
  const [calls, setCalls] = useState<CallWithRequest[] | null>(null);
  const [ledger, setLedger] = useState<LedgerRow[] | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [reloadTick, setReloadTick] = useState(0);
  const [firing, setFiring] = useState(false);
  const [fireError, setFireError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const t = todayKey();
    Promise.allSettled([getCalls(500), getLedger(), getProfile()]).then(([c, l, p]) => {
      if (cancelled) return;
      const errs: string[] = [];
      setToday(t);
      setCursor((prev) => prev || t);
      if (c.status === "fulfilled") setCalls(c.value);
      else {
        setCalls([]);
        errs.push(`Calls: ${describeError(c.reason)}`);
      }
      if (l.status === "fulfilled") setLedger(l.value);
      else {
        setLedger([]);
        errs.push(`Ledger: ${describeError(l.reason)}`);
      }
      if (p.status === "fulfilled") setProfile(p.value);
      else errs.push(`Profile: ${describeError(p.reason)}`);
      setLoadErrors(errs);
    });
    return () => {
      cancelled = true;
    };
  }, [reloadTick]);

  const events = useMemo(
    () => [...callEvents(calls ?? []), ...ledgerEvents(ledger ?? []), ...availabilityEvents(profile?.prefs?.availability)],
    [calls, ledger, profile],
  );
  const byDay = useMemo(() => groupByDay(events), [events]);

  const rows = useMemo(() => {
    if (!cursor) return [];
    return view === "month" ? monthGrid(cursor) : [weekOf(cursor)];
  }, [cursor, view]);

  const visibleDays = useMemo(() => rows.flat().filter((d) => view === "week" || sameMonth(d, cursor)), [rows, view, cursor]);
  const visible = useMemo(() => visibleDays.flatMap((d) => byDay.get(d) ?? []), [visibleDays, byDay]);
  const nAppt = visible.filter((e) => e.kind === "appointment").length;
  const nPencilled = visible.filter((e) => e.kind === "pencilled").length;
  const dueRows = useMemo(() => (ledger ?? []).filter((r) => r.expires_on && today && r.expires_on <= today), [ledger, today]);

  const next14 = useMemo(() => {
    if (!today) return [];
    return Array.from({ length: 14 }, (_, i) => addDays(today, i))
      .map((d) => ({ day: d, events: (byDay.get(d) ?? []).filter((e) => e.kind !== "avail") }))
      .filter((d) => d.events.length > 0);
  }, [today, byDay]);

  const loading = calls === null || ledger === null;
  const nothingAtAll = !loading && events.length === 0;

  const fire = async () => {
    if (firing || !today || dueRows.length === 0) return;
    setFiring(true);
    setFireError(null);
    try {
      const run = await fireLedger(today);
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setFireError(describeError(e));
      setFiring(false);
    }
  };

  const move = (n: number) => setCursor((c) => (view === "month" ? addMonths(c, n) : addDays(c, 7 * n)));
  const rangeTitle = view === "month" ? monthTitle(cursor) : `Week of ${fmtKeyLong(weekOf(cursor)[0])}`;

  return (
    <AppShell
      wide
      head={
        <PageTitle
          title="Calendar"
          lede="Every appointment the calls booked, every date the ledger is watching, and the windows you told the agent you are free. Times are Pacific."
        />
      }
    >
      {loadErrors.map((m) => (
        <ErrorNote key={m} message={m} />
      ))}
      {loading ? <Loading>Loading calendar…</Loading> : null}

      {!loading && cursor ? (
        <>
          <div className="cal-head">
            <h2>
              {rangeTitle}
              <span>
                {view === "month"
                  ? `${nAppt} ${nAppt === 1 ? "appointment" : "appointments"} · ${nPencilled} pencilled · ${dueRows.length} due as of today`
                  : `${nAppt} ${nAppt === 1 ? "appointment" : "appointments"} · ${nPencilled} pencilled this week · ${dueRows.length} due as of today`}
              </span>
            </h2>
            <div className="cal-ctl">
              <div className="cal-seg" role="group" aria-label="View">
                <button type="button" aria-pressed={view === "month"} onClick={() => setView("month")}>
                  Month
                </button>
                <button type="button" aria-pressed={view === "week"} onClick={() => setView("week")}>
                  Week
                </button>
              </div>
              <Button variant="line" size="sm" onClick={() => move(-1)}>
                ← Prev
              </Button>
              <Button variant="line" size="sm" onClick={() => setCursor(today)} disabled={cursor === today}>
                Today
              </Button>
              <Button variant="line" size="sm" onClick={() => move(1)}>
                Next →
              </Button>
            </div>
          </div>

          <div className={`cal-grid${view === "week" ? " cal-week-view" : ""}`} role="grid" aria-label={rangeTitle}>
            <div className="cal-dow" role="row">
              {WEEKDAYS.map((d) => (
                <div key={d} role="columnheader">
                  {d}
                </div>
              ))}
            </div>
            {rows.map((week) => (
              <div className="cal-week" role="row" key={week[0]}>
                {week.map((day) => (
                  <DayCell key={day} day={day} today={today} cursor={cursor} events={byDay.get(day) ?? []} week={view === "week"} />
                ))}
              </div>
            ))}
          </div>

          <div className="cal-legend" aria-label="Legend">
            <span>
              <i className="k appointment" style={{ width: 10, height: 10, borderRadius: 3, background: "var(--mint)", display: "inline-block" }} /> Confirmed appointment
            </span>
            <span>
              <i style={{ width: 10, height: 10, borderRadius: 3, boxShadow: "inset 0 0 0 1.5px var(--deep)", display: "inline-block" }} /> Pencilled (unconfirmed)
            </span>
            <span>
              <i style={{ width: 10, height: 10, borderRadius: 3, background: "rgba(194,122,0,0.35)", display: "inline-block" }} /> Due (promo end, renewal)
            </span>
            <span>
              <i style={{ width: 10, height: 10, borderRadius: 3, background: "rgba(42,26,49,0.12)", display: "inline-block" }} /> Quote valid until
            </span>
            <span>
              <i style={{ width: 10, height: 10, borderRadius: 3, background: "rgba(18,120,108,0.25)", display: "inline-block" }} /> Your availability
            </span>
          </div>

          {nothingAtAll ? (
            <p className="cal-empty">
              Nothing on the calendar yet. A confirmed slot from a scheduling call, a promo end or renewal from the ledger, and any availability
              window you add below will appear here.
            </p>
          ) : null}

          <div className="cal-two">
            <section aria-labelledby="cal-next-h">
              <h3 id="cal-next-h" style={{ font: "600 22px/1.2 var(--display)", letterSpacing: "-0.01em" }}>
                Next 14 days
              </h3>
              {next14.length === 0 ? (
                <p className="cal-empty">
                  Nothing dated between {fmtKeyShort(today)} and {fmtKeyShort(addDays(today, 13))}. Appointments the agent books and ledger dates
                  that fall in this window will list here.
                </p>
              ) : (
                <ol className="cal-list">
                  {next14.map(({ day, events: evs }) => (
                    <li key={day}>
                      <time className={day === today ? "today" : undefined} dateTime={day}>
                        {day === today ? "Today" : fmtKeyLong(day)}
                      </time>
                      <div>
                        {evs.map((ev) => (
                          <div className="row" key={ev.id}>
                            <span className={`k ${ev.kind}`} aria-hidden />
                            {ev.time ? <span className="num">{ev.time}</span> : null}
                            {ev.href && !ev.href.startsWith("#") ? <Link href={ev.href}>{ev.title}</Link> : <span>{ev.title}</span>}
                            <span className="sub">
                              {KIND_LABEL[ev.kind]}
                              {ev.detail ? ` · ${ev.detail}` : ""}
                            </span>
                            {ev.seeded ? (
                              <Tag tone="line" first>
                                seeded
                              </Tag>
                            ) : null}
                          </div>
                        ))}
                      </div>
                    </li>
                  ))}
                </ol>
              )}

              <section className="cal-panel" style={{ marginTop: 28 }} aria-labelledby="cal-due-h">
                <h3 id="cal-due-h">Due as of {fmtDay(today)}</h3>
                <p>
                  {dueRows.length === 0
                    ? "No promo end, renewal or quote has passed its date yet. When one does, it lists here and can fire a retention run."
                    : `${dueRows.length} ${dueRows.length === 1 ? "row has" : "rows have"} reached its date. Firing opens a run with the prior quote as context.`}
                </p>
                {dueRows.length > 0 ? (
                  <ul className="cal-list" style={{ marginTop: 12 }}>
                    {dueRows.map((r) => (
                      <li key={r.id}>
                        <time dateTime={r.expires_on ?? undefined}>{fmtDay(r.expires_on)}</time>
                        <div className="row">
                          <span className={`k ${r.kind === "quote" ? "quote" : "due"}`} aria-hidden />
                          <span>{r.label}</span>
                          <span className="sub">{r.kind.replace("_", " ")}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="cal-acts">
                  <Button size="sm" onClick={fire} disabled={firing || dueRows.length === 0}>
                    {firing ? "Firing…" : `Fire due ledger (${dueRows.length})`}
                  </Button>
                  <Link href="/ledger" className="more">
                    Open the ledger
                  </Link>
                </div>
                <ErrorNote message={fireError} />
              </section>
            </section>

            <div>
              <AvailabilityEditor key={profile?.id ?? "none"} profile={profile} onSaved={setProfile} />
              <BookForm today={today} onBooked={() => setReloadTick((n) => n + 1)} />
            </div>
          </div>
        </>
      ) : null}
    </AppShell>
  );
}
