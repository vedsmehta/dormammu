// TS mirror of docs/CONTRACT.md row shapes. Column names == JSON keys, snake_case.

export type Track = "quote" | "schedule" | "negotiate";

/** Plain-language names for the three call tracks. */
export const TRACK_LABEL: Record<Track, string> = {
  quote: "Quotes",
  schedule: "Appointments",
  negotiate: "Negotiations",
};

export const TRACK_ORDER: Track[] = ["quote", "schedule", "negotiate"];

export function trackLabel(track: string | null | undefined): string {
  return (track && TRACK_LABEL[track as Track]) || (track ?? "other");
}

export type CallState = "planned" | "dialing" | "ringing" | "talking" | "extracting" | "done" | "failed";

export const STATE_RANK: Record<CallState, number> = {
  planned: 0,
  dialing: 1,
  ringing: 2,
  talking: 3,
  extracting: 4,
  done: 6,
  failed: 6,
};

export const STATE_ORDER: CallState[] = ["planned", "dialing", "ringing", "talking", "extracting", "done"];

export function isTerminal(state: string | null | undefined): boolean {
  return state === "done" || state === "failed";
}

export type RunStatus = "dispatching" | "running" | "complete" | "failed";

export interface Run {
  id: string;
  user_id: string;
  request: string;
  status: RunStatus;
  created_at: string;
  completed_at: string | null;
}

// GET /api/py/runs returns run rows. A backend may attach a call count or the calls themselves.
export interface RunListItem extends Run {
  n_calls?: number;
  calls?: Call[];
}

export interface TranscriptTurn {
  speaker: string;
  text: string;
}

export interface QuoteOutcome {
  track: "quote";
  amount_usd: number | null;
  unit: string | null;
  caveats: string[];
  note: string | null;
}

export interface ScheduleOutcome {
  track: "schedule";
  slot_iso: string | null;
  confirmed: boolean;
  note: string | null;
}

export type NegotiateAction = "reduced" | "canceled" | "refused" | "none";

export interface NegotiateOutcome {
  track: "negotiate";
  action: NegotiateAction;
  saved_usd: number | null;
  effective_date: string | null;
  note: string | null;
}

export type Outcome = QuoteOutcome | ScheduleOutcome | NegotiateOutcome;

export interface Call {
  id: string;
  run_id: string;
  vendor_id: string | null;
  vendor_key: string | null;
  track: Track;
  target_name: string;
  target_phone: string;
  objective: string;
  prior_context: string | null;
  dial_call_id: string | null;
  state: CallState;
  state_rank: number;
  termination_type: string | null;
  transcript: string | null;
  transcript_turns: TranscriptTurn[] | null;
  outcome: Outcome | null;
  simulated: boolean;
  error: string | null;
  duration_seconds: number | null;
  started_at: string;
  ended_at: string | null;
}

export type RankedCall = Call & { best?: boolean };

export interface AggregateView {
  tracks: {
    quote?: RankedCall[];
    schedule?: RankedCall[];
    negotiate?: RankedCall[];
  };
  wall_clock_seconds: number | null;
  talk_seconds: number;
  sequential_estimate_seconds: number | null;
  multiplier: number | null;
  dial_overhead_seconds: number;
  n_calls: number;
  n_completed: number;
  n_failed: number;
  n_simulated: number;
  n_live: number;
}

/** A follow-up booking call placed for one of this run's options. */
export interface BookingLink {
  call_id: string;
  run_id: string;
  state: string | null;
  slot_iso: string | null;
  confirmed: boolean;
}

export interface AssembledRun extends Run {
  calls: Call[];
  aggregate: AggregateView | null;
  server_now: string;
  /** Options from this run the user chose, each with its booking call. */
  bookings?: BookingLink[];
  /** Set when this run is itself a booking: the run the options came from. */
  booking_of?: { run_id: string | null } | null;
}

// --- Research-first flow (docs/CONTRACT.md "Proposals"). Everything is optional: the engine's output is
// --- read tolerantly, the way the server reads it.

export interface ProposalCitation {
  url?: string | null;
  title?: string | null;
}

export interface ProposalPlan {
  name?: string | null;
  monthly_usd?: number | null;
  one_time_usd?: number | null;
  term?: string | null;
}

export interface ProposalDiscount {
  label?: string | null;
  amount?: string | null;
  eligibility?: string | null;
}

