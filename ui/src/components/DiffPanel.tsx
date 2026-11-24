import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { findingMatchesFile, findingTarget, parsePatch } from "../lib";
import { useStore } from "../store";
import type { DiffFile, DiffFinding, WorkerDiff } from "../types";

const SEVERITY_TONE: Record<DiffFinding["severity"], string> = { blocker: "tone-bad", major: "tone-warn", nit: "tone-muted" };
const STATUS_LABEL: Record<string, string> = { A: "added", M: "modified", D: "deleted" };

function Finding({ f }: { f: DiffFinding }) {
  return (
    <li className="text-xs">
      <span className={`font-medium ${SEVERITY_TONE[f.severity]}`}>{f.severity}</span>
      {f.location && <span className="mono tone-muted"> {f.location}</span>} {f.reason}
      <span className="tone-muted"> — {f.critic}, round {f.round}</span>
    </li>
  );
}

function FileDiff({ file, findings, open }: { file: DiffFile; findings: DiffFinding[]; open: boolean }) {
  const cited = new Set(findings.map((f) => findingTarget(f.location).line).filter((n): n is number => n !== null));
  return (
    <details open={open} className="rounded border" style={{ borderColor: "var(--line)" }}>
      <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-2 py-1.5 text-sm">
        <span className="mono min-w-0 break-all font-medium">{file.path}</span>
        <span className="text-xs tone-muted">{STATUS_LABEL[file.status] ?? file.status}</span>
        {file.binary ? <span className="text-xs tone-muted">binary</span> : (
          <span className="mono text-xs"><span className="tone-ok">+{file.additions ?? 0}</span> <span className="tone-bad">−{file.deletions ?? 0}</span></span>
        )}
        {file.outOfScope && <span className="text-xs font-medium tone-bad">outside owned paths</span>}
        {findings.length > 0 && <span className="text-xs tone-warn">{findings.length} finding{findings.length > 1 ? "s" : ""}</span>}
      </summary>
      {findings.length > 0 && <ul aria-label={`findings for ${file.path}`} className="space-y-1 border-t px-2 py-1.5" style={{ borderColor: "var(--line)" }}>{findings.map((f, i) => <Finding key={i} f={f} />)}</ul>}
      {file.patch && (
        <div aria-label={`patch ${file.path}`} className="mono overflow-x-auto border-t text-xs leading-5" style={{ borderColor: "var(--line)" }}>
          {parsePatch(file.patch).map((l, i) => (
            <div key={i} className={`diff-line ${l.kind === "add" ? "diff-add" : l.kind === "del" ? "diff-del" : l.kind === "hunk" ? "diff-hunk" : ""} ${l.newNo !== null && cited.has(l.newNo) ? "diff-cited" : ""}`}>
              <span className="diff-no">{l.oldNo ?? ""}</span>
              <span className="diff-no">{l.newNo ?? ""}</span>
              <span className="diff-text">{l.text || " "}</span>
            </div>
          ))}
        </div>
      )}
      {(file.truncated || file.redacted) && (
        <p className="border-t px-2 py-1 text-xs tone-warn" style={{ borderColor: "var(--line)" }}>
          {file.truncated && "Large file: only the first part of this patch is shown. "}
          {file.redacted && "A value shaped like a credential was hidden in this patch."}
        </p>
      )}
    </details>
  );
}

