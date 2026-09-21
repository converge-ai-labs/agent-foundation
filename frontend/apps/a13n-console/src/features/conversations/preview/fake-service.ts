import type { Schema } from "../../../shared/api";
import { compareCursors } from "../projection";
import { runCommands } from "./commands";
import {
  fail,
  integer,
  json,
  matchPath,
  notFound,
  page,
  type Route,
} from "./http";
import type { PreviewScenario } from "./model";
import { finalizeItems, retainedItems } from "./retained";
import { previewScenario } from "./scenario";
import { PreviewStore } from "./store";
import { runStreamResponse, TERMINAL_EVENTS } from "./stream";

export interface FakeService {
  /** Drop-in replacement for `fetch` in `createClient({ fetch })`. */
  fetch: typeof globalThis.fetch;
  store: PreviewStore;
  /** Paths the preview deliberately does not implement, for diagnosis. */
  readonly unhandled: string[];
  close(): void;
}

/** A whole Service, in memory, for one scenario. */
export function createFakeService(
  scenario: PreviewScenario = previewScenario(),
  options: { speed?: number } = {},
): FakeService {
  const store = new PreviewStore(scenario, options.speed ?? 1);
  const routes: Route[] = [...reads(store), ...runCommands(store)];
  const unhandled: string[] = [];

  const fetcher: typeof globalThis.fetch = async (input, init) => {
    const request = new Request(input as RequestInfo, init);
    const url = new URL(request.url);
    for (const route of routes) {
      if (route.method !== request.method) continue;
      const params = matchPath(route.pattern, url.pathname);
      if (!params) continue;
      return route.handle({ params, url, request });
    }
    const target = `${request.method} ${url.pathname}`;
    if (!unhandled.includes(target)) unhandled.push(target);
    return fail(
      404,
      "route_not_implemented",
      `The preview Service does not implement ${target}.`,
    );
  };
  return { fetch: fetcher, store, unhandled, close: () => store.close() };
}

