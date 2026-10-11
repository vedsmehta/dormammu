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
