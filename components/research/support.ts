import { ApiError } from "@/lib/api";
import { describeError } from "@/lib/format";
import type { ProposalItem, ProposalStatus, ProposalVendor } from "@/lib/types";

/*
  Small pure helpers for the research screens. Nothing here reads the clock or the network.
*/

/** The API's `detail` string from an ApiError body (FastAPI's HTTPException shape), else the generic text. */
export function apiDetail(e: unknown): string {
  if (e instanceof ApiError && e.body) {
    try {
      const parsed = JSON.parse(e.body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail) return parsed.detail;
      if (Array.isArray(parsed.detail)) {
        const msgs = parsed.detail.map((d) =>
          d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d),
        );
        if (msgs.length) return msgs.join("; ");
      }
    } catch {
      // Not JSON: fall through to the generic description.
    }
  }
  return describeError(e);
}

function str(x: unknown): string | null {
  return typeof x === "string" && x.trim() ? x.trim() : null;
}

/**
 * The item's vendor block, read the way server.app.item_vendor reads it: the nested `vendor` first,
 * then flat keys on the item. The engine always nests; this only keeps a hand-written row from crashing.
 */
export function vendorOf(item: ProposalItem): ProposalVendor {
  const v = (item.vendor ?? {}) as Partial<ProposalVendor>;
  const flat = item as unknown as Record<string, unknown>;
  return {
    key: v.key ?? "",
    name: str(v.name) ?? str(flat.vendor_name) ?? str(flat.name) ?? "vendor",
    phone: str(v.phone) ?? str(flat.phone),
    website: str(v.website) ?? str(flat.website) ?? str(flat.url),
    address: str(v.address),
    is_demo: Boolean(v.is_demo),
    roster_vendor_id: str(v.roster_vendor_id),
  };
}

/** Only an item with a verified phone can become a call. The server skips the rest ("no verified phone"). */
export function callable(item: ProposalItem): boolean {
  return vendorOf(item).phone !== null;
}

/** Checked by default when the engine recommends it and the phone is verified. */
export function defaultChecked(item: ProposalItem): boolean {
  return Boolean(item.recommended) && Boolean(item.phone_verified) && callable(item);
}

/** A proposal's status in the Dot vocabulary (components/ui). */
export function statusDot(status: ProposalStatus | string): string {
  switch (status) {
    case "researching":
      return "dialing";
    case "proposed":
      return "ringing";
    case "approved":
      return "extracting";
    case "dispatched":
      return "done";
    case "failed":
      return "failed";
    default:
      return "planned";
  }
}

/** Tag tone for a proposal status. */
export function statusTone(status: ProposalStatus | string): "mint" | "warn" | "line" {
  switch (status) {
    case "dispatched":
    case "approved":
      return "mint";
    case "failed":
      return "warn";
    default:
      return "line";
  }
}

/** Text color class for a proposal status word, in the state palette. */
export function statusTextTone(status: ProposalStatus | string): string {
  switch (status) {
    case "proposed":
      return "t-ringing";
    case "dispatched":
    case "approved":
      return "t-talking";
    case "researching":
      return "t-texting";
    case "failed":
      return "t-warn";
    default:
      return "t-muted";
  }
}

/** 'https://www.example.com/path' -> 'example.com'. Unparseable input is returned as is. */
export function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** 0..1 -> whole percent, clamped. */
export function pct(confidence: number | null | undefined): number {
  if (confidence === null || confidence === undefined || !Number.isFinite(confidence)) return 0;
  return Math.max(0, Math.min(100, Math.round(confidence * 100)));
}
