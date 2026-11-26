export type EventKind = "plan" | "claim" | "update" | "blocked" | "done" | "verify" | "nudge" | "mention" | "human" | "research" | "critique" | "decision" | "approval";
export const EVENT_KINDS: EventKind[] = ["plan", "claim", "update", "blocked", "done", "verify", "nudge", "mention", "human", "research", "critique", "decision", "approval"];

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
  group?: string | null;
  queue: number;
  lastActivity: string | null;
  activityNote: string | null;
  recentTools?: string[];
  /** Newer servers may send these; the Overview falls back to plan events when they are missing. */
  goal?: string | null;
  role?: string | null;
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

export type ApprovalDecision = "allow" | "program" | "deny";
export interface Approval {
  id: string;
  worker: string;
  command: string;
  reason: string;
  program: string | null;
  status: "pending" | "allowed" | "denied";
  decision: ApprovalDecision | null;
  note: string;
  createdAt: string;
  decidedAt: string | null;
}
export interface PauseState { paused: boolean; reason: string | null; agents: string[] }

export interface StatusResponse extends Partial<Budget> {
  ok: boolean;
  allTasksSettled: boolean;
  unsettled: string[];
  pendingApprovals?: Approval[];
  pause?: PauseState;
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

// ---- Result API (GET /result and friends): where the final work is and what to do with it ----
export type ResultStage = "none" | "workers" | "integration" | "applied";
export interface ResultLocation { path: string; branch: string | null; kind: "integration" | "worker" | "repo"; worker: string | null }
export interface ResultWorker { name: string; branch: string | null; path: string | null; taskStatus: string | null }
export type ProjectKind = "node" | "python" | "fullstack" | "static" | "unknown";
export interface ResultProject {
  kind: ProjectKind;
  label: string;
  runCommand: string[] | null;
  installCommand: string[] | null;
  template: string | null;
  cwd: string;
}
export type ResultFileStatus = "added" | "modified" | "deleted" | "unchanged";
export interface ResultFile { path: string; status: ResultFileStatus }
export interface ResultInfo {
  stage: ResultStage;
  location: ResultLocation | null;
  workers: ResultWorker[];
  merged: string[];
  verified: boolean | null;
  applyCommand: string | null;
  project: ResultProject | null;
  files: ResultFile[];
  summary: { added: number; modified: number; deleted: number; total: number };
}
export interface RunState {
  running: boolean;
  command: string[] | null;
  cwd: string | null;
  startedAt: string | null;
  exitCode: number | null;
  url: string | null;
  log: string[];
}
export type OpenTarget = "folder" | "vscode";
