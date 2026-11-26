import { useEffect, useRef, useState } from "react";
import { api, ApiError, zipUrl } from "../../api";
import { buildTree, partName, resultHeadline, type TreeNode } from "../../plain";
import { useStore } from "../../store";
import type { OpenTarget, ResultInfo, RunState } from "../../types";
import { copyText, panel, primaryBtn, primaryStyle, secondaryBtn, secondaryStyle, toneClass } from "./ui";

const POLL_MS = 5000;
const RUN_POLL_MS = 1500;

/** Plain message for a failed result action; 404 means there is no single copy to act on yet. */
function actionError(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) return "Pick a part first: there is no single copy to use yet.";
  if (e instanceof ApiError && e.status === 409) return "The project is already running.";
  return e instanceof Error ? e.message : String(e);
}

/** Where the final result is, what is in it, and buttons to open, download or run it. */
export function YourProject() {
  const result = useStore((s) => s.result);
  const load = useStore((s) => s.resultLoad);
  const error = useStore((s) => s.resultError);
  const loadResult = useStore((s) => s.loadResult);
  const hasSwarm = useStore((s) => s.swarm !== null);
  // refresh when a part finishes or a check lands: that is when the result changes
  const lastKey = useStore((s) => {
    for (let i = s.events.length - 1; i >= 0; i--) { const k = s.events[i]!.kind; if (k === "done" || k === "verify") return s.events[i]!.seq; }
    return 0;
  });

  useEffect(() => {
    void loadResult();
    const t = setInterval(() => void loadResult(), POLL_MS);
    return () => clearInterval(t);
  }, [loadResult]);
  useEffect(() => { if (lastKey) void loadResult(); }, [lastKey, loadResult]);

  return (
    <section aria-label="your project" className="flex flex-col gap-3 rounded-xl border-2 p-4" style={{ ...panel, borderColor: result?.verified || result?.stage === "applied" ? "var(--ok)" : "var(--line)" }}>
      <h2 className="text-xs font-semibold uppercase tracking-wide tone-muted">Your project</h2>
      {load === "unavailable" ? (
        <div role="status">
          <p className="text-lg font-semibold">Result info unavailable</p>
          <p className="text-sm tone-muted">This server cannot show where the result is yet. Update it, or use the Advanced tab to see each part's changes.</p>
        </div>
      ) : load === "idle" || (load === "loading" && !result) ? (
        <p className="text-sm tone-muted">Looking for your project…</p>
      ) : !result ? (
        <div role="status">
          <p className="text-lg font-semibold">{hasSwarm ? "Could not load the result" : "Nothing built yet"}</p>
          {error && <p className="text-sm tone-muted">{error}</p>}
        </div>
      ) : (
        <ResultBody r={result} />
      )}
    </section>
  );
}

