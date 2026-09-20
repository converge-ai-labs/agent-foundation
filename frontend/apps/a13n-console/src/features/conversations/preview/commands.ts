import type { Schema } from "../../../shared/api";
import {
  newAsset,
  newRun,
  newSession,
  newThread,
  refreshSessionPreview,
} from "./factories";
import {
  body,
  conflict,
  fail,
  json,
  noContent,
  notFound,
  type Route,
} from "./http";
import type { PreviewRun, PreviewThread } from "./model";
import { chooseScript, simulate, type ScriptName } from "./simulate";
import type { PreviewStore } from "./store";

const post = (pattern: string, handle: Route["handle"]): Route => ({
  method: "POST",
  pattern,
  handle,
});

/** Every command the session page and its composer can send. */
export function runCommands(store: PreviewStore): Route[] {
  const workspace = store.scenario.workspace;
  return [
    post("/api/v1/auth/logout", () => noContent()),

    post("/api/v1/workspaces/{workspace}/assets", ({ url }) =>
      json(
        newAsset({
          id: store.id("ast"),
          filename: url.searchParams.get("filename") ?? "upload.bin",
          mediaType:
            url.searchParams.get("media_type") ?? "application/octet-stream",
          workspaceId: workspace.id,
          createdAt: store.now(),
        }),
        { status: 201 },
      ),
    ),

    /* Starting work -------------------------------------------------------- */

    post("/api/v1/workspaces/{workspace}/runs", async ({ request }) => {
      const payload = await body(request);
      const agentId = String(payload.agent_id ?? "");
      if (!store.scenario.agents.some((agent) => agent.id === agentId))
        return fail(422, "invalid_request", "Choose an agent that exists.");
      const session = {
        session: newSession({
          id: store.id("ses"),
          workspaceId: workspace.id,
          createdAt: store.now(),
          purpose: payload.session_purpose === "debug" ? "debug" : "execution",
        }),
        threads: [] as PreviewThread[],
      };
      const thread: PreviewThread = {
        thread: newThread({
          id: store.id("thr"),
          sessionId: session.session.id,
          createdAt: store.now(),
          purpose: session.session.purpose,
        }),
        runs: [],
        queue: [],
      };
      session.threads.push(thread);
      store.sessions.unshift(session);
      const run = begin(store, thread, payload.input as Schema["JsonValue"], {
        agentId,
        lineageKind: "root",
      });
      refreshSessionPreview(session, store.scenario.agents);
      return json(receipt(run, thread), { status: 202 });
    }),

    post(
      "/api/v1/runs/{source_run_id}/continue",
      async ({ params, request }) => {
        const source = store.run(params.source_run_id!);
        if (!source) return notFound("Run");
        const thread = store.threadOf(source);
        const payload = await body(request);
        const mismatch = expectVersion(
          payload.expected_thread_version,
          thread.thread.version,
          "Thread",
        );
        if (mismatch) return mismatch;
        const run = begin(store, thread, payload.input as Schema["JsonValue"], {
          agentId: source.run.agent_id,
          lineageKind: "continue",
          parentRunId: source.run.id,
        });
        return json(receipt(run, thread), { status: 202 });
      },
    ),

    post("/api/v1/runs/{run_id}/retry", async ({ params, request }) => {
      const source = store.run(params.run_id!);
      if (!source) return notFound("Run");
      const thread = store.threadOf(source);
      const mismatch = expectVersion(
        (await body(request)).expected_thread_version,
        thread.thread.version,
        "Thread",
      );
      if (mismatch) return mismatch;
      const run = begin(store, thread, source.run.input, {
        agentId: source.run.agent_id,
        lineageKind: source.run.lineage_kind,
        parentRunId: source.run.parent_run_id,
        retryOfRunId: source.run.id,
        script: "review",
      });
      return json(receipt(run, thread), { status: 202 });
    }),

    /* Answering a waiting Run --------------------------------------------- */

    post("/api/v1/runs/{run_id}/feedback", async ({ params, request }) => {
      const waiting = store.run(params.run_id!);
      if (!waiting) return notFound("Run");
      if (waiting.run.status !== "waiting")
        return conflict("This Run is not waiting for feedback.");
      const thread = store.threadOf(waiting);
      const payload = await body(request);
      const mismatch = expectVersion(
        payload.expected_thread_version,
        thread.thread.version,
        "Thread",
      );
      if (mismatch) return mismatch;
      if (
        payload.sealed_state_digest_sha256 !==
        waiting.run.sealed_state_digest_sha256
      )
        return conflict("The Run's sealed state moved on.");
      const approval = waiting.pending.some(
        (action) => action.kind === "approval",
      );
      waiting.pending = [];
      const run = begin(store, thread, waiting.run.input, {
        agentId: waiting.run.agent_id,
        lineageKind: "continue",
        parentRunId: waiting.run.id,
        script: approval ? "approved" : "answered",
        inputKind: "waiting_feedback",
        triggerType: "feedback",
      });
      return json(receipt(run, thread), { status: 202 });
    }),

    post("/api/v1/threads/{thread_id}/runs", async ({ params, request }) => {
      const thread = store.thread(params.thread_id!);
      if (!thread) return notFound("Thread");
      const payload = await body(request);
      const mismatch = expectVersion(
        payload.expected_thread_version,
        thread.thread.version,
        "Thread",
      );
      if (mismatch) return mismatch;
      const waiting = thread.runs.at(-1);
      // The preview implements the one submission the dock offers: resolving a
      // waiting head with defaults and continuing with a new message.
      if (!waiting || waiting.run.status !== "waiting")
        return conflict("This Thread has no waiting Run to resolve.");
      const resolution = payload.waiting_resolution as {
        sealed_state_digest_sha256?: unknown;
      } | null;
      if (
        !resolution ||
        resolution.sealed_state_digest_sha256 !==
          waiting.run.sealed_state_digest_sha256
      )
        return conflict("The Run's sealed state moved on.");
      waiting.pending = [];
      const run = begin(
        store,
        thread,
        {
          schema_version: "1",
          waiting_run_id: waiting.run.id,
          sealed_state_digest_sha256: waiting.run.sealed_state_digest_sha256,
          resolutions: [],
          input: payload.input as Schema["JsonValue"],
        },
        {
          agentId: waiting.run.agent_id,
          lineageKind: "continue",
          parentRunId: waiting.run.id,
          inputKind: "waiting_continue",
          triggerType: "user_input",
        },
      );
      const submission: Schema["ThreadRunSubmissionReceipt"] = {
        outcome: "run_accepted",
        queue_version: thread.thread.queue_version,
        run: receipt(run, thread),
      };
      return json(submission, { status: 202 });
    }),

    /* Acting on a Run in flight ------------------------------------------- */

    post("/api/v1/runs/{run_id}/steer", async ({ params, request }) => {
      const run = store.run(params.run_id!);
      if (!run) return notFound("Run");
      const payload = await body(request);
      const text = inputText(payload);
      const steerId = store.id("ste");
      const status: Schema["SteerStatus"] = {
        steer_id: steerId,
        schema_version: "1",
        accepted_against_run_id: run.run.id,
        consumed_by_run_id: null,
        consumed_checkpoint_seq: null,
        consumed_state_digest_sha256: null,
        created_at: store.now(),
        delivery_sequence: 1,
        finalized_at: null,
        session_id: run.run.session_id,
        source_waiting_run_id: null,
        status: "pending",
        target_run_id: run.run.id,
        thread_id: run.run.thread_id,
      };
      store.recordSteer(status);
      store.appendLive(run, (log) =>
        log.steering(store.id("itm"), text, store.id("enq")),
      );
      store.recordSteer({
        ...status,
        status: "consumed",
        consumed_by_run_id: run.run.id,
        consumed_checkpoint_seq: run.log.length,
        finalized_at: store.now(),
      });
      const value: Schema["SteerReceipt"] = {
        steer_id: steerId,
        schema_version: "1",
        accepted_at: status.created_at,
        delivery_sequence: 1,
        run_id: run.run.id,
        session_id: run.run.session_id,
        thread_id: run.run.thread_id,
      };
      return json(value, { status: 202 });
    }),

    post("/api/v1/runs/{run_id}/interrupt", async ({ params, request }) => {
      const run = store.run(params.run_id!);
      if (!run) return notFound("Run");
      const thread = store.threadOf(run);
      const payload = await body(request);
      const mismatch =
        expectVersion(
          payload.expected_thread_version,
          thread.thread.version,
          "Thread",
        ) ??
        expectVersion(payload.expected_run_version, run.run.version, "Run");
      if (mismatch) return mismatch;
      store.stop(run);
      const log = store.appendLive(run, (entry) => entry.cancelled());
      store.touchRun(run, {
        status: "cancelled",
        completed_at: log.facts.at(-1)?.occurredAt ?? store.now(),
        wait_reason: null,
      });
      run.pending = [];
      const value: Schema["InterruptReceipt"] = {
        run_id: run.run.id,
        schema_version: "1",
        status: "cancelled",
        interrupted_at: run.run.updated_at,
      };
      return json(value, { status: 202 });
    }),

    /* Queued submissions --------------------------------------------------- */

    post(
      "/api/v1/threads/{thread_id}/queued-submissions/reorder",
      async ({ params, request }) => {
        const thread = store.thread(params.thread_id!);
        if (!thread) return notFound("Thread");
        const payload = await body(request);
        const mismatch = expectVersion(
          payload.expected_queue_version,
          thread.thread.queue_version,
          "Queue",
        );
        if (mismatch) return mismatch;
        const order = Array.isArray(payload.queued_submission_ids)
          ? (payload.queued_submission_ids as string[])
          : [];
        thread.queue.sort(
          (a, b) =>
            order.indexOf(a.queued_submission_id) -
            order.indexOf(b.queued_submission_id),
        );
        thread.queue.forEach((entry, index) => (entry.position = index + 1));
        store.touchThread(thread, {
          queue_version: thread.thread.queue_version + 1,
        });
        return json({
          queue_version: thread.thread.queue_version,
          queued_submission: thread.queue[0]!,
        });
      },
    ),

    post(
      "/api/v1/threads/{thread_id}/queued-submissions/consume",
      async ({ params, request }) => {
        const thread = store.thread(params.thread_id!);
        if (!thread) return notFound("Thread");
        const payload = await body(request);
        const mismatch = expectVersion(
          payload.expected_queue_version,
          thread.thread.queue_version,
          "Queue",
        );
        if (mismatch) return mismatch;
        const next = thread.queue.find((entry) => entry.state === "queued");
        if (!next)
          return conflict("The queue has no submission left to consume.");
        const run = begin(store, thread, next.submission.input, {
          agentId:
            next.submission.agent_id ??
            thread.runs.at(-1)?.run.agent_id ??
            store.scenario.agents[0]!.id,
          lineageKind: "continue",
          parentRunId: thread.thread.head_run_id,
        });
        Object.assign(next, {
          state: "consumed",
          consumed_at: store.now(),
          consumed_run_id: run.run.id,
          version: next.version + 1,
        });
        store.touchThread(thread, {
          queue_version: thread.thread.queue_version + 1,
        });
        return json({
          outcome: "run_accepted",
          queue_version: thread.thread.queue_version,
          queued_submission: next,
          run: receipt(run, thread),
        });
      },
    ),

    {
      method: "DELETE",
      pattern: "/api/v1/queued-submissions/{queued_submission_id}",
      handle: ({ params }) => {
        const thread = store.threads.find((entry) =>
          entry.queue.some(
            (item) => item.queued_submission_id === params.queued_submission_id,
          ),
        );
        if (!thread) return notFound("Queued submission");
        thread.queue = thread.queue.filter(
          (item) => item.queued_submission_id !== params.queued_submission_id,
        );
        store.touchThread(thread, {
          queue_version: thread.thread.queue_version + 1,
        });
        return noContent();
      },
    },
  ];
}