/** What a worker's branch changed since its base commit, next to the critic findings and verifier verdicts about it. */
export function DiffPanel() {
  const token = useStore((s) => s.token);
  const worker = useStore((s) => s.diffWorker);
  const close = useStore((s) => s.openDiff);
  const [data, setData] = useState<WorkerDiff | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const latest = useRef(0);

  const load = useCallback(async (name: string) => {
    const id = ++latest.current;
    setLoading(true);
    try {
      const d = await api.diff(token, name);
      if (id !== latest.current) return; // a newer request (another worker, or a refresh) superseded this one
      setData(d);
      setErr(null);
    } catch (e) {
      if (id !== latest.current) return;
      setErr(e instanceof Error ? e.message : "could not load the diff");
    } finally {
      if (id === latest.current) setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    setData(null);
    setErr(null);
    if (worker) void load(worker);
  }, [worker, load]);

  if (!worker) return null;
  const files = data?.files ?? [];
  const general = (data?.critiques ?? []).filter((f) => !files.some((x) => findingMatchesFile(f.location, x.path)));
  const drift = files.filter((f) => f.outOfScope).length;
  const btn = "rounded border px-2 py-1 text-xs";

  return (
    <section aria-label={`diff ${worker}`} className="flex min-w-0 flex-col gap-3 rounded-lg border p-3" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Changes by <span className="mono">{worker}</span></h2>
        <div className="flex gap-2">
          <button type="button" className={btn} style={{ borderColor: "var(--line)" }} disabled={loading} onClick={() => void load(worker)}>{loading ? "Loading…" : "Refresh"}</button>
          <button type="button" className={btn} style={{ borderColor: "var(--line)" }} onClick={() => close(null)}>Close</button>
        </div>
      </div>
      {err && <p role="alert" className="text-sm tone-bad">{err}</p>}
      {data && (
        <>
          <p className="text-sm tone-muted">{data.goal}</p>
          <p aria-label="diff summary" className="text-sm">
            {data.totals.files} file{data.totals.files === 1 ? "" : "s"} changed · <span className="mono tone-ok">+{data.totals.additions}</span> <span className="mono tone-bad">−{data.totals.deletions}</span>
            {data.task && <> · task <span className="font-medium">{data.task.status.replace("_", " ")}</span></>}
            {drift > 0 && <span className="font-medium tone-bad"> · {drift} outside owned paths ({data.owns.join(", ")})</span>}
            {data.branch && <span className="mono tone-muted"> · {data.branch}{data.base ? ` since ${data.base.slice(0, 8)}` : ""}</span>}
          </p>
          {data.note && <p className="text-sm tone-muted">{data.note}</p>}
          {data.uncommitted.length > 0 && (
            <p className="text-sm tone-warn">Not committed yet, so not shown below: <span className="mono">{data.uncommitted.slice(0, 8).join(", ")}{data.uncommitted.length > 8 ? ` and ${data.uncommitted.length - 8} more` : ""}</span></p>
          )}
          {data.verdicts.length > 0 && (
            <ul aria-label="verdicts" className="space-y-1 text-sm">
              {data.verdicts.map((v) => (
                <li key={v.seq}><span className={`font-medium ${v.passed ? "tone-ok" : "tone-bad"}`}>{v.passed ? "pass" : "fail"}</span> {v.text} <span className="text-xs tone-muted">— {v.by}, #{v.seq}</span></li>
              ))}
            </ul>
          )}
          {general.length > 0 && (
            <div>
              <h3 className="text-xs font-medium tone-muted">Critic findings not tied to a changed file</h3>
              <ul aria-label="general findings" className="mt-1 space-y-1">{general.map((f, i) => <Finding key={i} f={f} />)}</ul>
            </div>
          )}
          {data.files !== null && files.length === 0 && !data.note && <p className="text-sm tone-muted">No committed changes on this branch yet.</p>}
          <div className="flex min-w-0 flex-col gap-2">
            {files.map((f) => {
              const fs = data.critiques.filter((c) => findingMatchesFile(c.location, f.path));
              return <FileDiff key={f.path} file={f} findings={fs} open={files.length <= 4 || f.outOfScope || fs.length > 0} />;
            })}
          </div>
          {data.moreFiles > 0 && <p className="text-sm tone-warn">{data.moreFiles} more changed files are not shown. Run <code className="mono">git diff {data.base?.slice(0, 8)}..{data.branch}</code> to see everything.</p>}
        </>
      )}
    </section>
  );
}
