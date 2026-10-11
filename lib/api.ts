import { getSupabaseBrowser } from "@/lib/supabase/client";
import type { AssembledRun, Call, LedgerRow, Profile, Proposal, RunListItem, Vendor } from "@/lib/types";

// Browser-side client for the Python service. Every call goes to /api/py/..., which next.config
// rewrites to uvicorn in dev and to the Vercel Python function in production. The Supabase access
// token rides along as a Bearer; in dev mode (no Supabase) nothing is attached and the Python side
// falls back to DEV_USER_ID.

export class ApiError extends Error {
  status: number;
  body: string;

  constructor(status: number, body: string, path: string) {
    super(`${status} from ${path}: ${body.slice(0, 200) || "(empty body)"}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function bearer(): Promise<string | null> {
  const supabase = getSupabaseBrowser();
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}

export async function pyFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = await bearer();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch("/api/py" + path, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body, path);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return pyFetch<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export const getRuns = () => pyFetch<RunListItem[]>("/runs");
export const createRun = (request: string) => post<AssembledRun>("/runs", { request });
// Research first: nothing is dialed until the user approves items on the proposal.
export const research = (request: string) => post<Proposal>("/research", { request });
export const getProposal = (id: string) => pyFetch<Proposal>(`/proposals/${encodeURIComponent(id)}`);
export const approveProposal = (id: string, item_ids: string[]) =>
  post<{ run: AssembledRun; skipped: { item_id: string; reason: string }[]; external_calls_enabled: boolean }>(
    `/proposals/${encodeURIComponent(id)}/approve`,
    { item_ids },
  );
export const rejectProposal = (id: string) => post<Proposal>(`/proposals/${encodeURIComponent(id)}/reject`, {});
/** "Which ones do you want to go with?": one booking call per chosen option, as a new run. */
export const chooseOptions = (runId: string, call_ids: string[]) =>
  post<{ run: AssembledRun; skipped: { call_id: string; reason: string }[] }>(`/runs/${encodeURIComponent(runId)}/choose`, { call_ids });
export const getRun = (id: string) => pyFetch<AssembledRun>(`/runs/${encodeURIComponent(id)}`);
export const tickRun = (id: string) => post<AssembledRun>(`/runs/${encodeURIComponent(id)}/tick`, {});

export const getVendors = () => pyFetch<Vendor[]>("/vendors");
export const upsertVendors = (vendors: Vendor[]) => post<Vendor[]>("/vendors", { vendors });

export const getProfile = () => pyFetch<Profile>("/profile");
export const upsertProfile = (fields: Partial<Omit<Profile, "id" | "created_at">>) =>
  post<Profile>("/profile", fields);

export const getLedger = () => pyFetch<LedgerRow[]>("/ledger");
export const fireLedger = (as_of: string) => post<AssembledRun>("/ledger/fire", { as_of });

// Password sign-up goes through the Python service (GoTrue admin API with email_confirm set), so no
// email is ever sent and no Bearer is attached: there is no session yet. 409 means the account exists.
export type SignupResult = { id: string; email: string };
export async function signup(email: string, password: string, display_name?: string): Promise<SignupResult> {
  const res = await fetch("/api/py/auth/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, display_name: display_name || null }),
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body, "/auth/signup");
  }
  return (await res.json()) as SignupResult;
}

// There is no cross-run calls endpoint and GET /runs returns bare run rows, so list views compose:
// the run rows, then the assembled run (calls + aggregate) for each of the most recent ones. Runs
// whose fetch fails are left out rather than failing the whole list.
export async function assembleRuns(runs: RunListItem[], limit = 20): Promise<AssembledRun[]> {
  const recent = runs.slice(0, limit);
  const settled = await Promise.allSettled(recent.map((r) => getRun(r.id)));
  return settled.flatMap((s) => (s.status === "fulfilled" ? [s.value] : []));
}

export async function getRecentRuns(limit = 20): Promise<AssembledRun[]> {
  const runs = await getRuns();
  return assembleRuns(runs, limit);
}

// ---------------------------------------------------------------------------------------------
// Past calls, evidence, accuracy and insights (read-only routes)
// ---------------------------------------------------------------------------------------------

/** A calls row from GET /calls: the run's request rides along and the phone is already masked. */
export type CallWithRequest = Call & { request: string | null };

/** The user's calls across every run, newest first. Replaces composing getRuns + getRun for lists. */
export const getCalls = (limit = 200, offset = 0) =>
  pyFetch<CallWithRequest[]>(`/calls?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`);

export type RealDialCall = Pick<
  Call,
  "id" | "run_id" | "track" | "target_name" | "target_phone" | "dial_call_id" | "state" | "termination_type" | "duration_seconds" | "started_at" | "ended_at"
>;

export interface EvidenceReport {
  dial: {
    account: {
      configured: boolean;
      limits: Record<string, unknown> | null;
      max_call_duration_seconds: number | null;
      from_number: string | null;
      numbers: { number: string | null; nickname: string | null; country: string | null; setup_status: string | null; calling_enabled: boolean | null }[];
      api_keys: number | null;
      error: string | null;
    };
    real_calls: RealDialCall[];
    n_real_calls: number;
    n_simulated_calls: number;
    n_undialed_calls: number;
  };
  keewano: {
    enabled: boolean;
    token_kind: string;
    status: Record<string, unknown> | null;
    last_error: string | null;
    last_status: unknown;
    event_types: Record<string, string> | null;
    companion_types: Record<string, string> | null;
    fallback_event_type_id: string | null;
    ingress_url: string | null;
    auth_hint: string | null;
  };
  /** The self-improvement loop: calls that opened with what earlier calls learned. */
  loop?: {
    n_calls: number;
    n_with_prior_context: number;
    n_context_applied_events: number;
    examples: { call_id: string | null; run_id: string | null; target_name: string | null; track: string | null; prior_context: string; simulated: boolean }[];
    context_effect: {
      n_repeat_vendors?: number;
      n_later_calls?: number;
      n_later_with_prior_context?: number;
      quote?: { n_vendors?: number; later_lower_by_usd?: number | null } | null;
      negotiate?: { n_vendors?: number; later_saved_more_usd?: number | null } | null;
    } | null;
    n_recommendations: number | null;
    error: string | null;
  };
  events: {
    by_type: Record<string, number>;
    recent: { id: string | null; type: string | null; created_at: string | null; run_id: string | null; call_id: string | null; data: string; truncated: boolean }[];
  };
  calls: { by_state: Record<string, number>; by_simulated: Record<string, number> };
  policy: {
    ai_disclosure: string | null;
    summary_closer: string | null;
    only_vendor_rows_dialable: boolean;
    multiplier_label: string;
    dial_overhead_seconds: number | null;
    max_calls_per_run: number;
    system_of_record: string;
    analysis_sink: string;
  };
  build: { commit: string | null; deployed_at: string | null; python: string; store: string };
  generated_at: string;
}

/** GET /evidence calls Dial, so it is fetched once per page load and never polled. */
export const getEvidence = () => pyFetch<EvidenceReport>("/evidence");

export interface AccuracyCore {
  tracks: Record<string, { n_done: number; n_with_value: number; success_rate: number | null }>;
  overall: {
    n_calls: number;
    n_done: number;
    n_failed: number;
    n_terminal: number;
    n_simulated: number;
    answer_rate: number | null;
    voicemail_rate: number | null;
    no_answer_rate: number | null;
    avg_duration_seconds: number | null;
    extraction_coverage: number | null;
  };
  recap_agreement: { checked: number; agreed: number; rate: number | null; disagreed_call_ids: string[]; definition?: string };
}

export interface EvalCase {
  id: string;
  track: string;
  scenario: string;
  exact: boolean;
  partial: number;
  fields: Record<string, boolean>;
  expected: Record<string, unknown>;
  actual: Record<string, unknown> | null;
  has_recap: boolean;
  recap_disagrees: boolean;
  recap_agrees: boolean | null;
  error: string | null;
  seconds: number;
}

/** public/eval-report.json, written by the extraction eval. */
export interface EvalReport {
  generated_at: string;
  model: string;
  n: number;
  exact_rate: number;
  field_rates: Record<string, number>;
  by_track: Record<string, { n: number; exact_rate: number; field_rates: Record<string, number> }>;
  recap: { checked: number; agreed: number; rate: number | null };
  errors: number;
  tolerances: Record<string, string>;
  fixtures: { dir: string; no_recap: number; recap_disagrees: number };
  cases: EvalCase[];
}

export interface AccuracyReport extends AccuracyCore {
  split: { real: AccuracyCore; simulated: AccuracyCore };
  eval: EvalReport | null;
  note: string;
  cap: number;
  generated_at: string;
}

export const getAccuracy = () => pyFetch<AccuracyReport>("/accuracy");

/** The static eval report next to the app, shown even when /accuracy cannot read it. */
export async function getEvalReport(): Promise<EvalReport | null> {
  const res = await fetch("/eval-report.json", { cache: "no-store" });
  if (!res.ok) return null;
  return (await res.json()) as EvalReport;
}

export interface VendorScorecard {
  vendor_key: string;
  name: string;
  track: string | null;
  n_calls: number;
  n_done: number;
  n_failed: number;
  n_live: number;
  n_simulated: number;
  n_with_prior_context: number;
  failure_breakdown: Record<string, number>;
  answer_rate: number | null;
  avg_duration_seconds: number | null;
  first_called_at: string | null;
  last_called_at: string | null;
  last_outcome: Record<string, unknown> | null;
  n_with_outcome: number;
  n_quotes?: number;
  best_quote_usd?: number | null;
  latest_quote_usd?: number | null;
  first_quote_usd?: number | null;
  avg_quote_usd?: number | null;
  quote_trend?: string | null;
  n_compared?: number;
  times_cheapest?: number;
  n_reduced?: number;
  n_canceled?: number;
  n_refused?: number;
  reduction_rate?: number | null;
  avg_saved_usd?: number | null;
  total_saved_usd_per_month?: number | null;
  n_confirmed?: number;
  confirmed_rate?: number | null;
  takeaway?: string | null;
  [key: string]: unknown;
}

export interface TrackStat {
  n_calls: number;
  n_done: number;
  n_failed: number;
  n_runs: number;
  answer_rate: number | null;
  avg_calls_per_run: number | null;
  avg_talk_seconds: number | null;
  total_talk_seconds: number;
  avg_wall_clock_seconds_per_run: number | null;
  avg_multiplier_est: number | null;
  multiplier_label: string;
  dial_overhead_seconds: number;
  median_outcome: number | null;
  median_outcome_label: string;
}

export interface Recommendation {
  kind: string;
  text: string;
  score: number;
  vendor_key: string | null;
  track: string | null;
  evidence: Record<string, unknown>;
}

export interface InsightsReport {
  generated_at: string;
  coverage: {
    n_runs: number;
    n_runs_seeded: number;
    runs_truncated: boolean;
    max_runs: number;
    n_calls: number;
    n_done: number;
    n_failed: number;
    n_live: number;
    n_simulated: number;
    n_seeded: number;
    share_simulated: number | null;
    share_seeded: number | null;
    share_synthetic: number | null;
    label: string;
    n_events: number;
    n_events_seeded: number;
    events_truncated: boolean;
    first_call_at: string | null;
    last_call_at: string | null;
    vendors: string[];
  };
  vendor_scorecards: Record<string, VendorScorecard>;
  track_stats: Record<string, TrackStat>;
  failure_modes: {
    n_failed: number;
    n_terminal: number;
    failure_rate: number | null;
    counts: Record<string, number>;
    share: Record<string, number | null>;
    by_track: Record<string, Record<string, number>>;
  };
  time_of_day: {
    timezone: string;
    n_calls: number;
    enough_data: boolean;
    min_calls: number;
    buckets: { bucket: string; label: string; hours: [number, number]; n_calls: number; n_answered: number; n_voicemail: number; answer_rate: number | null }[];
    best_bucket: string | null;
    worst_bucket: string | null;
  };
  context_effect: {
    n_repeat_vendors: number;
    n_first_calls: number;
    n_later_calls: number;
    n_later_with_prior_context: number;
    share_later_with_prior_context: number | null;
    first_answer_rate: number | null;
    later_answer_rate: number | null;
    quote: { n_vendors: number; n_later_calls: number; avg_first_usd: number | null; avg_later_usd: number | null; later_lower_by_usd: number | null };
    negotiate: { n_vendors: number; n_later_calls: number; avg_first_saved_usd: number | null; avg_later_saved_usd: number | null; later_saved_more_usd: number | null };
    schedule: { n_vendors: number; n_later_calls: number; first_confirmed_rate: number | null; later_confirmed_rate: number | null };
  };
  recommendations: Recommendation[];
  honesty?: { source: string; estimates: string[]; note: string };
}

export const getInsights = () => pyFetch<InsightsReport>("/insights");

// ---------------------------------------------------------------------------------------------
// Calendar: manual bookings
// ---------------------------------------------------------------------------------------------

/**
 * POST /ledger with one row (kind appointment from the calendar's "Book manually" form). The route
 * may not exist on the deployed API yet; callers treat a 404 or 405 ApiError as "not deployed".
 */
export const createLedgerRow = (row: import("@/lib/types").NewLedgerRow) => post<LedgerRow>("/ledger", row);
