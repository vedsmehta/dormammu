// TS mirror of docs/CONTRACT.md row shapes. Column names == JSON keys, snake_case.

export type Track = "quote" | "schedule" | "negotiate";

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

export interface AssembledRun extends Run {
  calls: Call[];
  aggregate: AggregateView | null;
  server_now: string;
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

export interface Prefs {
  budget_ceiling_usd?: number | null;
  preferred_times?: string[];
  availability?: Availability[];
  [key: string]: unknown;
}

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
