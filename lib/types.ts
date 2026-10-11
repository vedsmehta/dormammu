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

// ---------------------------------------------------------------------------------------------
// Proposals: the research-first flow (docs/CONTRACT.md "Proposals", research/schemas.py Proposal).
// Everything on the research screens comes from these rows; the UI invents nothing.
// ---------------------------------------------------------------------------------------------

export type ProposalStatus = "researching" | "proposed" | "approved" | "dispatched" | "rejected" | "failed";

export const PROPOSAL_STATUS_WORD: Record<ProposalStatus, string> = {
  researching: "Researching",
  proposed: "Awaiting your yes",
  approved: "Approved",
  dispatched: "Calls placed",
  rejected: "Rejected",
  failed: "Research failed",
};

export function proposalStatusWord(status: string | null | undefined): string {
  return PROPOSAL_STATUS_WORD[status as ProposalStatus] ?? String(status ?? "");
}

export interface ProposalCitation {
  title: string;
  url: string;
  /** api: the Responses API cited it; fetched: we opened it; roster: the user's own row. */
  verified: "api" | "fetched" | "roster" | null;
}

export interface ProposalVendor {
  key: string;
  name: string;
  /** Full E.164 when the engine verified one, else null. Mask before showing (lib/format maskPhone). */
  phone: string | null;
  website: string | null;
  address: string | null;
  is_demo: boolean;
  roster_vendor_id: string | null;
}

export interface ProposalPlan {
  name: string;
  price_usd: number | null;
  unit: string | null;
  notes: string | null;
  /** Index into the item's citations, or null. */
  citation: number | null;
  /** Whether the dollar amount was seen on the cited page. */
  price_in_source: boolean | null;
}

export interface ProposalDiscount {
  label: string;
  who_qualifies: string;
  how_to_claim: string;
  citation: number | null;
}

export interface ProposalStrategy {
  /** negotiate: new_service | S1_anchor | S2_loyalty | S3_cancel | S4_audit; quote and schedule: the track. */
  variant: string;
  label: string | null;
  reason: string | null;
  asks: string[];
  /** The one sentence after the fixed opener and the AI line. */
  opening: string;
  walk_away: string;
  objective: string;
}

export interface ProposalItem {
  id: string;
  /** Category key: movers | internet | dentist | lower_<slug> | cancel_<slug> | ... */
  category: string;
  track: Track;
  source: "roster" | "web" | "profile";
  vendor: ProposalVendor;
  why: string;
  plans: ProposalPlan[];
  discounts: ProposalDiscount[];
  strategy: ProposalStrategy;
  /** 0..1, scaled by 0.6 when the phone is unverified. */
  confidence: number;
  citations: ProposalCitation[];
  /** phone_verified and confidence >= 0.5 */
  recommended: boolean;
  phone_verified: boolean;
  phone_source?: "roster" | "model_and_page" | "page" | null;
  phone_note?: string | null;
  /** Why the item cannot be dialed, when vendor.phone is null. */
  skip_reason: string | null;
}

export interface ProposalRegulation {
  key?: string | null;
  rule: string;
  jurisdiction: string;
  applies: boolean;
  what_we_do: string;
  /** Kept only when the API cited it or we fetched it; null otherwise. */
  citation: ProposalCitation | null;
  note?: string | null;
}

export interface ProposalCategory {
  key: string;
  label: string;
  track: Track;
  why: string;
  source: "request" | "profile" | "roster";
  query: string | null;
  provider?: CurrentProvider | null;
}

/** router.plan.parse_request_details(request) merged with the profile prefs. Every key is optional. */
export interface ProposalDetails {
  date?: string | null;
  bedrooms?: string | null;
  origin?: string | null;
  destination?: string | null;
  budget?: string | null;
  state?: string | null;
  zip?: string | null;
  is_move?: boolean;
  move_date?: string | null;
  budget_ceiling_usd?: number | null;
  household_size?: number | null;
  new_address?: Address | null;
  insurance?: Insurance | null;
  student_proof?: string | null;
  [key: string]: unknown;
}

/** The proposals.proposal json: what research.engine.research returned, normalized by the server. */
export interface ProposalBody {
  request: string;
  details: ProposalDetails;
  categories: ProposalCategory[];
  items: ProposalItem[];
  regulations: ProposalRegulation[];
  summary: string;
  generated_at: string;
  model: string;
  /** False when the account rejected the web_search tool: no citations, model knowledge only. */
  web_search: boolean;
  search_count: number;
  errors: string[];
  seconds: number | null;
  meta?: Record<string, unknown>;
}

/** A proposals row (GET /proposals/{id}). */
export interface Proposal {
  id: string;
  user_id: string;
  request: string;
  status: ProposalStatus;
  proposal: ProposalBody;
  /** The item ids that became calls rows on approval. */
  approved_item_ids: string[] | null;
  run_id: string | null;
  created_at: string;
  updated_at: string;
}

/** A row from GET /proposals: the summary columns plus n_items, never the body. */
export interface ProposalListItem {
  id: string;
  user_id: string;
  request: string;
  status: ProposalStatus;
  approved_item_ids: string[] | null;
  run_id: string | null;
  created_at: string;
  updated_at: string;
  n_items: number;
}

export interface ProposalSkipped {
  item_id: string;
  /** "no verified phone" | "not in this proposal" | "same vendor as <item_id>" | "over the MAX_CALLS_PER_RUN cap of N" */
  reason: string;
}

/** POST /proposals/{id}/approve. When external_calls_enabled is false, outside numbers run as labeled simulated rows. */
export interface ProposalApproveResult {
  run: AssembledRun;
  skipped: ProposalSkipped[];
  external_calls_enabled: boolean;
  proposal: Proposal;
}