export interface ProposalItem {
  id: string;
  category?: string | null;
  track?: Track | null;
  source?: "research" | "roster" | string | null;
  recommended?: boolean;
  phone_verified?: boolean;
  skip_reason?: string | null;
  why?: string | null;
  vendor?: { name?: string | null; phone?: string | null; website?: string | null; address?: string | null; is_demo?: boolean } | null;
  plan?: ProposalPlan | null;
  plans?: ProposalPlan[] | null;
  discounts?: ProposalDiscount[] | null;
  strategy?: { label?: string | null; opening?: string | null; asks?: string[] | null; walk_away?: string | null; fallback?: string | null } | null;
  citations?: ProposalCitation[] | null;
}

export interface ProposalBody {
  request?: string;
  summary?: string | null;
  items: ProposalItem[];
  categories?: { key: string; label?: string | null }[];
  regulations?: { topic?: string | null; rule?: string | null; what_we_do?: string | null; note?: string | null; applies?: boolean; citations?: (string | ProposalCitation)[] }[];
  errors?: string[];
  web_search?: boolean;
  search_count?: number | null;
  seconds?: number | null;
  model?: string | null;
}

export type ProposalStatus = "researching" | "proposed" | "approved" | "dispatched" | "rejected" | "failed";

export interface Proposal {
  id: string;
  request: string;
  status: ProposalStatus;
  proposal: ProposalBody;
  approved_item_ids: string[];
  run_id: string | null;
  created_at: string;
}

export interface Vendor {
  id?: string;
  owner_id?: string;
  key: string;
  name: string;
  track: Track;
  phone: string;
  is_demo: boolean;
}

export interface Availability {
  start: string;
  end: string;
}

export interface Address {
  street?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
}

export type Bedrooms = "studio" | "1" | "2" | "3+";
export const BEDROOMS: Bedrooms[] = ["studio", "1", "2", "3+"];

export type ProviderCategory = "internet" | "phone" | "gym" | "insurance" | "utilities" | "streaming" | "other";
export const PROVIDER_CATEGORIES: ProviderCategory[] = ["internet", "phone", "gym", "insurance", "utilities", "streaming", "other"];

/** Something the user already pays for. The research stage reads these before any call. */
export interface CurrentProvider {
  category: ProviderCategory;
  name: string;
  monthly_usd: number | null;
  contract_end?: string | null;
}

export interface Insurance {
  dental?: string | null;
  health?: string | null;
}

/**
 * Everything the agent and the research stage know about the user. Keys are spelled exactly as the
 * research engine reads them; unknown keys round-trip untouched.
 */
export interface Prefs {
  budget_ceiling_usd?: number | null;
  preferred_times?: string[];
  availability?: Availability[];
  /** The name the agent introduces itself with on calls. Empty means the server default (Alex). */
  caller_name?: string | null;
  current_address?: Address | null;
  new_address?: Address | null;
  /** YYYY-MM-DD */
  move_date?: string | null;
  bedrooms?: Bedrooms | null;
  household_size?: number | null;
  current_providers?: CurrentProvider[];
  insurance?: Insurance | null;
  /** Free text, e.g. "school.edu email on file". */
  student_proof?: string | null;
  [key: string]: unknown;
}

export const DEFAULT_CALLER_NAME = "Alex";

/**
 * Mirrors router/cohorts.py. Only `student` ships today; an unknown cohort falls back to student on the
 * Python side, so the UI offers exactly what the agent can honestly claim.
 */
export const COHORTS: { key: string; label: string; asks: string[]; proof: string }[] = [
  {
    key: "student",
    label: "Full-time student",
    asks: ["student rate", "waive activation fee", "no-contract term"],
    proof: "university email on file",
  },
];

export interface Profile {
  id: string;
  display_name: string | null;
  phone: string | null;
  prefs: Prefs;
  cohort: string;
  created_at?: string;
}

export type LedgerKind = "promo_expiry" | "renewal" | "appointment" | "quote";

export interface LedgerRow {
  id: string;
  user_id: string;
  vendor_id: string | null;
  kind: LedgerKind;
  label: string;
  amount_usd: number | null;
  effective_on: string | null;
  expires_on: string | null;
  source_call: string | null;
  created_at: string;
  // Optional join the backend may add so the UI can link the source call to its run.
  run_id?: string | null;
}

/** Body for POST /ledger (manual bookings from the calendar). Dates are YYYY-MM-DD. */
export interface NewLedgerRow {
  kind: LedgerKind;
  label: string;
  effective_on: string | null;
  expires_on: string | null;
  amount_usd: number | null;
}
