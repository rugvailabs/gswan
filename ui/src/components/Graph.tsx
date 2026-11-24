import { useMemo, useState } from "react";
import { mentionGraph } from "../lib";
import { useStore } from "../store";

const W = 640, H = 420, R = 22;

/** Who messaged whom: arrows weighted by message count; the slider rebuilds the graph as it stood at any event. */
export function Graph() {
  const events = useStore((s) => s.events);
  const [system, setSystem] = useState(false); // swarmd's automatic briefs to verifiers and critics
  const mentions = useMemo(() => events.filter((e) => e.kind === "mention" && (system || e.agent !== "swarmd")), [events, system]);
  const [upto, setUpto] = useState<number | null>(null); // null = live
  const at = upto ?? Infinity;
  const g = useMemo(() => mentionGraph(mentions, at), [mentions, at]);
  const toggle = (
    <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={system} onChange={(e) => setSystem(e.target.checked)} /> include swarmd briefs</label>
  );
  if (mentions.length === 0) return <div className="flex flex-col gap-2 p-4 text-sm"><p className="tone-muted">No messages yet. The graph fills in as agents mention each other.</p>{toggle}</div>;

  const idx = upto === null ? mentions.length - 1 : Math.max(0, mentions.findIndex((e) => e.seq >= upto));
  const pos = new Map(g.nodes.map((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(1, g.nodes.length) - Math.PI / 2;
    return [n, { x: W / 2 + (W / 2 - 90) * Math.cos(a), y: H / 2 + (H / 2 - 50) * Math.sin(a) }];
  }));
  const max = Math.max(1, ...g.edges.map((e) => e.count));
  const pairs = new Set(g.edges.map((e) => `${e.from}\u0000${e.to}`));

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <label className="flex flex-1 items-center gap-2">
          <span className="tone-muted">up to message</span>
          <input type="range" aria-label="graph position" min={0} max={mentions.length - 1} value={idx} className="flex-1"
            onChange={(e) => { const i = Number(e.target.value); setUpto(i >= mentions.length - 1 ? null : mentions[i]?.seq ?? null); }} />
          <span className="mono">{idx + 1}/{mentions.length}</span>
        </label>
        <button type="button" aria-pressed={upto === null} onClick={() => setUpto(null)} className="rounded border px-2 py-0.5" style={{ borderColor: "var(--line)" }}>
          {upto === null ? "● live" : "go live"}
        </button>
        {toggle}
      </div>
      <svg role="img" aria-label="who messaged whom" viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ maxHeight: "60vh" }}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill="var(--info)" />
          </marker>
        </defs>
        {g.edges.map((e) => {
          const a = pos.get(e.from)!, b = pos.get(e.to)!;
          const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
          const ux = dx / len, uy = dy / len;
          const bend = pairs.has(`${e.to}\u0000${e.from}`) ? 28 : 0; // two-way traffic: bend each direction apart
          const sx = a.x + ux * R, sy = a.y + uy * R, tx = b.x - ux * (R + 4), ty = b.y - uy * (R + 4);
          const cx = (sx + tx) / 2 - uy * bend, cy = (sy + ty) / 2 + ux * bend;
          return (
            <g key={`${e.from}-${e.to}`}>
              <path d={`M${sx},${sy} Q${cx},${cy} ${tx},${ty}`} fill="none" stroke="var(--info)" strokeOpacity={0.75}
                strokeWidth={1.5 + (5 * e.count) / max} markerEnd="url(#arrow)" />
              <text x={cx} y={cy - 4} textAnchor="middle" fontSize="12" fill="var(--muted)" stroke="var(--panel)" strokeWidth={4} paintOrder="stroke">{e.count}</text>
            </g>
          );
        })}
        {g.nodes.map((n) => {
          const p = pos.get(n)!;
          const fill = n === "human" ? "var(--warn)" : n === "coordinator" ? "var(--ok)" : "var(--panel)";
          return (
            <g key={n}>
              <circle cx={p.x} cy={p.y} r={R} fill={fill} stroke="var(--text)" strokeOpacity={0.5} />
              <text x={p.x} y={p.y + R + 16} textAnchor="middle" fontSize="13" fill="var(--text)" stroke="var(--panel)" strokeWidth={5} paintOrder="stroke" className="mono">{n}</text>
            </g>
          );
        })}
      </svg>
      <table aria-label="messages between agents" className="w-full text-sm">
        <thead><tr className="text-left text-xs tone-muted"><th className="py-1">from</th><th>to</th><th className="text-right">messages</th></tr></thead>
        <tbody>
          {g.edges.map((e) => (
            <tr key={`${e.from}-${e.to}`} className="border-t" style={{ borderColor: "var(--line)" }}>
              <td className="mono py-1">{e.from}</td><td className="mono">{e.to}</td><td className="mono text-right">{e.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
