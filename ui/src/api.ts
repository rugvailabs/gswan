import type { Approval, ApprovalDecision, BoardEvent, EventPage, StatusResponse, SwarmInfo, WorkerDiff, WorkerDraft, LimitsDraft } from "./types";

const BASE: string = import.meta.env.DEV ? "/api" : "";

export function readToken(search: string = window.location.search): string | null {
  return new URLSearchParams(search).get("token");
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(token: string | null, path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = (await res.json()) as { message?: string; detail?: string };
      msg = j.message ?? j.detail ?? msg;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const api = {
  swarm: (t: string | null) => call<SwarmInfo>(t, "/swarms/current"),
  status: (t: string | null) => call<StatusResponse>(t, "/status"),
  events: (t: string | null, sinceSeq = 0) => call<EventPage>(t, `/events?limit=500&sinceSeq=${sinceSeq}`),
  mention: (t: string | null, worker: string, message: string) =>
    call<{ ok: boolean }>(t, `/workers/${encodeURIComponent(worker)}/mentions`, { method: "POST", body: JSON.stringify({ message, sender: "human" }) }),
  addWorker: (t: string | null, w: WorkerDraft) => call<{ ok: boolean }>(t, "/workers", { method: "POST", body: JSON.stringify(w) }),
  setLimits: (t: string | null, l: LimitsDraft) => call<{ ok: boolean }>(t, "/limits", { method: "POST", body: JSON.stringify(l) }),
  decide: (t: string | null, id: string, decision: ApprovalDecision, note = "") =>
    call<{ ok: boolean; approval: Approval }>(t, `/approvals/${encodeURIComponent(id)}`, { method: "POST", body: JSON.stringify({ decision, note }) }),
  diff: (t: string | null, worker: string) => call<WorkerDiff>(t, `/workers/${encodeURIComponent(worker)}/diff`),
  report: (t: string | null) => call<{ markdown: string }>(t, "/report?format=markdown"),
  note: (t: string | null, body: string) =>
    call<BoardEvent>(t, "/events", { method: "POST", body: JSON.stringify({ agent: "human", kind: "human", body }) }),
};

/** Live board stream. EventSource reconnects by itself and resends Last-Event-ID, which the server honours. */
export function openStream(token: string | null, sinceSeq: number, onEvent: (e: BoardEvent) => void, onState: (live: boolean) => void): () => void {
  const q = new URLSearchParams({ sinceSeq: String(sinceSeq) });
  if (token) q.set("token", token);
  const es = new EventSource(`${BASE}/events/stream?${q}`);
  es.addEventListener("board", (m) => onEvent(JSON.parse((m as MessageEvent<string>).data) as BoardEvent));
  es.onopen = () => onState(true);
  es.onerror = () => onState(false);
  return () => es.close();
}
