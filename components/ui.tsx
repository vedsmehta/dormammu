import Link from "next/link";
import type { ReactNode } from "react";

import type { CallState, RunStatus } from "@/lib/types";

/*
  Small pieces of the prototype's system (frontend/styles.css, frontend/app.css), each one a thin
  wrapper over his class names so screens read like his HTML.
*/

/** The filled pill label: mint by default, amber for a warning, outlined for a neutral note. */
export function Tag({
  children,
  tone = "mint",
  first,
  title,
}: {
  children: ReactNode;
  tone?: "mint" | "warn" | "line";
  /** Drop the leading 8px margin when the tag starts a line. */
  first?: boolean;
  title?: string;
}) {
  const cls = ["tag", tone === "mint" ? "" : tone, first ? "first" : ""].filter(Boolean).join(" ");
  return (
    <i className={cls} title={title}>
      {children}
    </i>
  );
}

export function StatusChip({ status, first }: { status: RunStatus | string; first?: boolean }) {
  const tone = status === "complete" ? "mint" : status === "failed" ? "warn" : "line";
  return (
    <Tag tone={tone} first={first}>
      {status}
    </Tag>
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
  block,
}: {
  children: ReactNode;
  type?: "button" | "submit";
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "line" | "glass";
  className?: string;
  size?: "sm" | "md";
  block?: boolean;
}) {
  const cls = ["btn", `btn-${variant}`, size === "sm" ? "btn-sm" : "", block ? "btn-block" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={cls}>
      {children}
    </button>
  );
}

/** His underline input, for inputs that sit outside a .field (roster rows, windows). */
export const inputClass = "input";

/** A labelled field: label above, underline input, optional hint below. */
export function Field({
  label,
  children,
  hint,
  tight,
}: {
  label: string;
  children: ReactNode;
  hint?: ReactNode;
  tight?: boolean;
}) {
  return (
    <label className={tight ? "field tight" : "field"}>
      <span className="lbl">{label}</span>
      {children}
      {hint ? <span className="hint block">{hint}</span> : null}
    </label>
  );
}

export function Panel({
  children,
  className = "",
  index,
  id,
  labelledBy,
}: {
  children: ReactNode;
  className?: string;
  /** Stagger slot for the rise-in, like the prototype's style="--i:n". */
  index?: number;
  id?: string;
  labelledBy?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={`panel ${className}`.trim()}
      style={index !== undefined ? ({ "--i": index } as React.CSSProperties) : undefined}
    >
      {children}
    </section>
  );
}

/** A panel heading: small teal label on the left, an optional underlined link on the right. */
export function PanelTitle({ children, href, linkText, id }: { children: ReactNode; href?: string; linkText?: string; id?: string }) {
  return (
    <h2 id={id}>
      <span>{children}</span>
      {href ? <Link href={href}>{linkText ?? "Open"}</Link> : null}
    </h2>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return <p className="note bad">{message}</p>;
}

export function Brand({ href = "/" }: { href?: string }) {
  return (
    <Link href={href} className="brand">
      Dormammu
    </Link>
  );
}

const DOT_CLASS: Record<CallState, string> = {
  planned: "planned",
  dialing: "dialing",
  ringing: "ringing",
  talking: "talking",
  extracting: "extracting",
  done: "",
  failed: "warn",
};

/** The status dot. Live states take his state colors and the expanding ring; done is a plain dot. */
export function Dot({ state, className = "" }: { state: CallState | string; className?: string }) {
  const tone = DOT_CLASS[state as CallState] ?? "planned";
  return <span className={`dot ${tone} ${className}`.trim()} aria-hidden="true" />;
}

/** Four bars that bob while a call is being talked on. */
export function Wave() {
  return (
    <span className="wave" aria-hidden="true">
      <i />
      <i />
      <i />
      <i />
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

/** Text color class for a state word, in his state palette. */
export function stateTone(state: string | null | undefined): string {
  switch (state) {
    case "talking":
      return "t-talking";
    case "ringing":
    case "dialing":
    case "failed":
      return "t-warn";
    case "extracting":
      return "t-texting";
    case "planned":
      return "t-muted";
    default:
      return "";
  }
}

export function Loading({ children = "Loading…" }: { children?: ReactNode }) {
  return <p className="dash-note">{children}</p>;
}
