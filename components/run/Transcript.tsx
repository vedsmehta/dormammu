import type { Call } from "@/lib/types";

/** The transcript folded under a finished row, in the call page's list style. */
export function Transcript({ call }: { call: Call }) {
  const turns = Array.isArray(call.transcript_turns) ? call.transcript_turns : null;
  const text = call.transcript;
  if ((!turns || turns.length === 0) && !text) return null;

  return (
    <details className="said">
      <summary className="tog">Transcript{turns && turns.length ? ` · ${turns.length} turns` : ""}</summary>
      <ol className="transcript no-time inline">
        {turns && turns.length ? (
          turns.map((t, i) => {
            const agent = /agent|assistant|ai/i.test(t.speaker);
            return (
              <li key={i} className={agent ? "agent" : "rep"}>
                <span className="sp">{t.speaker}</span>
                <p>{t.text}</p>
              </li>
            );
          })
        ) : (
          <li>
            <pre>{text}</pre>
          </li>
        )}
      </ol>
    </details>
  );
}
