export type EventKind = "plan" | "claim" | "update" | "blocked" | "done" | "verify" | "nudge" | "mention" | "human" | "research" | "critique" | "decision";
export const EVENT_KINDS: EventKind[] = ["plan", "claim", "update", "blocked", "done", "verify", "nudge", "mention", "human", "research", "critique", "decision"];

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

/** input = fresh input + cache writes; cached = cache reads (large and cheap); total = input + output, which is what the budget counts. */
export interface TokenCount { input: number; output: number; cached: number; total: number }

export interface WorkerView {
  name: string;
  status: "idle" | "running" | "error";
  busy: boolean;
  turns: number;
  tokens: TokenCount;
  owns: string[];
  branch: string | null;
  model: string;
  queue: number;
  lastActivity: string | null;
  activityNote: string | null;
  recentTools?: string[];
  lastError: string | null;
  task: TaskView | null;
}

export interface MonitorFinding {
  worker: string;
  state: "healthy" | "hung" | "looping" | "forgot" | "waiting" | "blocked";
  detail: string;
}

export interface Budget {
  limits: { max_workers: number; max_wakeups: number; max_wall_s: number; max_tokens: number };
  usage: { workers: number; wakeups: number; wall_s: number; tokens: number; tokens_cached: number };
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

export interface WorkerDraft { name: string; goal: string; owns: string[]; tools: string[]; model: "sonnet" | "haiku" | "opus"; message?: string }
export interface LimitsDraft { maxWorkers?: number; maxWakeups?: number; maxWallS?: number; maxTokens?: number }

export interface DiffFile {
  path: string;
  status: string; // A | M | D
  additions: number | null; // null for binary files
  deletions: number | null;
  binary: boolean;
  outOfScope: boolean;
  redacted: boolean;
  truncated: boolean;
  patch: string;
}
export interface DiffFinding { critic: string; round: number; severity: "blocker" | "major" | "nit"; location: string; reason: string }
export interface DiffVerdict { seq: number; passed: boolean; by: string; text: string }
export interface WorkerDiff {
  worker: string;
  role: string;
  goal: string;
  owns: string[];
  branch: string | null;
  base: string | null;
  task: TaskView | null;
  files: DiffFile[] | null; // null when there is no branch to diff yet
  moreFiles: number;
  note: string | null;
  uncommitted: string[];
  totals: { files: number; additions: number; deletions: number };
  critiques: DiffFinding[];
  verdicts: DiffVerdict[];
}