function ResultBody({ r }: { r: ResultInfo }) {
  const token = useStore((s) => s.token);
  const worker = useStore((s) => s.resultWorker);
  const setWorker = useStore((s) => s.setResultWorker);
  const head = resultHeadline(r);
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null);
  const [asking, setAsking] = useState(false);
  const [run, setRun] = useState<RunState | null>(null);
  const loc = r.location;
  const parts = r.workers.filter((w) => w.path);

  const open = async (target: OpenTarget) => {
    try {
      const res = await api.openResult(token, target, worker);
      setNote({ text: target === "vscode" ? "Opening in VS Code…" : `Opening ${res.path}…`, bad: false });
    } catch (e) { setNote({ text: actionError(e), bad: true }); }
  };
  const copy = async (text: string, what: string) => {
    const ok = await copyText(text);
    setNote(ok ? { text: `Copied the ${what}.`, bad: false } : { text: `Could not copy. Select the ${what} and copy it by hand.`, bad: true });
  };

  return (
    <div className="flex flex-col gap-3">
      <p className={`text-xl font-semibold ${toneClass(head.tone)}`} aria-label="result headline">{head.text}</p>
      {r.stage === "none" && <p className="text-sm tone-muted">When the builders save their work, it will show up here.</p>}
      {r.stage === "workers" && <p className="text-sm tone-muted">The parts are not combined yet. Each builder works in a separate draft so they do not get in each other's way. Pick one to look at.</p>}
      {(r.stage === "workers" || worker) && parts.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-sm">
          <span>Show the part:</span>
          <select aria-label="which part to show" value={worker ?? ""} onChange={(e) => setWorker(e.target.value || null)}
            className="rounded-md border px-2 py-1" style={{ borderColor: "var(--line)", backgroundColor: "var(--bg)", color: "var(--text)" }}>
            <option value="">{r.stage === "workers" ? "Choose a part…" : "The combined copy"}</option>
            {parts.map((w) => <option key={w.name} value={w.name}>{partName(w.name)}{w.taskStatus === "done" ? " (checked)" : ""}</option>)}
          </select>
        </label>
      )}

      {loc && (
        <>
          <div className="flex flex-col gap-1">
            <p className="text-sm tone-muted">It is in this folder on your computer:</p>
            <div className="flex flex-wrap items-center gap-2">
              <code aria-label="project folder" className="mono min-w-0 flex-1 break-all rounded-md px-2 py-1.5 text-sm" style={{ backgroundColor: "var(--bg)" }}>{loc.path}</code>
              <button type="button" className={secondaryBtn} style={secondaryStyle} onClick={() => void copy(loc.path, "folder path")}>Copy</button>
            </div>
            {loc.branch && <p className="text-xs tone-muted">Saved on branch <span className="mono">{loc.branch}</span></p>}
          </div>
          {r.project && (
            <p className="text-sm">
              <span className="font-medium">{r.project.label}</span>
              {r.project.template && <span className="tone-muted"> · Started from template {r.project.template}</span>}
            </p>
          )}
          <p aria-label="change summary" className="text-sm">
            <span className="tone-ok font-medium">{r.summary.added} new file{r.summary.added === 1 ? "" : "s"}</span>
            {", "}<span className="tone-warn font-medium">{r.summary.modified} changed</span>
            {r.summary.deleted > 0 && <>{", "}<span className="tone-bad font-medium">{r.summary.deleted} removed</span></>}
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={secondaryBtn} style={secondaryStyle} onClick={() => void open("folder")}>Open folder</button>
            <button type="button" className={secondaryBtn} style={secondaryStyle} onClick={() => void open("vscode")}>Open in VS Code</button>
            <a className={secondaryBtn} style={secondaryStyle} href={zipUrl(token, worker)} download>Download zip</a>
            <button type="button" className={primaryBtn} style={primaryStyle} disabled={!r.project?.runCommand || run?.running}
              title={r.project?.runCommand ? `Runs: ${r.project.runCommand.join(" ")}` : "We do not know how to start this kind of project yet."}
              onClick={() => setAsking(true)}>▶ Run it</button>
          </div>
          {!r.project?.runCommand && <p className="text-xs tone-muted">We do not know how to start this kind of project yet, so "Run it" is off.</p>}
          {r.applyCommand && r.stage !== "applied" && (
            <div className="flex flex-col gap-1 rounded-lg border p-3" style={{ borderColor: "var(--line)" }}>
              <p className="text-sm">To add it to your project, run:</p>
              <div className="flex flex-wrap items-center gap-2">
                <code aria-label="apply command" className="mono flex-1 rounded px-2 py-1 text-sm" style={{ backgroundColor: "var(--bg)" }}>{r.applyCommand}</code>
                <button type="button" className={secondaryBtn} style={secondaryStyle} onClick={() => void copy(r.applyCommand ?? "", "command")}>Copy</button>
              </div>
            </div>
          )}
          {note && <p role="status" className={`text-sm ${note.bad ? "tone-bad" : "tone-ok"}`}>{note.text}</p>}
          <Files r={r} />
          <RunPanel r={r} run={run} setRun={setRun} worker={worker} />
        </>
      )}
      {asking && r.project && <RunDialog r={r} worker={worker} onClose={() => setAsking(false)} onStarted={(s) => { setRun(s); setAsking(false); }} />}
    </div>
  );
}

function Files({ r }: { r: ResultInfo }) {
  const [open, setOpen] = useState(false);
  const tree = buildTree(r.files);
  if (!r.files.length) return null;
  return (
    <div className="flex flex-col gap-1">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="self-start text-sm tone-info underline">
        {open ? "Hide the files" : `Show the files (${r.summary.total})`}
      </button>
      {open && (
        <ul role="tree" aria-label="project files" className="mono rounded-lg border p-2 text-sm" style={{ borderColor: "var(--line)" }}>
          {tree.map((n) => <Node key={n.path} n={n} depth={0} />)}
        </ul>
      )}
    </div>
  );
}

const BADGE = { added: ["new", "tone-ok"], modified: ["changed", "tone-warn"], deleted: ["removed", "tone-bad"] } as const;

