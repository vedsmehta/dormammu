import Link from "next/link";
import type { ReactNode } from "react";

import type { CallState, RunStatus } from "@/lib/types";

export function Chip({
  children,
  tone = "muted",
  className = "",
  title,
}: {
  children: ReactNode;
  tone?: "muted" | "ok" | "warn" | "bad" | "accent" | "violet" | "mint";
  className?: string;
  title?: string;
}) {
  const tones: Record<string, string> = {
    muted: "border-line text-muted",
    ok: "border-ok/40 text-ok",
    warn: "border-warn/40 text-warn",
    bad: "border-bad/40 text-bad",
    accent: "border-accent/40 text-accent",
    violet: "border-violet/40 text-violet",
    mint: "border-mint/40 text-mint",
  };
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-mono uppercase tracking-wide ${tones[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

export function StatusChip({ status }: { status: RunStatus | string }) {
  const tone =
    status === "complete" ? "ok" : status === "failed" ? "bad" : status === "running" ? "accent" : "warn";
  return <Chip tone={tone}>{status}</Chip>;
}

/** The prototype's pill tag ("Best", "Live"): a filled rounded label. */
export function Tag({ children, tone = "mint" }: { children: ReactNode; tone?: "mint" | "warn" | "muted" }) {
  const tones = {
    mint: "bg-mint text-bg",
    warn: "bg-warn text-bg",
    muted: "bg-line text-muted",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function Button({
  children,
  type = "button",
  onClick,
  disabled,
  variant = "primary",
  className = "",
  size = "md",
}: {
  children: ReactNode;
  type?: "button" | "submit";
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "ghost" | "mint";
  className?: string;
  size?: "sm" | "md";
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-full font-medium transition disabled:cursor-not-allowed disabled:opacity-50";
  const sizing = size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm";
  const styles =
    variant === "primary"
      ? "bg-fg text-bg hover:bg-white"
      : variant === "mint"
        ? "bg-mint text-bg hover:bg-white"
        : "border border-line bg-panel text-fg hover:border-muted";
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${sizing} ${styles} ${className}`}>
      {children}
    </button>
  );
}

export const inputClass =
  "w-full rounded-md border border-line bg-panel px-3 py-2 text-sm text-fg placeholder:text-dim focus:border-accent focus:outline-none";

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-mono uppercase tracking-wide text-muted">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-dim">{hint}</span> : null}
    </label>
  );
}

export function Panel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-panel ${className}`}>{children}</section>;
}

/** A panel heading in the prototype's style: small label on the left, an optional link on the right. */
export function PanelTitle({ children, href, linkText }: { children: ReactNode; href?: string; linkText?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h2 className="font-mono text-xs uppercase tracking-widest text-teal">{children}</h2>
      {href ? (
        <Link href={href} className="text-xs text-muted underline underline-offset-4 hover:text-fg">
          {linkText ?? "open"}
        </Link>
      ) : null}
    </div>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="rounded-md border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad break-words">
      {message}
    </p>
  );
}

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="font-display text-sm font-bold uppercase tracking-widest text-fg hover:text-mint">
      Dormammu
    </Link>
  );
}

/**
 * The page head from the prototype's app pages: an optional back link, a large title, a lede. The
 * teal sky behind it was not ported; the dark background stays.
 */
export function PageHeader({
  title,
  lede,
  back,
  children,
}: {
  title: ReactNode;
  lede?: ReactNode;
  back?: { href: string; label: string };
  children?: ReactNode;
}) {
  return (
    <header className="animate-rise pb-8 pt-4 sm:pb-10 sm:pt-8">
      {back ? (
        <Link href={back.href} className="mb-4 inline-block text-sm text-muted hover:text-fg">
          ← {back.label}
        </Link>
      ) : null}
      <h1 className="font-display text-3xl font-semibold leading-tight tracking-tight sm:text-5xl">{title}</h1>
      {lede ? <p className="mt-3 max-w-2xl text-base text-muted">{lede}</p> : null}
      {children}
    </header>
  );
}

const DOT_TONE: Record<CallState, string> = {
  planned: "bg-dim",
  dialing: "bg-accent",
  ringing: "bg-warn",
  talking: "bg-ok",
  extracting: "bg-violet",
  done: "bg-ok",
  failed: "bg-bad",
};

/** The prototype's status dot. Live states get a soft expanding ring. */
export function Dot({ state, className = "" }: { state: CallState | string; className?: string }) {
  const tone = DOT_TONE[state as CallState] ?? "bg-dim";
  const live = state === "dialing" || state === "ringing" || state === "talking" || state === "extracting";
  return (
    <span className={`relative inline-block h-2.5 w-2.5 shrink-0 rounded-full ${tone} ${className}`} aria-hidden="true">
      {live ? <span className={`absolute inset-0 animate-ping-soft rounded-full ${tone} opacity-70`} /> : null}
    </span>
  );
}

/** Four bars that bob while a call is being talked on. */
export function Wave() {
  return (
    <span className="ml-2 inline-flex h-3.5 items-center gap-0.5 align-middle" aria-hidden="true">
      {[0, -0.6, -0.3, -0.75].map((delay, i) => (
        <i key={i} className="block h-full w-0.5 animate-wave rounded-sm bg-ok" style={{ animationDelay: `${delay}s` }} />
      ))}
    </span>
  );
}

export const STATE_WORD: Record<CallState, string> = {
  planned: "Planned",
  dialing: "Dialing",
  ringing: "Ringing",
  talking: "Talking",
  extracting: "Reading the transcript",
  done: "Done",
  failed: "Failed",
};

export function stateWord(state: string | null | undefined): string {
  return STATE_WORD[state as CallState] ?? String(state ?? "");
}

export function Loading({ children = "loading…" }: { children?: ReactNode }) {
  return <p className="font-mono text-xs text-dim">{children}</p>;
}