/** Start one Run in a Thread and begin delivering its scripted observations. */
function begin(
  store: PreviewStore,
  thread: PreviewThread,
  input: Schema["JsonValue"],
  options: {
    agentId: string;
    lineageKind: Schema["RunLineageKind"];
    parentRunId?: string | null;
    retryOfRunId?: string | null;
    inputKind?: string;
    triggerType?: string;
    script?: ScriptName;
  },
): PreviewRun {
  // A composite waiting-continue value carries the message it was sent with.
  const accepted =
    input && typeof input === "object" && "input" in input
      ? (input as { input: Schema["JsonValue"] }).input
      : input;
  const text = inputText(accepted);
  const agent = store.scenario.agents.find(
    (entry) => entry.id === options.agentId,
  );
  const run = newRun({
    id: store.id("run"),
    sessionId: thread.thread.session_id,
    threadId: thread.thread.id,
    agentId: options.agentId,
    agentKey: agent?.key ?? "preview",
    createdAt: store.now(),
    input,
    inputText: text || null,
    inputKind: options.inputKind,
    triggerType: options.triggerType,
    lineageKind: options.lineageKind,
    parentRunId: options.parentRunId ?? null,
    retryOfRunId: options.retryOfRunId ?? null,
    environmentId: thread.thread.default_environment_id,
  });
  thread.runs.push(run);
  store.touchThread(thread, { current_run_id: run.run.id });
  store.play(
    run,
    simulate(
      options.script ?? chooseScript(text),
      {
        runId: run.run.id,
        threadId: run.run.thread_id,
        startedAt: run.run.created_at,
        attemptId: store.id("att"),
        harnessRunId: store.id("hrn"),
      },
      (prefix) => store.id(prefix),
    ),
  );
  return run;
}

function receipt(
  run: PreviewRun,
  thread: PreviewThread,
): Schema["RunAcceptanceReceipt"] {
  return {
    schema_version: "1",
    status: "accepted",
    run_id: run.run.id,
    run_version: run.run.version,
    session_id: run.run.session_id,
    thread_id: thread.thread.id,
    thread_version: thread.thread.version,
  };
}

function expectVersion(
  expected: unknown,
  actual: number,
  what: string,
): Response | null {
  if (typeof expected !== "number" || expected === actual) return null;
  return conflict(
    `${what} version ${String(expected)} is stale; it is now ${actual}.`,
  );
}

/** Read the text blocks of an AgentInput, whatever else it carries. */
function inputText(input: unknown): string {
  if (!input || typeof input !== "object") return "";
  const content = (input as { content?: unknown }).content;
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (block): block is { type: string; text: string } =>
        !!block &&
        typeof block === "object" &&
        (block as { type?: unknown }).type === "text",
    )
    .map((block) => block.text)
    .join("\n\n");
}