function reads(store: PreviewStore): Route[] {
  const { scenario } = store;
  const get = (pattern: string, handle: Route["handle"]): Route => ({
    method: "GET",
    pattern,
    handle,
  });
  return [
    /* Identity and tenancy ------------------------------------------------ */
    get("/api/v1/users/me", () => json(scenario.user)),
    get("/api/v1/auth/csrf", () => json({ csrf_token: "preview-csrf-token" })),
    get("/api/v1/auth/context", () =>
      json({
        organization_id: scenario.organization.id,
        workspace_id: scenario.workspace.id,
      }),
    ),
    get("/api/v1/organizations", () => page([scenario.organization])),
    get("/api/v1/organizations/{organization}/workspaces", () =>
      page([scenario.workspace]),
    ),
    get("/api/v1/organizations/{organization}/permissions", () =>
      json({ organization_admin: false }),
    ),
    get("/api/v1/workspaces/{workspace}/permissions", () =>
      json({ actions: scenario.permissions, organization_admin: false }),
    ),

    /* Catalogs the shell and composer read ------------------------------- */
    get("/api/v1/memory-provider-types", () => json({ items: [] })),
    get("/api/v1/workspaces/{workspace}/memory-providers", () => page([])),
    get("/api/v1/workspaces/{workspace}/models", () => page([])),
    get("/api/v1/workspaces/{workspace}/skills", () => page([])),
    get("/api/v1/workspaces/{workspace}/environment-templates", () => page([])),
    get("/api/v1/workspaces/{workspace}/environments", () =>
      page(scenario.environments),
    ),
    get("/api/v1/environments/{resource_id}", ({ params }) => {
      const environment = scenario.environments.find(
        (entry) => entry.id === params.resource_id,
      );
      return environment ? json(environment) : notFound("Environment");
    }),
    get("/api/v1/workspaces/{workspace}/agents", () => page(scenario.agents)),
    get("/api/v1/workspaces/{workspace}/agents/{agent}", ({ params }) => {
      const agent = scenario.agents.find(
        (entry) => entry.id === params.agent || entry.key === params.agent,
      );
      return agent ? json(agent) : notFound("Agent");
    }),
    get("/api/v1/assets/{asset_id}", ({ params }) => {
      const asset = scenario.assets.find(
        (entry) => entry.id === params.asset_id,
      );
      return asset ? json(asset) : notFound("Asset");
    }),
    get("/api/v1/assets/{asset_id}/content", ({ params }) => {
      const asset = scenario.assets.find(
        (entry) => entry.id === params.asset_id,
      );
      return asset
        ? new Response(`Preview content for ${asset.filename}.\n`, {
            headers: { "Content-Type": asset.media_type },
          })
        : notFound("Asset");
    }),

    /* Conversations ------------------------------------------------------- */
    get("/api/v1/workspaces/{workspace}/sessions", ({ url }) =>
      json(sessionPage(store, url)),
    ),
    get("/api/v1/sessions/{session_id}/threads", ({ params }) => {
      const session = store.session(params.session_id!);
      return session
        ? page(session.threads.map((entry) => entry.thread))
        : notFound("Session");
    }),
    get("/api/v1/threads/{thread_id}", ({ params }) => {
      const thread = store.thread(params.thread_id!);
      return thread ? json(thread.thread) : notFound("Thread");
    }),
    get("/api/v1/threads/{thread_id}/runs", ({ params }) => {
      const thread = store.thread(params.thread_id!);
      return thread
        ? page(thread.runs.map((entry) => entry.run))
        : notFound("Thread");
    }),
    get("/api/v1/threads/{thread_id}/queued-submissions", ({ params, url }) => {
      const thread = store.thread(params.thread_id!);
      if (!thread) return notFound("Thread");
      const state = url.searchParams.get("state") ?? "queued";
      return json({
        items: thread.queue.filter((entry) => entry.state === state),
      });
    }),
    get("/api/v1/runs/{run_id}", ({ params }) => {
      const run = store.run(params.run_id!);
      return run ? json(run.run) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/items", ({ params, url }) => {
      const run = store.run(params.run_id!);
      return run ? json(itemPage(store, params.run_id!, url)) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/pending-actions", ({ params }) => {
      const run = store.run(params.run_id!);
      return run ? json({ items: run.pending }) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/attempts", ({ params }) => {
      const run = store.run(params.run_id!);
      return run ? page(run.attempts) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/environment-mounts", ({ params }) => {
      const run = store.run(params.run_id!);
      return run ? page(run.mounts) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/lineage", ({ params }) => {
      const run = store.run(params.run_id!);
      return run ? json(lineage(store, params.run_id!)) : notFound("Run");
    }),
    get("/api/v1/runs/{run_id}/events", ({ params, url }) => {
      const run = store.run(params.run_id!);
      if (!run) return notFound("Run");
      const after = integer(url.searchParams.get("after_resource_seq"), 0);
      const limit = integer(url.searchParams.get("limit"), 50);
      const items = run.lifecycle
        .filter((event) => event.resource_seq > after)
        .slice(0, limit);
      return json({
        items,
        resource_id: run.run.id,
        resource_type: "run",
        next_resource_seq: items.at(-1)?.resource_seq ?? after,
        high_watermark_resource_seq: run.lifecycle.at(-1)?.resource_seq ?? 0,
        retained_resource_seq_floor: 1,
      });
    }),
    get("/api/v1/runs/{run_id}/stream", ({ params, request }) => {
      const run = store.run(params.run_id!);
      if (!run) return notFound("Run");
      return runStreamResponse(
        store,
        run,
        request.headers.get("Last-Event-ID"),
        request.signal,
      );
    }),
    get("/api/v1/runs/{run_id}/steers/{steer_id}", ({ params }) => {
      const status = store.steer(params.steer_id!);
      return status ? json(status) : notFound("Steer");
    }),

    /* Traces --------------------------------------------------------------- */
    get("/api/v1/workspaces/{workspace}/trace-query", () =>
      json({
        enabled: true,
        provider: "preview",
        // A week before the oldest Session, so the default day window is valid.
        history_from: new Date(
          Math.min(
            ...scenario.sessions.map((entry) =>
              Date.parse(entry.session.created_at),
            ),
          ) -
            7 * 86_400_000,
        ).toISOString(),
        search_in: ["input_output"],
      }),
    ),
    get("/api/v1/workspaces/{workspace}/traces", ({ url }) => {
      const query = url.searchParams;
      const matches = (key: string, value: string) => {
        const wanted = query.get(key);
        return !wanted || wanted === value;
      };
      const items = scenario.traces
        .filter(
          (entry) =>
            matches("run_id", entry.trace.correlation.run_id) &&
            matches("session_id", entry.trace.correlation.session_id) &&
            matches("thread_id", entry.trace.correlation.thread_id),
        )
        .map((entry) => entry.trace);
      return page(items);
    }),
    get("/api/v1/workspaces/{workspace}/traces/{trace_id}", ({ params }) => {
      const trace = scenario.traces.find(
        (entry) => entry.trace.id === params.trace_id,
      );
      return trace ? json(trace.trace) : notFound("Trace");
    }),
    get(
      "/api/v1/workspaces/{workspace}/traces/{trace_id}/observations",
      ({ params }) => {
        const trace = scenario.traces.find(
          (entry) => entry.trace.id === params.trace_id,
        );
        return trace ? page(trace.observations) : notFound("Trace");
      },
    ),
  ];
}

