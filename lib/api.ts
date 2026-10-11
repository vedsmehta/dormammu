import { getSupabaseBrowser } from "@/lib/supabase/client";
import type { AssembledRun, LedgerRow, Profile, RunListItem, Vendor } from "@/lib/types";

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
