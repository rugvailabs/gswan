import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { BoardFeed } from "./components/BoardFeed";
import { DiffPanel } from "./components/DiffPanel";
import { Header } from "./components/Header";
import { HumanQueue } from "./components/HumanQueue";
import { MentionBox } from "./components/MentionBox";
import { ReportPanel } from "./components/ReportPanel";
import { Overview } from "./components/simple/Overview";
import { WorkerCards } from "./components/WorkerCards";
import { useStore } from "./store";

export type Tab = "overview" | "advanced";
export const tabFromHash = (hash: string): Tab => (hash.replace(/^#/, "") === "advanced" ? "advanced" : "overview");

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "advanced", label: "Advanced" },
];

/** Overview (plain, default) or Advanced (the technical dashboard), kept in the URL hash so `#advanced` links work. */
function useTab(): [Tab, (t: Tab) => void] {
  const [tab, setTab] = useState<Tab>(() => tabFromHash(window.location.hash));
  useEffect(() => {
    const on = () => setTab(tabFromHash(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const choose = (t: Tab) => {
    setTab(t);
    if (t === "advanced") window.location.hash = "advanced";
    else window.history.replaceState(null, "", window.location.pathname + window.location.search);
  };
  return [tab, choose];
}

function Advanced() {
  return (
    <>
      <Header />
      <HumanQueue />
      <WorkerCards />
      <DiffPanel />
      <ReportPanel />
      <div className="grid flex-1 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <BoardFeed />
        <MentionBox />
      </div>
    </>
  );
}

export function App() {
  const start = useStore((s) => s.start);
  const error = useStore((s) => s.error);
  const hasSwarm = useStore((s) => s.swarm !== null);
  const [tab, setTab] = useTab();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => start(), [start]);

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (i + (e.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
    setTab(TABS[next]!.key);
    refs.current[next]?.focus();
  };

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-4 px-4 py-4">
      <nav className="flex items-center justify-between gap-3">
        <span className="text-lg font-semibold">todo</span>
        <div role="tablist" aria-label="dashboard view" className="flex gap-1 rounded-lg border p-1" style={{ backgroundColor: "var(--panel)", borderColor: "var(--line)" }}>
          {TABS.map((t, i) => (
            <button key={t.key} ref={(el) => { refs.current[i] = el; }} type="button" role="tab" id={`tab-${t.key}`} aria-selected={tab === t.key} aria-controls={`panel-${t.key}`}
              tabIndex={tab === t.key ? 0 : -1} onClick={() => setTab(t.key)} onKeyDown={(e) => onKey(e, i)}
              className="rounded-md px-3 py-1 text-sm font-medium"
              style={tab === t.key ? { backgroundColor: "var(--info)", color: "#fff" } : { color: "var(--muted)" }}>
              {t.label}
            </button>
          ))}
        </div>
      </nav>
      {error && (
        <div role="alert" className="rounded-md border px-3 py-2 text-sm tone-bad" style={{ borderColor: "var(--bad)", backgroundColor: "var(--panel)" }}>
          {error}
          {!hasSwarm && <span className="tone-muted"> — start one with <code className="mono">swarm init "task"</code> or the /swarm skill.</span>}
        </div>
      )}
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="flex flex-1 flex-col gap-4">
        {tab === "overview" ? <Overview /> : <Advanced />}
      </div>
    </div>
  );
}
