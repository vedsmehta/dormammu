import Link from "next/link";
import type { CSSProperties } from "react";

import { callHref } from "@/components/calls/summary";
import { OutcomeCell } from "@/components/run/OutcomeCell";
import { StateWord } from "@/components/run/StateMeter";
import { Transcript } from "@/components/run/Transcript";
import { Dot, Tag } from "@/components/ui";
import { fmtDuration, maskPhone, parseTs } from "@/lib/format";
import { isTerminal, type Call } from "@/lib/types";

/** Elapsed seconds for a row: started_at to ended_at, else to the server-clock estimate. */
function elapsedFor(call: Call, serverNow: number | null): number | null {
  if (call.state === "planned") return 0;
  const start = parseTs(call.started_at);
  if (start === null) return call.duration_seconds ?? null;
  const end = call.ended_at ? parseTs(call.ended_at) : null;
  if (end !== null) return Math.max(0, (end - start) / 1000);
  if (isTerminal(call.state)) return call.duration_seconds ?? null;
  if (serverNow === null) return null;
  return Math.max(0, (serverNow - start) / 1000);
}

/** One .row of the live board (run.html): dot, vendor, state, clock, result. */
export function CallRow({ call, serverNow, best, index }: { call: Call; serverNow: number | null; best: boolean; index: number }) {
  const elapsed = elapsedFor(call, serverNow);
  const failed = call.state === "failed";

  return (
    <li id={`call-${call.id}`} className={failed ? "row failed" : "row"} style={{ "--i": index } as CSSProperties}>
      <Dot state={call.state} />
      <span className="who">
        <Link href={callHref(call)} title="Open this call">
          {call.target_name}
        </Link>
        {call.simulated ? (
          <Tag tone="warn" title="Simulated: this row ran on the scripted timeline, not a live Dial call">
            Simulated
          </Tag>
        ) : null}
        <small>
          {maskPhone(call.target_phone)} · {call.track}
        </small>
      </span>
      <span className="state">
        <StateWord call={call} />
      </span>
      <span className={call.state === "talking" ? "time num t-talking" : "time num"}>{fmtDuration(elapsed)}</span>
      <span className="got">
        <OutcomeCell call={call} best={best} elapsed={elapsed} />
      </span>
      {isTerminal(call.state) ? <Transcript call={call} /> : null}
    </li>
  );
}
