import Link from "next/link";
import type { ReactNode } from "react";

import type { RunStatus } from "@/lib/types";

export function Chip({
  children,
  tone = "muted",
  className = "",
  title,
}: {
  children: ReactNode;
  tone?: "muted" | "ok" | "warn" | "bad" | "accent" | "violet";
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

export function Button({
  children,
  type = "button",
  onClick,
  disabled,
  variant = "primary",
  className = "",
}: {
  children: ReactNode;
  type?: "button" | "submit";
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "ghost";
  className?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50";
  const styles =
    variant === "primary"
      ? "bg-fg text-bg hover:bg-white"
      : "border border-line bg-panel text-fg hover:border-muted";
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${styles} ${className}`}>
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
  return <section className={`rounded-lg border border-line bg-panel ${className}`}>{children}</section>;
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
    <Link href={href} className="font-mono text-sm tracking-widest text-muted hover:text-fg">
      DORMAMMU
    </Link>
  );
}
