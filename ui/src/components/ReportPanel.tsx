import { useState } from "react";
import { api } from "../api";
import { useStore } from "../store";

/** Run report: plan vs actual, scope drift, decisions, research, critiques, verdicts, monitor actions, cost by role. */
export function ReportPanel() {
  const token = useStore((s) => s.token);
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const toggle = async () => {
    if (open) return setOpen(false);
    try {
      setText((await api.report(token)).markdown);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "report failed");
    }
    setOpen(true);
  };

  return (
    <section className="rounded-lg border p-3" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <button type="button" onClick={toggle} className="text-sm font-medium underline">
        {open ? "Hide run report" : "Run report"}
      </button>
      {open && (err ? <p role="alert" className="mt-2 text-sm tone-bad">{err}</p> : <pre aria-label="run report" className="mono mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-xs">{text}</pre>)}
    </section>
  );
}
