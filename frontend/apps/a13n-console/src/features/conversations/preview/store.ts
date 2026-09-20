import type { Schema } from "../../../shared/api";
import { RunLog } from "./events";
import { applySimulation, deriveRun, refreshSessionPreview } from "./factories";
import type {
  LogEntry,
  PreviewRun,
  PreviewScenario,
  PreviewSession,
  PreviewThread,
} from "./model";
import { nextCursor } from "./model";
import type { Simulation } from "./simulate";

type Listener = () => void;

/**
 * The fake Service's memory. It owns resource versions, the appended Run stream
 * log, and the timers that deliver a simulated Run over time.
 */
export class PreviewStore {
  private readonly listeners = new Map<string, Set<Listener>>();
  private readonly timers = new Map<
    string,
    Set<ReturnType<typeof setTimeout>>
  >();
  private readonly steers = new Map<string, Schema["SteerStatus"]>();
  private sequence = 0;

  constructor(
    readonly scenario: PreviewScenario,
    /** Scales simulated delays; 2 runs a script twice as fast. */
    readonly speed = 1,
  ) {}

  /* Lookups ---------------------------------------------------------------- */

  get sessions(): PreviewSession[] {
    return this.scenario.sessions;
  }
  get threads(): PreviewThread[] {
    return this.sessions.flatMap((session) => session.threads);
  }
  get runs(): PreviewRun[] {
    return this.threads.flatMap((thread) => thread.runs);
  }
  session(id: string) {
    return this.sessions.find((entry) => entry.session.id === id);
  }
  thread(id: string) {
    return this.threads.find((entry) => entry.thread.id === id);
  }
  run(id: string) {
    return this.runs.find((entry) => entry.run.id === id);
  }
  threadOf(run: PreviewRun) {
    return this.threads.find((entry) => entry.runs.includes(run))!;
  }
  sessionOf(thread: PreviewThread) {
    return this.sessions.find((entry) => entry.threads.includes(thread))!;
  }
  steer(id: string) {
    return this.steers.get(id);
  }
  recordSteer(status: Schema["SteerStatus"]) {
    this.steers.set(status.steer_id, status);
  }

  /* Identity --------------------------------------------------------------- */

  id(prefix: string) {
    return `${prefix}_${(++this.sequence).toString(36).padStart(6, "0")}`;
  }
  now() {
    return new Date().toISOString();
  }

  /* Versions --------------------------------------------------------------- */

  touchRun(run: PreviewRun, changes: Partial<Schema["RunResource"]> = {}) {
    Object.assign(run.run, changes, {
      version: run.run.version + 1,
      updated_at: this.now(),
    });
    const thread = this.threadOf(run);
    if (thread) {
      thread.thread.updated_at = run.run.updated_at;
      this.sessionOf(thread).session.updated_at = run.run.updated_at;
      this.refreshPreview(thread);
    }
  }
  touchThread(
    thread: PreviewThread,
    changes: Partial<Schema["ThreadResource"]> = {},
  ) {
    Object.assign(thread.thread, changes, {
      version: thread.thread.version + 1,
      updated_at: this.now(),
    });
    this.refreshPreview(thread);
  }

  refreshPreview(thread: PreviewThread) {
    const session = this.sessionOf(thread);
    if (session) refreshSessionPreview(session, this.scenario.agents);
  }

  /* Run stream ------------------------------------------------------------- */

  subscribe(runId: string, listener: Listener) {
    const set = this.listeners.get(runId) ?? new Set<Listener>();
    set.add(listener);
    this.listeners.set(runId, set);
    return () => set.delete(listener);
  }
  private notify(runId: string) {
    for (const listener of this.listeners.get(runId) ?? []) listener();
  }

  /** Cursors are assigned on append so scripted and live frames stay ordered. */
  append(run: PreviewRun, entry: LogEntry) {
    entry.cursor = nextCursor(
      run.log.at(-1)?.cursor,
      entry.event.occurred_at as string,
    );
    run.log.push(entry);
    this.notify(run.run.id);
  }

  /** Apply the outcome a simulation reached to its Run resource. */
  settle(run: PreviewRun, simulation: Simulation) {
    applySimulation(run, simulation, this.now());
    const thread = this.threadOf(run);
    if (!thread) return;
    thread.thread.updated_at = run.run.updated_at;
    if (["completed", "waiting"].includes(simulation.status))
      thread.thread.head_run_id = run.run.id;
    this.sessionOf(thread).session.updated_at = run.run.updated_at;
    this.refreshPreview(thread);
  }

  /** Deliver a scripted Run over time; its outcome lands with its last frame. */
  play(run: PreviewRun, simulation: Simulation) {
    const entries = simulation.log.entries;
    run.script = [...entries];
    const timers = new Set<ReturnType<typeof setTimeout>>();
    this.timers.set(run.run.id, timers);
    for (const entry of entries) {
      const timer = setTimeout(
        () => {
          timers.delete(timer);
          run.script = run.script?.filter((pending) => pending !== entry);
          this.append(run, entry);
          if (entry.event.event_type === "run.running")
            this.touchRun(run, { status: "running" });
          if (!run.script?.length) this.settle(run, simulation);
        },
        Math.max(0, entry.offsetMs) / this.speed,
      );
      timers.add(timer);
    }
    if (!entries.length) this.settle(run, simulation);
  }

  /** Drop everything a Run has not delivered yet. */
  stop(run: PreviewRun) {
    for (const timer of this.timers.get(run.run.id) ?? []) clearTimeout(timer);
    this.timers.delete(run.run.id);
    run.script = [];
  }

  /**
   * Live additions to a Run in flight carry their own small log: the scripted
   * frames that have not been delivered must never arrive early.
   */
  appendLive(run: PreviewRun, write: (log: RunLog) => void) {
    const log = new RunLog({
      runId: run.run.id,
      threadId: run.run.thread_id,
      startedAt: this.now(),
      attemptId: run.attempts.at(-1)?.id ?? `att_${run.run.id}`,
      harnessRunId: run.attempts.at(-1)?.harness_run_id ?? `hrn_${run.run.id}`,
    });
    write(log);
    for (const entry of log.entries) this.append(run, entry);
    if (log.facts.length) {
      run.facts.push(...log.facts);
      deriveRun(run);
    }
    return log;
  }

  close() {
    for (const timers of this.timers.values())
      for (const timer of timers) clearTimeout(timer);
    this.timers.clear();
    this.listeners.clear();
  }
}
