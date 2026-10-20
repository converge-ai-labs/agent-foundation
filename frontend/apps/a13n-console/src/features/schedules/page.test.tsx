// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { fixtureThread } from "../conversations/transcript/fixture";
import { ScheduleDetail } from "./detail";
import { SchedulesPage } from "./list";

let client: Client, cache: QueryClient;
let viewer = "usr_one";
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  useAuth: () => ({ data: { user: { value: { id: viewer } } } }),
  revalidateSession: () => undefined,
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_one" },
    basePath: "/workspace/ws_one",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
  viewer = "usr_one";
  vi.restoreAllMocks();
});

const at = "2026-10-20T00:00:00Z";
const schedule: Schema["Schedule"] = {
  id: "sch_one",
  session_id: "ses_one",
  workspace_id: "ws_one",
  name: "Morning report",
  description: null,
  labels: {},
  state: "paused",
  pause_reason: "manual",
  schedule: { type: "cron", expression: "0 9 * * *", timezone: "UTC" },
  invocation: {
    agent_id: "agt_one",
    agent_revision_id: null,
    payload: { content: [{ type: "text", text: "Report" }] },
  },
  max_lateness_seconds: 300,
  version: 1,
  principal_id: "usr_one",
  created_by_id: "usr_one",
  updated_by_id: "usr_one",
  created_at: at,
  updated_at: at,
  archived_at: null,
  stream_url: "/api/v1/sessions/ses_one/stream",
};
const occurrence = (
  overrides: Partial<Schema["OccurrenceSummary"]> = {},
): Schema["OccurrenceSummary"] => ({
  id: "occ_one",
  schedule_id: "sch_one",
  session_id: "ses_one",
  trigger: "manual",
  scheduled_at: null,
  covered_from_at: null,
  schedule_version: 1,
  status: "failed",
  reason: "authority_invalid",
  thread_id: null,
  entry_id: null,
  dispatch_attempts: 1,
  next_attempt_at: null,
  deadline_at: at,
  last_error: null,
  version: 2,
  created_at: at,
  updated_at: at,
  finished_at: at,
  ...overrides,
});
const status = {
  schedule_id: "sch_one",
  session_id: "ses_one",
  observed_at: at,
  next_fire_at: null,
  pending_occurrence_id: null,
  last_occurrence_id: null,
  has_unfinished_work: false,
  busy_occurrences: { items: [], next_cursor: null },
};

function show(
  path: string,
  fetch: (request: Request) => Promise<Response> | Response,
) {
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "test-csrf" },
    maxReadRetries: 0,
    fetch: async (input, init) => fetch(new Request(input, init)),
  });
  cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 15_000 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/workspace/:workspaceId/schedules"
            element={<SchedulesPage />}
          />
          <Route
            path="/workspace/:workspaceId/schedules/:scheduleId"
            element={<ScheduleDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function live(
  request: Request,
  controllers: ReadableStreamDefaultController<Uint8Array>[],
) {
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controllers.push(controller);
        request.signal.addEventListener("abort", () => {
          try {
            controller.close();
          } catch {
            /* Already ended. */
          }
        });
      },
    }),
    { headers: { "Content-Type": "text/event-stream" } },
  );
}

it("previews explicit timing and retries an unchanged create with the same request key", async () => {
  const user = userEvent.setup(),
    creates: Request[] = [],
    bodies: unknown[] = [],
    previews: unknown[] = [];
  show("/workspace/ws_one/schedules", async (request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/v1/schedules" && request.method === "GET")
      return Response.json({ items: [], next_cursor: null });
    if (path === "/api/v1/agents")
      return Response.json({
        items: [{ id: "agt_one", name: "Reporter" }],
        next_cursor: null,
      });
    if (path.endsWith("/preview")) {
      previews.push(await request.json());
      return Response.json({
        items: [{ utc: at, local: "2026-10-20T08:00:00+08:00" }],
      });
    }
    if (path === "/api/v1/schedules") {
      creates.push(request);
      bodies.push(await request.json());
      if (creates.length === 1)
        return Response.json(
          { error: { code: "unavailable", message: "Temporary outage" } },
          { status: 503 },
        );
      return Response.json(schedule, { status: 201 });
    }
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    if (path.endsWith("/stream")) return live(request, []);
    return Response.json(schedule);
  });
  await user.click(
    await screen.findByRole("button", { name: "Create schedule" }),
  );
  const dialog = screen.getByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "Morning report");
  await user.click(within(dialog).getByRole("combobox", { name: "Agent" }));
  await user.click(await screen.findByRole("option", { name: "Reporter" }));
  await user.type(within(dialog).getByLabelText("Input"), "Report");
  await user.click(
    within(dialog).getByRole("button", { name: "Preview next times" }),
  );
  expect(await screen.findByText(/2026-10-20T08:00:00/)).toBeTruthy();
  expect(previews[0]).toMatchObject({
    schedule: { expression: "0 9 * * *" },
    count: 5,
  });
  await user.click(
    within(dialog).getByRole("button", {
      name: "Create schedule",
    }),
  );
  await screen.findByText("Temporary outage");
  await user.click(
    within(dialog).getByRole("button", {
      name: "Create schedule",
    }),
  );
  await screen.findByRole("heading", { name: "Morning report" });
  expect(creates).toHaveLength(2);
  expect(creates[0].headers.get("Idempotency-Key")).toBe(
    creates[1].headers.get("Idempotency-Key"),
  );
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]).toMatchObject({
    invocation: {
      agent_id: "agt_one",
      agent_revision_id: null,
      payload: { content: [{ type: "text", text: "Report" }] },
    },
  });
});

