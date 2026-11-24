import { useEffect } from "react";
import { BoardFeed } from "./components/BoardFeed";
import { DiffPanel } from "./components/DiffPanel";
import { Header } from "./components/Header";
import { MentionBox } from "./components/MentionBox";
import { ReportPanel } from "./components/ReportPanel";
import { WorkerCards } from "./components/WorkerCards";
import { useStore } from "./store";

export function App() {
  const start = useStore((s) => s.start);
  const error = useStore((s) => s.error);
  const hasSwarm = useStore((s) => s.swarm !== null);
  useEffect(() => start(), [start]);

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-4 px-4 py-4">
      <Header />
      {error && (
        <div role="alert" className="rounded-md border px-3 py-2 text-sm tone-bad" style={{ borderColor: "var(--bad)", backgroundColor: "var(--panel)" }}>
          {error}
          {!hasSwarm && <span className="tone-muted"> — start one with <code className="mono">swarm init "task"</code> or the /swarm skill.</span>}
        </div>
      )}
      <WorkerCards />
      <DiffPanel />
      <ReportPanel />
      <div className="grid flex-1 grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <BoardFeed />
        <MentionBox />
      </div>
    </div>
  );
}
