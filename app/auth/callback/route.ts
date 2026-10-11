import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export const dynamic = "force-dynamic";

type CookieToSet = { name: string; value: string; options: Record<string, unknown> };

function siteOrigin(request: NextRequest): string {
  // Behind Vercel's proxy nextUrl.origin can be the internal host; prefer the forwarded one.
  const forwarded = request.headers.get("x-forwarded-host");
  if (process.env.NODE_ENV !== "development" && forwarded) {
    const proto = request.headers.get("x-forwarded-proto") ?? "https";
    return `${proto}://${forwarded}`;
  }
  return request.nextUrl.origin;
}

/** Magic-link landing: exchange the PKCE code, then route to onboarding when the roster is empty. */
export async function GET(request: NextRequest) {
  const origin = siteOrigin(request);
  const code = request.nextUrl.searchParams.get("code");
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!url || !key) return NextResponse.redirect(new URL("/", origin));
  if (!code) return NextResponse.redirect(new URL("/login?error=missing_code", origin));

  const pending: CookieToSet[] = [];
  const supabase = createServerClient(url, key, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        pending.push(...(cookiesToSet as CookieToSet[]));
      },
    },
  });

  const { data, error } = await supabase.auth.exchangeCodeForSession(code);
  if (error || !data.session) {
    const reason = encodeURIComponent(error?.message ?? "no_session");
    return NextResponse.redirect(new URL(`/login?error=${reason}`, origin));
  }

  let dest = "/";
  try {
    const base = process.env.PYTHON_API_URL || origin;
    const res = await fetch(`${base}/api/py/vendors`, {
      headers: { Authorization: `Bearer ${data.session.access_token}` },
      cache: "no-store",
    });
    if (res.ok) {
      const vendors = (await res.json()) as unknown;
      if (Array.isArray(vendors) && vendors.length === 0) dest = "/onboarding";
    }
  } catch {
    // The API being unreachable must not block sign-in; the landing page links to onboarding.
  }

  const response = NextResponse.redirect(new URL(dest, origin));
  pending.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
  return response;
}
