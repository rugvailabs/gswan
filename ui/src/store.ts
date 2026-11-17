import { create } from "zustand";
import { api, openStream } from "./api";
import { mergeEvents } from "./lib";
import type { BoardEvent, EventKind, LimitsDraft, StatusResponse, SwarmInfo, WorkerDraft } from "./types";

interface State {
  token: string | null;
  swarm: SwarmInfo | null;
  status: StatusResponse | null;
  events: BoardEvent[];
  live: boolean;
  error: string | null;
  kinds: ReadonlySet<EventKind>;
  agent: string | null;
  mentionTarget: string | null;
  view: "board" | "threads";
  threadAgent: string | null;
  setView: (v: "board" | "threads") => void;
  openThread: (a: string) => void;
  addWorker: (w: WorkerDraft) => Promise<void>;
  setLimits: (l: LimitsDraft) => Promise<void>;
  setToken: (t: string | null) => void;
  addEvents: (e: BoardEvent[]) => void;
  toggleKind: (k: EventKind) => void;
  setAgent: (a: string | null) => void;
  setMentionTarget: (w: string | null) => void;
  setError: (e: string | null) => void;
  refresh: () => Promise<void>;
  start: () => () => void;
  sendMention: (worker: string, message: string) => Promise<void>;
  sendNote: (message: string) => Promise<void>;
}

const msg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export const useStore = create<State>((set, get) => ({
  token: null,
  swarm: null,
  status: null,
  events: [],
  live: false,
  error: null,
  kinds: new Set<EventKind>(),
  agent: null,
  mentionTarget: null,
  view: "board",
  threadAgent: null,
  setView: (view) => set({ view }),
  openThread: (threadAgent) => set({ view: "threads", threadAgent }),
  addWorker: async (w) => {
    try { await api.addWorker(get().token, w); set({ error: null }); await get().refresh(); } catch (e) { set({ error: msg(e) }); throw e; }
  },
  setLimits: async (l) => {
    try { await api.setLimits(get().token, l); set({ error: null }); await get().refresh(); } catch (e) { set({ error: msg(e) }); throw e; }
  },
  setToken: (token) => set({ token }),
  addEvents: (incoming) => set((s) => ({ events: mergeEvents(s.events, incoming) })),
  toggleKind: (k) =>
    set((s) => {
      const next = new Set(s.kinds);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return { kinds: next };
    }),
  setAgent: (agent) => set({ agent }),
  setMentionTarget: (mentionTarget) => set({ mentionTarget }),
  setError: (error) => set({ error }),
  refresh: async () => {
    const { token } = get();
    try {
      const [swarm, status] = await Promise.all([api.swarm(token), api.status(token)]);
      set({ swarm, status, error: null });
    } catch (e) {
      set({ error: msg(e) });
    }
  },
  start: () => {
    let closeStream = () => {};
    let timer: ReturnType<typeof setInterval> | undefined;
    let cancelled = false;
    void (async () => {
      const { token, refresh, addEvents } = get();
      await refresh();
      try {
        const page = await api.events(token);
        addEvents(page.items);
      } catch (e) {
        set({ error: msg(e) });
      }
      if (cancelled) return;
      const last = get().events.at(-1)?.seq ?? 0;
      closeStream = openStream(token, last, (ev) => get().addEvents([ev]), (live) => set({ live }));
      timer = setInterval(() => void get().refresh(), 3000);
    })();
    return () => {
      cancelled = true;
      closeStream();
      if (timer) clearInterval(timer);
    };
  },
  sendMention: async (worker, message) => {
    try {
      await api.mention(get().token, worker, message);
      set({ error: null });
      await get().refresh();
    } catch (e) {
      set({ error: msg(e) });
      throw e;
    }
  },
  sendNote: async (message) => {
    try {
      const ev = await api.note(get().token, message);
      get().addEvents([ev]);
      set({ error: null });
    } catch (e) {
      set({ error: msg(e) });
      throw e;
    }
  },
}));