/* Read projections --------------------------------------------------------- */

function sessionPage(
  store: PreviewStore,
  url: URL,
): Schema["SessionCollection"] {
  const query = url.searchParams;
  const text = (query.get("q") ?? "").trim().toLowerCase();
  const statuses = query.getAll("status");
  const triggers = query.getAll("trigger_type");
  const agentId = query.get("agent_id");
  const items = store.sessions
    .map((entry) => entry.session)
    .filter((session) => {
      const preview = session.preview;
      if (statuses.length && !statuses.includes(preview?.run_status ?? ""))
        return false;
      if (triggers.length && !triggers.includes(preview?.trigger_type ?? ""))
        return false;
      if (agentId) {
        const threads = store.session(session.id)?.threads ?? [];
        if (
          !threads.some((thread) =>
            thread.runs.some((run) => run.run.agent_id === agentId),
          )
        )
          return false;
      }
      if (!text) return true;
      return [
        session.id,
        preview?.thread_id,
        preview?.run_id,
        preview?.input_text,
        preview?.agent_name,
      ].some((value) => value?.toLowerCase().includes(text));
    })
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return { items, next_cursor: null };
}

function itemPage(
  store: PreviewStore,
  runId: string,
  url: URL,
): Schema["ItemCollection"] {
  const run = store.run(runId)!;
  const finalized =
    !run.script?.length &&
    run.log.some((entry) => TERMINAL_EVENTS.includes(entry.event.event_type));
  const projected = retainedItems(run.log);
  const all = (finalized ? finalizeItems(projected) : projected).sort((a, b) =>
    compareCursors(a.first_stream_id, b.first_stream_id),
  );
  const limit = Math.max(1, integer(url.searchParams.get("limit"), 50));
  const ordered =
    url.searchParams.get("order") === "asc" ? all : [...all].reverse();
  const cursor = url.searchParams.get("cursor");
  const start = cursor
    ? Math.max(
        0,
        ordered.findIndex((item) => item.first_stream_id === cursor) + 1,
      )
    : 0;
  const window = ordered.slice(start, start + limit);
  const more = start + limit < ordered.length;
  return {
    items: window,
    next_cursor: more ? (window.at(-1)?.first_stream_id ?? null) : null,
    snapshot_version: run.run.version,
    projection_cursor: run.log.at(-1)?.cursor ?? null,
    complete: true,
    incomplete_reason: null,
    finalized,
  };
}

function lineage(store: PreviewStore, runId: string): Schema["RunLineage"] {
  const run = store.run(runId)!;
  const thread = store.threadOf(run);
  const chronological = [...thread.runs].sort((a, b) =>
    a.run.created_at.localeCompare(b.run.created_at),
  );
  const index = chronological.findIndex((entry) => entry.run.id === runId);
  const ancestry = chronological.slice(0, index + 1);
  return {
    head_run_id: runId,
    items: ancestry.map((entry, offset) => ({
      run_id: entry.run.id,
      session_id: entry.run.session_id,
      thread_id: entry.run.thread_id,
      parent_run_id: entry.run.parent_run_id,
      status: entry.run.status,
      lineage_kind: entry.run.lineage_kind,
      created_at: entry.run.created_at,
      depth_from_head: ancestry.length - 1 - offset,
    })),
  };
}
