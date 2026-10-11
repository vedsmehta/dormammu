import { OutcomeCell } from "@/components/run/OutcomeCell";
import { StateMeter, StateWord } from "@/components/run/StateMeter";
import { Transcript } from "@/components/run/Transcript";
import { Chip } from "@/components/ui";
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

export function CallRow({ call, serverNow, best }: { call: Call; serverNow: number | null; best: boolean }) {
  const elapsed = elapsedFor(call, serverNow);
  const failed = call.state === "failed";
  const talking = call.state === "talking";
  const ringing = call.state === "ringing";

  const rowTone = failed
    ? "border-l-4 border-l-bad/40 text-muted"
    : talking
      ? "border-l-4 border-l-ok bg-ok/10 animate-row-in"
      : ringing
        ? "border-l-4 border-l-warn/70"
        : "border-l-4 border-l-transparent";

  return (
    <li id={`call-${call.id}`} className={`px-4 py-3 transition-colors duration-500 ${rowTone}`}>
      <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 md:grid-cols-[minmax(0,11rem)_auto_8.5rem_3.5rem_minmax(0,1fr)_auto]">
        <div className="min-w-0">
          <div className={`truncate text-sm font-medium ${failed ? "text-bad/80" : "text-fg"}`}>{call.target_name}</div>
          <div className="tnum truncate font-mono text-[11px] text-dim">
            {maskPhone(call.target_phone)}
            <span className="mx-1">·</span>
            {call.track}
          </div>
        </div>

        <div className="justify-self-end md:justify-self-start">
          <StateMeter state={call.state} />
        </div>

        <div className="text-sm">
          <StateWord call={call} />
        </div>

        <div className={`tnum font-mono text-sm ${failed ? "text-bad/70" : talking ? "text-ok" : "text-fg"}`}>
          {fmtDuration(elapsed)}
        </div>

        <div className="col-span-2 min-w-0 md:col-span-1">
          <OutcomeCell call={call} best={best} elapsed={elapsed} />
        </div>

        <div className="col-span-2 flex items-center justify-end gap-2 md:col-span-1">
          {call.simulated ? (
            <Chip tone="warn" title="Simulated: this row ran on the scripted timeline, not a live Dial call">
              simulated
            </Chip>
          ) : null}
        </div>
      </div>
      {isTerminal(call.state) ? <Transcript call={call} /> : null}
    </li>
  );
}
