import { Panel, PanelTitle, Tag } from "@/components/ui";
import type { ProposalRegulation } from "@/lib/types";

import { hostOf } from "./support";

/**
 * The regulations the agent must respect on these calls (research/regulations.py), first on the page.
 * Each row: whether it applies, the rule, what the agent does about it, and the citation when the API
 * cited it or we fetched it. A row with no verified citation says so instead of linking.
 */
export function RegulationsCard({ regulations, state }: { regulations: ProposalRegulation[]; state: string | null | undefined }) {
  return (
    <Panel index={0} labelledBy="regs">
      <PanelTitle id="regs">Regulations{state ? ` · ${state}` : ""}</PanelTitle>
      {regulations.length === 0 ? (
        <p>The research returned no regulation findings for this request.</p>
      ) : (
        <ul className="mt-4">
          {regulations.map((r, i) => (
            <li key={r.key ?? i} className="border-t border-line py-4 first:border-t-0 first:pt-2">
              <div className="flex flex-wrap items-center gap-y-1">
                <Tag tone={r.applies ? "mint" : "line"} first>
                  {r.applies ? "applies" : "does not apply"}
                </Tag>
                {r.jurisdiction ? <Tag tone="line">{r.jurisdiction}</Tag> : null}
              </div>
              <p className="mt-2 font-display text-[17px] font-semibold leading-snug tracking-tight">{r.rule}</p>
              <p className="mt-2 text-[15px] text-ink-2">
                <span className="text-deep">What we do.</span> {r.what_we_do}
              </p>
              {r.citation ? (
                <a className="more mt-2" href={r.citation.url} target="_blank" rel="noreferrer noopener">
                  {r.citation.title || hostOf(r.citation.url)} ↗
                  {r.citation.verified ? <Tag tone="line">{r.citation.verified}</Tag> : null}
                </a>
              ) : null}
              {r.note || !r.citation ? (
                <small className="mt-1 block text-[13px] text-ink-3">{r.note ?? "No verified citation."}</small>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