function Node({ n, depth }: { n: TreeNode; depth: number }) {
  const [open, setOpen] = useState(depth === 0 && n.changed > 0);
  const pad = { paddingLeft: `${depth * 1}rem` };
  if (!n.folder) {
    const b = n.status && n.status !== "unchanged" ? BADGE[n.status] : null;
    return (
      <li role="treeitem" aria-label={n.path} style={pad} className="flex items-center gap-2 py-0.5">
        <span className={n.status === "deleted" ? "line-through tone-muted" : ""}>{n.name}</span>
        {b && <span className={`rounded px-1 text-[0.65rem] font-sans font-semibold ${b[1]}`} style={{ border: "1px solid currentColor" }}>{b[0]}</span>}
      </li>
    );
  }
  return (
    <li role="treeitem" aria-label={`${n.path}/`} aria-expanded={open} className="py-0.5">
      <button type="button" onClick={() => setOpen(!open)} style={pad} className="flex items-center gap-1 text-left">
        <span aria-hidden className="w-3">{open ? "▾" : "▸"}</span>{n.name}/
        {n.changed > 0 && <span className="font-sans text-xs tone-muted"> ({n.changed} changed)</span>}
      </button>
      {open && <ul role="group">{n.children.map((c) => <Node key={c.path} n={c} depth={depth + 1} />)}</ul>}
    </li>
  );
}

function RunDialog({ r, worker, onClose, onStarted }: { r: ResultInfo; worker: string | null; onClose: () => void; onStarted: (s: RunState) => void }) {
  const token = useStore((s) => s.token);
  const [install, setInstall] = useState(!!r.project?.installCommand);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { confirmRef.current?.focus(); }, []);
  const go = async () => {
    setBusy(true); setErr(null);
    try { onStarted(await api.startRun(token, install, worker)); } catch (e) { setErr(actionError(e)); } finally { setBusy(false); }
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ backgroundColor: "rgb(0 0 0 / 0.45)" }}
      onKeyDown={(e) => { if (e.key === "Escape") onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="run-title" className="flex w-full max-w-md flex-col gap-3 rounded-xl border p-5" style={panel}>
        <h3 id="run-title" className="text-lg font-semibold">Run the project?</h3>
        <p className="text-sm">This will run the code the agents wrote on your computer. Only continue if you are fine with that.</p>
        <p className="text-sm tone-muted">It starts with <code className="mono">{r.project?.runCommand?.join(" ")}</code>.</p>
        {r.project?.installCommand && (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={install} onChange={(e) => setInstall(e.target.checked)} className="mt-1" />
            <span>Install what it needs first (<code className="mono">{r.project.installCommand.join(" ")}</code>). This downloads libraries from the internet.</span>
          </label>
        )}
        {err && <p role="alert" className="text-sm tone-bad">{err}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className={secondaryBtn} style={secondaryStyle} onClick={onClose}>Cancel</button>
          <button ref={confirmRef} type="button" disabled={busy} className={primaryBtn} style={primaryStyle} onClick={() => void go()}>{busy ? "Starting…" : "Yes, run it"}</button>
        </div>
      </div>
    </div>
  );
}

function RunPanel({ r, run, setRun, worker }: { r: ResultInfo; run: RunState | null; setRun: (s: RunState) => void; worker: string | null }) {
  const token = useStore((s) => s.token);
  const [err, setErr] = useState<string | null>(null);
  const canRun = !!r.project?.runCommand;
  // pick up a run that was started before this page opened
  useEffect(() => {
    if (!canRun) return;
    let gone = false;
    api.runState(token).then((s) => { if (!gone && (s.running || s.log.length)) setRun(s); }).catch(() => { /* no run yet */ });
    return () => { gone = true; };
  }, [canRun, token, setRun, worker]);
  const running = run?.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => {
      api.runState(token).then(setRun).catch((e: unknown) => setErr(actionError(e)));
    }, RUN_POLL_MS);
    return () => clearInterval(t);
  }, [running, token, setRun]);
  if (!run) return null;
  const stop = async () => {
    try { setRun(await api.stopRun(token)); } catch (e) { setErr(actionError(e)); }
  };
  return (
    <div role="region" aria-label="running project" className="flex flex-col gap-2 rounded-lg border p-3" style={{ borderColor: "var(--line)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium" aria-label="run status">
          {run.running ? <span className="tone-info">● Running</span> : run.exitCode === 0 || run.exitCode === null ? <span className="tone-muted">Stopped</span> : <span className="tone-bad">Stopped with an error (code {run.exitCode})</span>}
        </p>
        <div className="flex gap-2">
          {run.url && <a href={run.url} target="_blank" rel="noreferrer" className={primaryBtn} style={primaryStyle}>Open the app</a>}
          {run.running && <button type="button" className={secondaryBtn} style={{ ...secondaryStyle, color: "var(--bad)" }} onClick={() => void stop()}>Stop</button>}
        </div>
      </div>
      <pre aria-label="run log" className="mono max-h-64 overflow-auto whitespace-pre-wrap break-all rounded p-2 text-xs" style={{ backgroundColor: "var(--bg)" }}>
        {run.log.length ? run.log.join("\n") : "Starting…"}
      </pre>
      {err && <p role="alert" className="text-sm tone-bad">{err}</p>}
    </div>
  );
}
