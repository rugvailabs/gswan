export type EventKind = "plan" | "claim" | "update" | "blocked" | "done" | "verify" | "nudge" | "mention" | "human";
export const EVENT_KINDS: EventKind[] = ["plan", "claim", "update", "blocked", "done", "verify", "nudge", "mention", "human"];

export interface BoardEvent {
  seq: number;
  id: string;
  swarmId: string;
  ts: string;
  agent: string;
  kind: EventKind;
  body: string;
  taskId: string | null;
  refs: string[];
}

export interface EventPage {
  items: BoardEvent[];
  total: number;
  limit: number;
  offset: number;
}

export type TaskStatus = "open" | "awaiting_verification" | "verifying" | "failed" | "done" | "escalated";

export interface TaskView {
  id: string;
  status: TaskStatus;
  failedRounds: number;
  reasons: string[];
}

export interface WorkerView {
  name: string;
  status: "idle" | "running" | "error";
  busy: boolean;
  turns: number;
  costUsd: number;
  owns: string[];
  branch: string | null;
  model: string;
  queue: number;
  lastActivity: string | null;
  activityNote: string | null;
  lastError: string | null;
  task: TaskView | null;
}

export interface MonitorFinding {
  worker: string;
  state: "healthy" | "hung" | "looping" | "forgot" | "waiting" | "blocked";
  detail: string;
}

export interface Budget {
  limits: { max_workers: number; max_wakeups: number; max_wall_s: number; max_cost_usd: number };
  usage: { workers: number; wakeups: number; wall_s: number; cost_usd: number };
}

export interface StatusResponse extends Partial<Budget> {
  ok: boolean;
  allTasksSettled: boolean;
  unsettled: string[];
  workers: WorkerView[];
  monitor: MonitorFinding[];
  digest: string;
}

export interface SwarmInfo {
  id: string;
  task: string;
  status: string;
  createdAt: string;
}
