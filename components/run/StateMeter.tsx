import { stateTone, Wave } from "@/components/ui";
import type { Call } from "@/lib/types";

/** The state word in the prototype's row: Talking takes the wave, ringing and failed take amber. */
export function StateWord({ call }: { call: Call }) {
  const tone = stateTone(call.state);
  switch (call.state) {
    case "planned":
      return <span className={tone}>Planned</span>;
    case "dialing":
      return <span className={tone}>Dialing…</span>;
    case "ringing":
      return <span className={tone}>Ringing</span>;
    case "talking":
      return (
        <>
          <span className={tone}>Talking</span>
          <Wave />
        </>
      );
    case "extracting":
      return <span className={tone}>Reading the transcript…</span>;
    case "done":
      return <span>Done</span>;
    case "failed":
      return <span className={tone}>Failed</span>;
    default:
      return <span className="t-muted">{String(call.state)}</span>;
  }
}
