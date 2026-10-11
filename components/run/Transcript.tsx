import type { Call } from "@/lib/types";

export function Transcript({ call }: { call: Call }) {
  const turns = Array.isArray(call.transcript_turns) ? call.transcript_turns : null;
  const text = call.transcript;
  if ((!turns || turns.length === 0) && !text) return null;

  return (
    <details className="mt-2">
      <summary className="cursor-pointer select-none font-mono text-xs text-dim hover:text-fg">
        transcript{turns && turns.length ? ` · ${turns.length} turns` : ""}
      </summary>
      <div className="mt-2 max-h-72 space-y-1.5 overflow-y-auto border-l border-line pl-3 text-xs leading-relaxed">
        {turns && turns.length ? (
          turns.map((t, i) => (
            <p key={i} className="text-muted">
              <span className="mr-1 font-mono text-dim">{t.speaker}:</span>
              <span className="text-fg/90">{t.text}</span>
            </p>
          ))
        ) : (
          <pre className="whitespace-pre-wrap font-sans text-muted">{text}</pre>
        )}
      </div>
    </details>
  );
}
