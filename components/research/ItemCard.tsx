"use client";

import type { CSSProperties } from "react";

import { Tag } from "@/components/ui";
import { fmtUsd, maskPhone } from "@/lib/format";
import { trackLabel, type ProposalItem } from "@/lib/types";

import { callable, hostOf, pct, vendorOf } from "./support";

/**
 * One proposed vendor. Everything shown is read off the item; the phone is masked client-side and the
 * full digits never reach the DOM. In `select` mode the checkbox picks the item for approval and is
 * disabled, with the engine's skip_reason, when there is no verified phone. In `locked` mode (the
 * proposal is dispatched, rejected or failed) the checkbox only reports what was approved.
 */
export function ItemCard({
  item,
  index,
  mode,
  checked,
  approved,
  onToggle,
  caller,
  client,
}: {
  item: ProposalItem;
  index: number;
  mode: "select" | "locked";
  checked: boolean;
  /** Dispatched proposals: this item's id is in approved_item_ids. */
  approved: boolean;
  onToggle: (id: string, next: boolean) => void;
  /** The name the agent introduces itself with (profile prefs.caller_name, else Alex). */
  caller: string;
  /** Who the agent says it is calling for (profile display_name, else "a client"). */
  client: string;
}) {
  const vendor = vendorOf(item);
  const canCall = callable(item);
  const plans = Array.isArray(item.plans) ? item.plans : [];
  const discounts = Array.isArray(item.discounts) ? item.discounts : [];
  const citations = Array.isArray(item.citations) ? item.citations : [];
  const strategy = item.strategy ?? null;
  const asks = Array.isArray(strategy?.asks) ? strategy.asks : [];
  const confidence = pct(item.confidence);
  const nameId = `${item.id}-name`;
  const skipId = `${item.id}-skip`;

  const cite = (n: number | null | undefined) => {
    if (n === null || n === undefined || !citations[n]) return null;
    return (
      <sup className="ml-0.5 text-[11px]">
        <a href={citations[n].url} target="_blank" rel="noreferrer noopener" className="text-deep" title={citations[n].title}>
          [{n + 1}]
        </a>
      </sup>
    );
  };

  return (
    <article className="panel" style={{ "--i": Math.min(index, 8) } as CSSProperties} aria-labelledby={nameId}>
      <header className="flex items-start gap-4">
        <input
          type="checkbox"
          className="mt-1.5 h-5 w-5 shrink-0 accent-deep"
          checked={mode === "locked" ? approved : checked}
          disabled={mode === "locked" || !canCall}
          onChange={(e) => onToggle(item.id, e.target.checked)}
          aria-label={`Call ${vendor.name}`}
          aria-describedby={!canCall && item.skip_reason ? skipId : undefined}
        />
        <div className="min-w-0 flex-1">
          <h3 id={nameId} className="font-display text-[22px] font-semibold leading-tight tracking-tight">
            {vendor.name}
          </h3>
          <div className="mt-1.5 flex flex-wrap items-center gap-y-1">
            <Tag tone={item.source === "roster" ? "mint" : "line"} first>
              {item.source === "roster" ? "on your roster" : item.source === "profile" ? "from your profile" : "found on the web"}
            </Tag>
            <Tag tone="line">{trackLabel(item.track)}</Tag>
            {item.recommended ? <Tag>recommended</Tag> : null}
            {item.phone_verified ? <Tag tone="line">phone verified</Tag> : <Tag tone="warn">no verified phone</Tag>}
            {approved ? <Tag>approved</Tag> : null}
          </div>
          <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[15px] text-ink-2">
            {vendor.phone ? <span className="num">{maskPhone(vendor.phone)}</span> : <span className="t-muted">no phone number</span>}
            {vendor.website ? (
              <a href={vendor.website} target="_blank" rel="noreferrer noopener" className="underline underline-offset-4 hover:text-deep">
                {hostOf(vendor.website)}
              </a>
            ) : null}
            {vendor.address ? <span>{vendor.address}</span> : null}
          </p>
        </div>
        <div className="w-[96px] shrink-0 text-right" title={`Confidence ${confidence}%`}>
          <small className="block text-[12px] text-ink-3">confidence</small>
          <span className="talkbar mt-1" role="img" aria-label={`Confidence ${confidence} percent`}>
            <i style={{ width: `${confidence}%` }} />
          </span>
          <small className="num mt-1 block text-[12px] text-ink-2">{confidence}%</small>
        </div>
      </header>

      {!canCall ? (
        <p id={skipId} className="note warn">
          Cannot be called: {item.skip_reason ?? "no verified phone number"}.
        </p>
      ) : null}

      {item.why ? <p className="mt-4 text-[16px]">{item.why}</p> : null}

      {plans.length > 0 ? (
        <table className="mt-5 w-full border-collapse text-[15px]">
          <caption className="pb-2 text-left text-[12px] uppercase tracking-[0.08em] text-ink-3">Plans</caption>
          <thead>
            <tr className="text-left text-[12px] text-ink-3">
              <th scope="col" className="pb-1 pr-3 font-normal">
                Plan
              </th>
              <th scope="col" className="pb-1 pr-3 text-right font-normal">
                Price
              </th>
              <th scope="col" className="pb-1 pr-3 font-normal">
                Unit
              </th>
              <th scope="col" className="pb-1 font-normal">
                Notes
              </th>
            </tr>
          </thead>
          <tbody>
            {plans.map((p, i) => (
              <tr key={`${p.name}-${i}`} className="border-t border-line align-top">
                <td className="py-2 pr-3 font-medium">
                  {p.name}
                  {cite(p.citation)}
                </td>
                <td className="num py-2 pr-3 text-right whitespace-nowrap">
                  {p.price_usd !== null && p.price_usd !== undefined ? fmtUsd(p.price_usd) : <span className="t-muted">not listed</span>}
                  {p.price_in_source === false && p.price_usd !== null ? (
                    <small className="block font-body text-[12px] text-c-ring">not seen on the cited page</small>
                  ) : null}
                </td>
                <td className="py-2 pr-3 text-ink-2">{p.unit ?? ""}</td>
                <td className="py-2 text-ink-2">{p.notes ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {discounts.length > 0 ? (
        <div className="mt-5">
          <h4 className="text-[12px] uppercase tracking-[0.08em] text-ink-3">Discounts</h4>
          <ul className="mt-1">
            {discounts.map((dc, i) => (
              <li key={`${dc.label}-${i}`} className="border-t border-line py-2 text-[15px] first:border-t-0">
                <b className="font-display font-semibold">{dc.label}</b>
                {cite(dc.citation)}
                <span className="text-ink-2">
                  {dc.who_qualifies ? ` · who qualifies: ${dc.who_qualifies}` : ""}
                  {dc.how_to_claim ? ` · how to claim: ${dc.how_to_claim}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {strategy ? (
        <div className="mt-5 rounded-[18px] bg-paper p-4 sm:p-5">
          <h4 className="flex flex-wrap items-center gap-y-1 text-[12px] uppercase tracking-[0.08em] text-ink-3">
            Call plan
            {strategy.variant ? <Tag tone="line">{strategy.variant}</Tag> : null}
            {strategy.label ? <Tag>{strategy.label}</Tag> : null}
          </h4>
          {strategy.reason ? <p className="mt-1 text-[14px] text-ink-2">{strategy.reason}</p> : null}
          <ol className="mt-3 grid gap-2.5 text-[15px]">
            <li>
              <small className="block text-[12px] text-ink-3">1 · Opener, fixed</small>
              <q>
                Hi, this is {caller}, am I speaking with {vendor.name}?
              </q>
            </li>
            <li>
              <small className="block text-[12px] text-ink-3">2 · AI line, fixed</small>
              <q>I&rsquo;m an AI assistant calling for {client}.</q>
            </li>
            {strategy.opening ? (
              <li>
                <small className="block text-[12px] text-ink-3">3 · Then the ask</small>
                <q className="font-medium">{strategy.opening}</q>
              </li>
            ) : null}
          </ol>
          {asks.length > 0 ? (
            <div className="mt-3">
              <small className="block text-[12px] text-ink-3">Asks for</small>
              <ul className="mt-1.5 flex flex-wrap gap-2">
                {asks.map((a) => (
                  <li key={a} className="rounded-full px-3 py-1.5 text-[14px] shadow-[inset_0_0_0_1.5px_#a9ddd3]">
                    {a}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {strategy.walk_away ? (
            <p className="mt-3 text-[14px] text-ink-2">
              <span className="text-c-ring">Walks away:</span> {strategy.walk_away}
            </p>
          ) : null}
        </div>
      ) : null}

      {citations.length > 0 ? (
        <ol className="mt-5 list-decimal pl-5 text-[14px] text-ink-2">
          {citations.map((c, i) => (
            <li key={`${c.url}-${i}`} className="py-0.5">
              <a href={c.url} target="_blank" rel="noreferrer noopener" className="underline decoration-ink-3 underline-offset-4 hover:text-deep">
                {c.title || hostOf(c.url)}
              </a>
              {c.verified ? <Tag tone="line">{c.verified}</Tag> : null}
            </li>
          ))}
        </ol>
      ) : null}
    </article>
  );
}