it("pages history, distinguishes failed input from submission, and refreshes an old result on reconnect", async () => {
  vi.spyOn(Math, "random").mockReturnValue(0);
  const controllers: ReadableStreamDefaultController<Uint8Array>[] = [],
    history: string[] = [];
  let completed = false;
  const user = userEvent.setup();
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const url = new URL(request.url),
      path = url.pathname;
    if (path.endsWith("/stream")) {
      expect(url.search).toBe("");
      return live(request, controllers);
    }
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences")) {
      expect(url.searchParams.get("include")).toBe("execution");
      const cursor = url.searchParams.get("cursor") ?? "";
      history.push(cursor);
      return Response.json({
        items: cursor
          ? [
              occurrence({
                id: "occ_old",
                status: "submitted",
                reason: null,
                thread_id: "thr_old",
                entry_id: "entry_old",
                execution: {
                  initial_entry_status: completed ? "consumed" : "assigned",
                  initial_run: {
                    id: "run_old",
                    status: completed ? "completed" : "running",
                  },
                  has_unfinished_work: !completed,
                  observed_at: completed ? "2026-10-20T00:01:00Z" : at,
                },
              }),
            ]
          : [
              occurrence(),
              occurrence({
                id: "occ_entry_failed",
                status: "submitted",
                reason: null,
                thread_id: "thr_failed",
                entry_id: "entry_failed",
                execution: {
                  initial_entry_status: "failed",
                  initial_run: null,
                  has_unfinished_work: false,
                  observed_at: at,
                },
              }),
            ],
        next_cursor: cursor ? null : "older",
      });
    }
    expect(path).toBe("/api/v1/schedules/sch_one");
    return Response.json(schedule);
  });
  expect(await screen.findByText("No input submitted")).toBeTruthy();
  expect(await screen.findByText("No run created")).toBeTruthy();
  expect(screen.getByText("state.submitted")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next" }));
  expect(await screen.findByText("state.running")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "View output" }).getAttribute("href"),
  ).toBe("/workspace/ws_one/sessions/ses_one/threads/thr_old");
  await act(async () => {
    completed = true;
    controllers[0].close();
  });
  expect(await screen.findByText("state.completed")).toBeTruthy();
  expect(await screen.findByText("state.consumed")).toBeTruthy();
  expect(controllers.length).toBeGreaterThan(1);
  await waitFor(() =>
    expect(
      history.filter((cursor) => cursor === "older").length,
    ).toBeGreaterThan(1),
  );
});

