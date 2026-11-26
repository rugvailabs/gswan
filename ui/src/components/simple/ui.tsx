import type { CSSProperties, ReactNode } from "react";
import type { Tone } from "../../plain";

export const toneClass = (t: Tone): string => ({ good: "tone-ok", bad: "tone-bad", warn: "tone-warn", info: "tone-info" })[t];
export const toneVar = (t: Tone): string => ({ good: "var(--ok)", bad: "var(--bad)", warn: "var(--warn)", info: "var(--info)" })[t];

export const panel: CSSProperties = { backgroundColor: "var(--panel)", borderColor: "var(--line)" };
export const primaryBtn = "rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2";
export const primaryStyle: CSSProperties = { backgroundColor: "var(--info)", color: "#fff" };
export const secondaryBtn = "rounded-md border px-3 py-1.5 text-sm font-medium disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2";
export const secondaryStyle: CSSProperties = { borderColor: "var(--line)", color: "var(--text)", backgroundColor: "var(--bg)" };
export const inputCls = "w-full rounded-md border px-2 py-1.5 text-sm";
export const inputStyle: CSSProperties = { borderColor: "var(--line)", backgroundColor: "var(--bg)", color: "var(--text)" };

export function Section({ label, title, children, accent }: { label: string; title: ReactNode; children: ReactNode; accent?: string }) {
  return (
    <section aria-label={label} className="flex flex-col gap-3 rounded-xl border p-4" style={{ ...panel, ...(accent ? { borderColor: accent } : {}) }}>
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

export function Bar({ value, label, color = "var(--info)" }: { value: number; label: string; color?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="h-2 w-full overflow-hidden rounded-full" style={{ backgroundColor: "var(--line)" }}>
      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: color }} />
    </div>
  );
}

/** Copy text, with a short "Copied" confirmation. Clipboard can be unavailable (http, old browsers): then select-to-copy is the fallback. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