it("allows another writer to stop a schedule without offering creator-only actions", async () => {
  viewer = "usr_other";
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, []);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    return Response.json({ ...schedule, state: "enabled", pause_reason: null });
  });
  expect(await screen.findByRole("button", { name: "Pause" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Archive" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Run once" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit schedule" })).toBeNull();
});

it("preserves execution across occurrence frames and refreshes unchanged submission versions on thread changes", async () => {
  const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
  const submitted = occurrence({
    status: "submitted",
    reason: null,
    thread_id: "thr_one",
    entry_id: "entry_one",
  });
  let reads = 0,
    completed = false,
    hold = false;
  let resolveOld: ((response: Response) => void) | undefined;
  function page() {
    return {
      items: [
        {
          ...submitted,
          execution: {
            initial_entry_status: completed ? "consumed" : "assigned",
            initial_run: {
              id: "run_one",
              status: completed ? "completed" : "running",
            },
            has_unfinished_work: !completed,
            observed_at: completed ? "2026-10-20T00:01:00Z" : at,
          },
        },
      ],
      next_cursor: null,
    };
  }
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, controllers);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences")) {
      reads++;
      if (hold)
        return new Promise<Response>((resolve) => {
          resolveOld = resolve;
        });
      return Response.json(page());
    }
    expect(path).toBe("/api/v1/schedules/sch_one");
    return Response.json(schedule);
  });
  await screen.findByText("state.running");
  await waitFor(() => expect(reads).toBeGreaterThan(1));
  const oldPage = page();
  await act(async () => {
    hold = true;
    controllers[0].enqueue(
      new TextEncoder().encode(
        `event: occurrence\ndata: ${JSON.stringify({ session_id: "ses_one", schedule_id: "sch_one", occurrence: submitted })}\n\n`,
      ),
    );
  });
  await waitFor(() => expect(resolveOld).toBeDefined());
  expect(screen.getByText("state.running")).toBeTruthy();
  await act(async () => {
    completed = true;
    hold = false;
    controllers[0].enqueue(
      new TextEncoder().encode(
        `event: thread\ndata: ${JSON.stringify({ thread: fixtureThread({ id: "thr_one", session_id: "ses_one" }), current_run: null, last_run: null, entries: [] })}\n\n`,
      ),
    );
  });
  await screen.findByText("state.completed");
  await act(async () => {
    resolveOld?.(Response.json(oldPage));
  });
  expect(screen.queryByText("state.running")).toBeNull();
  expect(screen.getByText("state.completed")).toBeTruthy();
  expect(screen.getByText("state.submitted")).toBeTruthy();
});

it("keeps an open draft's original ETag after live configuration changes", async () => {
  const user = userEvent.setup(),
    controllers: ReadableStreamDefaultController<Uint8Array>[] = [],
    tags: string[] = [];
  let version = 1;
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, controllers);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    if (path === "/api/v1/agents")
      return Response.json({
        items: [{ id: "agt_one", name: "Reporter" }],
        next_cursor: null,
      });
    if (request.method === "PATCH") {
      tags.push(request.headers.get("If-Match") ?? "");
      return Response.json(
        { error: { code: "precondition_failed", message: "Stale version" } },
        { status: 412 },
      );
    }
    return Response.json({ ...schedule, version });
  });
  await user.click(
    await screen.findByRole("button", { name: "Edit schedule" }),
  );
  const dialog = screen.getByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), " edited");
  await act(async () => {
    version = 2;
    controllers[0].enqueue(
      new TextEncoder().encode(
        `event: schedule\ndata: ${JSON.stringify({ session_id: "ses_one", schedule_id: "sch_one", configuration: { id: "sch_one", version }, progress: {} })}\n\n`,
      ),
    );
  });
  await waitFor(() =>
    expect(
      cache.getQueryData<{ version: number }>([
        "schedules",
        "ws_one",
        "sch_one",
        "detail",
      ])?.version,
    ).toBe(2),
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save changes" }),
  );
  expect(await screen.findByText("Schedule changed")).toBeTruthy();
  expect(tags).toEqual(['"sch_one:1"']);
  expect(
    (within(dialog).getByLabelText("Name") as HTMLInputElement).value,
  ).toBe("Morning report edited");
});

it("does not replace a newer streamed occurrence with an older in-flight history read", async () => {
  const controllers: ReadableStreamDefaultController<Uint8Array>[] = [];
  let block = false,
    reads = 0,
    resolveOld: ((response: Response) => void) | undefined;
  const pending = occurrence({
    status: "pending",
    reason: null,
    finished_at: null,
    version: 1,
  });
  const failed = occurrence();
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, controllers);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences")) {
      reads++;
      if (block)
        return new Promise<Response>((resolve) => {
          resolveOld = resolve;
        });
      return Response.json({ items: [pending], next_cursor: null });
    }
    return Response.json(schedule);
  });
  await screen.findByText("state.pending");
  // Let the initial connection's batched refresh finish before holding the next request.
  await waitFor(() => expect(reads).toBeGreaterThan(1));
  await act(async () => {
    block = true;
    void cache.invalidateQueries({
      queryKey: ["schedules", "ws_one", "sch_one", "history"],
    });
  });
  await waitFor(() => expect(resolveOld).toBeDefined());
  await act(async () => {
    controllers[0].enqueue(
      new TextEncoder().encode(
        `event: occurrence\ndata: ${JSON.stringify({ session_id: "ses_one", schedule_id: "sch_one", occurrence: failed })}\n\n`,
      ),
    );
  });
  await screen.findByText("state.failed");
  await act(async () => {
    resolveOld?.(Response.json({ items: [pending], next_cursor: null }));
  });
  expect(screen.queryByText("state.pending")).toBeNull();
  expect(screen.getByText("state.failed")).toBeTruthy();
});
