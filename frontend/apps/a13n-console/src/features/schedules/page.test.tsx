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
import { ScheduleDetail } from "./detail";
import { SchedulesPage } from "./list";

let client: Client, cache: QueryClient;
let viewer = "usr_one";
let permissions = ["read", "write", "run", "admin"];
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  useAuth: () => ({ data: { user: { value: { id: viewer } } } }),
  revalidateSession: () => undefined,
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_one" },
    basePath: "/workspace/ws_one",
    can: (verb: string) => permissions.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values: Record<string, unknown> = {}) =>
      key.replace(/{{(\w+)}}/g, (match, name: string) =>
        String(values[name] ?? match),
      ),
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
  viewer = "usr_one";
  permissions = ["read", "write", "run", "admin"];
  vi.restoreAllMocks();
});

const at = "2026-10-20T00:00:00Z";
const schedule: Schema["Schedule"] = {
  target: { mode: "new_thread", session_id: "ses_one", thread_id: null },
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
    agent_id: "agt_0123456789abcdef0123",
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
    fetch: async (input, init) => {
      const request = new Request(input, init);
      if (
        new URL(request.url).pathname ===
        `/api/v1/agents/${schedule.invocation.agent_id}`
      )
        return Response.json({
          id: schedule.invocation.agent_id,
          name: "Reporter",
        });
      return fetch(request);
    },
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

it.each(["cron", "once"])(
  "previews %s timing and retries an unchanged create with the same request key",
  async (type) => {
    let expectedRunAt: string | undefined;
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
          items: [{ id: "agt_0123456789abcdef0123", name: "Reporter" }],
          next_cursor: null,
        });
      if (path.endsWith("/revisions"))
        return Response.json({ items: [], next_cursor: null });
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
    if (type === "once") {
      await user.click(
        within(dialog).getByRole("button", { name: "One time" }),
      );
      await user.click(within(dialog).getByRole("button", { name: "Run at" }));
      const picker = within(screen.getByRole("dialog", { name: "Run at" }));
      expect(
        picker.getByRole("button", { name: "Apply" }).hasAttribute("disabled"),
      ).toBe(true);
      const futureYear = new Date().getFullYear() + 1;
      await user.selectOptions(
        picker.getByRole("combobox", { name: "Choose the Year" }),
        String(futureYear),
      );
      await user.selectOptions(
        picker.getByRole("combobox", { name: "Choose the Month" }),
        "1",
      );
      await user.click(picker.getByRole("button", { name: /February 10/ }));
      for (const [label, value] of [
        ["Hour", "09"],
        ["Minute", "30"],
        ["Second", "15"],
      ]) {
        await user.click(picker.getByRole("combobox", { name: label }));
        await user.click(await screen.findByRole("option", { name: value }));
      }
      await user.click(picker.getByRole("button", { name: "Apply" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "Run at" })).toBeNull(),
      );
      expectedRunAt = new Date(futureYear, 1, 10, 9, 30, 15).toISOString();
    }
    await user.click(within(dialog).getByRole("combobox", { name: "Agent" }));
    await user.click(await screen.findByRole("option", { name: "Reporter" }));
    await user.type(within(dialog).getByLabelText("Input"), "Report");
    await user.click(
      within(dialog).getByRole("button", { name: "Preview next times" }),
    );
    expect(await screen.findByLabelText("Next scheduled times")).toBeTruthy();
    expect(previews[0]).toMatchObject({
      schedule:
        type === "once"
          ? {
              type: "once",
              run_at: expectedRunAt,
            }
          : { expression: "0 9 * * *" },
      count: 5,
    });
    if (type === "once") {
      const clock = vi
        .spyOn(Date, "now")
        .mockReturnValue(new Date(expectedRunAt!).getTime());
      await user.click(
        within(dialog).getByRole("button", { name: "Create schedule" }),
      );
      expect(
        await screen.findByText(
          "This time has already passed. Choose a future date and time.",
        ),
      ).toBeTruthy();
      expect(creates).toHaveLength(0);
      clock.mockRestore();
    }
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
    expect((bodies[0] as Schema["ScheduleCreate"]).schedule).toEqual(
      (previews[0] as Schema["SchedulePreviewRequest"]).schedule,
    );
    expect(bodies[0]).toMatchObject({
      invocation: {
        agent_id: "agt_0123456789abcdef0123",
        agent_revision_id: null,
        payload: { content: [{ type: "text", text: "Report" }] },
      },
    });
  },
  15_000,
);

it("explains an empty one-time preview and clears the message when timing changes", async () => {
  const user = userEvent.setup();
  const past = "2000-01-10T09:30:15Z";
  show("/workspace/ws_one/schedules/sch_one", async (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, []);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences") || path === "/api/v1/agents")
      return Response.json({ items: [], next_cursor: null });
    if (path.endsWith("/preview")) {
      const body = (await request.json()) as Schema["SchedulePreviewRequest"];
      if (body.schedule.type === "once") {
        expect(body.schedule.run_at).toBe(past);
        return Response.json({ items: [] });
      }
      return Response.json({ items: [{ utc: at, local: at }] });
    }
    return Response.json({
      ...schedule,
      schedule: { type: "once", run_at: past },
    });
  });
  await user.click(
    await screen.findByRole("button", { name: "Edit schedule" }),
  );
  await user.click(screen.getByRole("button", { name: "Preview next times" }));
  const message =
    "This time has already passed. Choose a future date and time.";
  expect(await screen.findByText(message)).toBeTruthy();
  expect(
    screen.queryByRole("list", { name: "Next scheduled times" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Recurring" }));
  expect(screen.queryByText(message)).toBeNull();
  await user.click(screen.getByRole("button", { name: "Preview next times" }));
  expect(
    await screen.findByRole("list", { name: "Next scheduled times" }),
  ).toBeTruthy();
});

async function openCreateDraft(
  respond?: (request: Request) => Response | undefined,
) {
  const user = userEvent.setup();
  const writes: Request[] = [];
  show("/workspace/ws_one/schedules", (request) => {
    if (request.method !== "GET") writes.push(request);
    const response = respond?.(request);
    if (response) return response;
    if (new URL(request.url).pathname === "/api/v1/agents")
      return Response.json({
        items: [{ id: "agt_0123456789abcdef0123", name: "Reporter" }],
        next_cursor: null,
      });
    return Response.json({ items: [], next_cursor: null });
  });
  await user.click(
    await screen.findByRole("button", { name: "Create schedule" }),
  );
  return { user, dialog: within(screen.getByRole("dialog")), writes };
}

it("returns unchanged and incomplete JSON drafts to fields without submitting", async () => {
  const { user, dialog, writes } = await openCreateDraft();
  await user.type(dialog.getByLabelText("Input"), " unfinished ");
  await user.click(dialog.getByRole("button", { name: "JSON" }));
  await user.click(dialog.getByRole("button", { name: "Fields" }));
  expect((dialog.getByLabelText("Input") as HTMLTextAreaElement).value).toBe(
    " unfinished ",
  );
  expect(dialog.queryByRole("alert")).toBeNull();

  await user.click(dialog.getByRole("button", { name: "JSON" }));
  await user.clear(dialog.getByLabelText("Invocation JSON"));
  await user.paste(
    JSON.stringify({
      agent_id: "",
      payload: { content: [{ type: "text", text: "Unfinished report" }] },
    }),
  );
  await user.click(dialog.getByRole("button", { name: "Fields" }));
  expect((dialog.getByLabelText("Input") as HTMLTextAreaElement).value).toBe(
    "Unfinished report",
  );
  expect(dialog.queryByRole("alert")).toBeNull();
  expect(
    (
      dialog.getByRole("button", {
        name: "Create schedule",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(writes).toHaveLength(0);
});

it("restores the latest form draft after malformed or structured JSON without losing other edits", async () => {
  const { user, dialog, writes } = await openCreateDraft();
  await user.type(dialog.getByLabelText("Name"), "Local draft");
  await user.type(dialog.getByLabelText("Input"), "Original input");
  await user.click(dialog.getByRole("button", { name: "JSON" }));
  await user.clear(dialog.getByLabelText("Invocation JSON"));
  await user.paste(
    JSON.stringify({
      agent_id: "",
      agent_revision_id: "revision draft",
      memories: [],
      payload: { content: [{ type: "text", text: "Updated input" }] },
    }),
  );
  await user.click(dialog.getByRole("button", { name: "Fields" }));
  expect((dialog.getByLabelText("Input") as HTMLTextAreaElement).value).toBe(
    "Updated input",
  );

  for (const broken of [
    "{",
    JSON.stringify({
      payload: { content: [{ type: "json", value: { keep: true } }] },
    }),
  ]) {
    await user.click(dialog.getByRole("button", { name: "JSON" }));
    const snapshot = (
      dialog.getByLabelText("Invocation JSON") as HTMLTextAreaElement
    ).value;
    expect(JSON.parse(snapshot).memories).toEqual([]);
    await user.clear(dialog.getByLabelText("Invocation JSON"));
    await user.paste(broken);
    expect(
      dialog.getByLabelText("Invocation JSON").getAttribute("aria-invalid"),
    ).toBe("true");
    expect(dialog.queryByRole("alert")).toBeNull();
    expect(
      dialog.queryByRole("button", {
        name: "Discard JSON changes and return to fields",
      }),
    ).toBeNull();
    await user.clear(dialog.getByLabelText("Name"));
    await user.type(dialog.getByLabelText("Name"), "Other edits kept");
    await user.click(dialog.getByRole("button", { name: "Fields" }));
    const recovery = within(
      screen.getByRole("dialog", { name: "Cannot switch to fields" }),
    );
    expect(
      (dialog.getByLabelText("Invocation JSON") as HTMLTextAreaElement).value,
    ).toBe(broken);
    await user.click(
      recovery.getByRole("button", {
        name: "Discard JSON changes and return to fields",
      }),
    );
    expect(dialog.queryByRole("alert")).toBeNull();
    expect((dialog.getByLabelText("Input") as HTMLTextAreaElement).value).toBe(
      "Updated input",
    );
    expect(
      dialog.getByRole("combobox", { name: "Agent revision (optional)" })
        .textContent,
    ).toContain("revision draft");
    expect((dialog.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Other edits kept",
    );
    await user.click(dialog.getByRole("button", { name: "JSON" }));
    expect(
      (dialog.getByLabelText("Invocation JSON") as HTMLTextAreaElement).value,
    ).toBe(snapshot);
    await user.click(dialog.getByRole("button", { name: "Fields" }));
  }
  expect(writes).toHaveLength(0);
});

it.each(["outside", "escape"])(
  "dismisses only the nested error dialog with %s and keeps the draft",
  async (dismiss) => {
    const { user, dialog, writes } = await openCreateDraft();
    await user.type(dialog.getByLabelText("Name"), "Retained draft");
    await user.click(dialog.getByRole("button", { name: "JSON" }));
    await user.clear(dialog.getByLabelText("Invocation JSON"));
    await user.paste("{");
    await user.click(dialog.getByRole("button", { name: "Fields" }));
    const errorDialog = screen.getByRole("dialog", {
      name: "Cannot switch to fields",
    });
    if (dismiss === "outside") {
      await user.click(errorDialog.closest('[data-slot="dialog-viewport"]')!);
    } else {
      await user.keyboard("{Escape}");
    }
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "Cannot switch to fields" }),
      ).toBeNull(),
    );
    expect(
      screen.getByRole("dialog", { name: "Create schedule" }),
    ).toBeTruthy();
    expect((dialog.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Retained draft",
    );
    const json = dialog.getByLabelText(
      "Invocation JSON",
    ) as HTMLTextAreaElement;
    expect(json.value).toBe("{");
    await user.click(json);
    await user.clear(json);
    await user.paste("{}");
    expect(json.value).toBe("{}");
    expect(writes).toHaveLength(0);
  },
);

it("lets users correct JSON after a failed switch and still rejects invalid submissions", async () => {
  const { user, dialog, writes } = await openCreateDraft();
  await user.type(dialog.getByLabelText("Name"), "Draft");
  await user.click(dialog.getByRole("button", { name: "JSON" }));
  await user.clear(dialog.getByLabelText("Invocation JSON"));
  await user.paste("{");
  await user.click(dialog.getByRole("button", { name: "Fields" }));
  const recovery = within(
    screen.getByRole("dialog", { name: "Cannot switch to fields" }),
  );
  expect(recovery.getByText("Expected a closing brace.")).toBeTruthy();
  expect(recovery.getByText("Line 1, column 2")).toBeTruthy();
  await user.click(recovery.getByRole("button", { name: "Continue editing" }));
  await waitFor(() =>
    expect(document.activeElement).toBe(
      dialog.getByLabelText("Invocation JSON"),
    ),
  );
  expect(
    (dialog.getByLabelText("Invocation JSON") as HTMLTextAreaElement).value,
  ).toBe("{");
  await user.clear(dialog.getByLabelText("Invocation JSON"));
  await user.paste(
    JSON.stringify({
      agent_id: "agt_0123456789abcdef0123",
      agent_revision_id: "unfinished",
      payload: { content: [{ type: "text", text: "Report" }] },
    }),
  );
  expect(dialog.queryByRole("alert")).toBeNull();
  expect(
    dialog.getByLabelText("Invocation JSON").getAttribute("aria-invalid"),
  ).not.toBe("true");
  await user.click(dialog.getByRole("button", { name: "Fields" }));
  expect((dialog.getByLabelText("Input") as HTMLTextAreaElement).value).toBe(
    "Report",
  );
  await user.click(dialog.getByRole("button", { name: "Create schedule" }));
  expect(await dialog.findByRole("alert")).toBeTruthy();
  expect(writes).toHaveLength(0);
});

it.each(["pinned", "default"])(
  "selects a revision and submits the %s choice",
  async (choice) => {
    const revisionId = "apr_0123456789abcdef0123";
    const { user, dialog, writes } = await openCreateDraft((request) => {
      if (new URL(request.url).pathname.endsWith("/revisions"))
        return Response.json({
          items: [{ id: revisionId, number: 1, note: "Stable" }],
          next_cursor: null,
        });
      if (request.method === "POST")
        return Response.json(
          { error: { code: "unavailable", message: "Temporary outage" } },
          { status: 503 },
        );
    });
    const revision = dialog.getByRole("combobox", {
      name: "Agent revision (optional)",
    });
    expect(revision.textContent).toContain("Default");
    expect((revision as HTMLButtonElement).disabled).toBe(true);
    await user.click(dialog.getByRole("combobox", { name: "Agent" }));
    await user.click(await screen.findByRole("option", { name: "Reporter" }));
    await user.click(revision);
    await user.click(await screen.findByRole("option", { name: "v1 Stable" }));
    if (choice === "default") {
      await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
      await user.click(
        dialog.getByRole("combobox", { name: "Agent revision (optional)" }),
      );
      await user.click(await screen.findByRole("option", { name: "Default" }));
    }
    const expected = choice === "default" ? null : revisionId;
    await user.click(dialog.getByRole("button", { name: "JSON" }));
    expect(
      JSON.parse(
        (dialog.getByLabelText("Invocation JSON") as HTMLTextAreaElement).value,
      ).agent_revision_id,
    ).toBe(expected);
    await user.click(dialog.getByRole("button", { name: "Fields" }));
    expect(
      dialog.getByRole("combobox", { name: "Agent revision (optional)" })
        .textContent,
    ).toContain(choice === "default" ? "Default" : "v1");
    await user.type(dialog.getByLabelText("Name"), "Report");
    await user.type(dialog.getByLabelText("Input"), "Hello");
    await user.click(dialog.getByRole("button", { name: "Create schedule" }));
    await dialog.findByText("Temporary outage");
    expect(writes).toHaveLength(1);
    expect(await writes[0].json()).toMatchObject({
      invocation: { agent_revision_id: expected },
    });
  },
);

it("loads all revision pages and resets the pinned version when the agent changes", async () => {
  const first = "agt_0123456789abcdef0123",
    second = "agt_1123456789abcdef0123";
  const reads: string[] = [];
  const { user, dialog, writes } = await openCreateDraft((request) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/v1/agents")
      return Response.json({
        items: [
          { id: first, name: "Reporter" },
          { id: second, name: "Reviewer" },
        ],
        next_cursor: null,
      });
    if (url.pathname.endsWith("/revisions")) {
      reads.push(url.pathname + url.search);
      if (url.pathname.includes(second))
        return Response.json({
          items: [
            { id: "apr_2123456789abcdef0123", number: 1, note: "Review" },
          ],
          next_cursor: null,
        });
      const laterPage = url.searchParams.has("cursor");
      return Response.json({
        items: [
          {
            id: laterPage
              ? "apr_1123456789abcdef0123"
              : "apr_0123456789abcdef0123",
            number: laterPage ? 1 : 2,
            note: laterPage ? "Earlier" : "Latest",
          },
        ],
        next_cursor: laterPage ? null : "older",
      });
    }
  });
  await user.click(dialog.getByRole("combobox", { name: "Agent" }));
  await user.click(await screen.findByRole("option", { name: "Reporter" }));
  await user.click(
    dialog.getByRole("combobox", { name: "Agent revision (optional)" }),
  );
  await user.click(await screen.findByRole("option", { name: "v1 Earlier" }));
  expect(reads.some((url) => url.includes("cursor=older"))).toBe(true);
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  await user.click(dialog.getByRole("combobox", { name: "Agent" }));
  await user.click(await screen.findByRole("option", { name: "Reviewer" }));
  const revision = dialog.getByRole("combobox", {
    name: "Agent revision (optional)",
  });
  expect(revision.textContent).toContain("Default");
  await user.click(revision);
  expect(await screen.findByRole("option", { name: "v1 Review" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "v1 Earlier" })).toBeNull();
  expect(writes).toHaveLength(0);
});

it.each([false, true])(
  "previews timing and retries creation with the same request key (enable immediately: %s)",
  async (enableImmediately) => {
    const user = userEvent.setup(),
      creates: Request[] = [],
      bodies: unknown[] = [],
      previews: unknown[] = [];
    show("/workspace/ws_one/schedules", async (request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/v1/schedules" && request.method === "GET")
        return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/revisions"))
        return Response.json({ items: [], next_cursor: null });
      if (path === "/api/v1/agents")
        return Response.json({
          items: [{ id: "agt_0123456789abcdef0123", name: "Reporter" }],
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
    const enable = within(dialog).getByRole("checkbox", {
      name: "Enable immediately",
    });
    expect(enable.getAttribute("aria-checked")).toBe("false");
    if (enableImmediately) await user.click(enable);
    const createLabel = enableImmediately
      ? "Create and enable"
      : "Create schedule";
    await user.type(within(dialog).getByLabelText("Name"), "Morning report");
    await user.click(within(dialog).getByRole("combobox", { name: "Agent" }));
    await user.click(await screen.findByRole("option", { name: "Reporter" }));
    await user.type(within(dialog).getByLabelText("Input"), "Report");
    await user.click(
      within(dialog).getByRole("button", { name: "Preview next times" }),
    );
    // Trigger times read as dates on the schedule's clock, not as ISO strings.
    const times = await screen.findByLabelText("Next scheduled times");
    expect(times.querySelector("time")?.getAttribute("datetime")).toBe(at);
    expect(times.textContent).not.toContain("2026-10-20T");
    expect(previews[0]).toMatchObject({
      schedule: { expression: "0 9 * * *" },
      count: 5,
    });
    await user.click(
      within(dialog).getByRole("button", {
        name: createLabel,
      }),
    );
    await screen.findByText("Temporary outage");
    await user.click(
      within(dialog).getByRole("button", {
        name: createLabel,
      }),
    );
    await screen.findByRole("heading", { name: "Morning report" });
    expect(creates).toHaveLength(2);
    expect(creates[0].headers.get("Idempotency-Key")).toBe(
      creates[1].headers.get("Idempotency-Key"),
    );
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[0]).toMatchObject({
      state: enableImmediately ? "enabled" : "paused",
      invocation: {
        agent_id: "agt_0123456789abcdef0123",
        agent_revision_id: null,
        payload: { content: [{ type: "text", text: "Report" }] },
      },
    });
  },
);

it("pages history, distinguishes failed input from submission, and refreshes an old result manually", async () => {
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
                status: completed ? "completed" : "submitted",
                execution_outcome: completed ? "succeeded" : null,
                completion_run_id: completed ? "run_resumed" : null,
                completed_at: completed ? "2026-10-20T00:01:00Z" : null,
                reason: null,
                thread_id: "thr_old",
                entry_id: "entry_old",
                execution: {
                  initial_entry_status: completed ? "consumed" : "assigned",
                  initial_run: {
                    id: "run_old",
                    status: completed ? "waiting" : "running",
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
  });
  expect(screen.queryByText("Execution finished")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(await screen.findByText("Execution finished")).toBeTruthy();
  expect(await screen.findByText("state.succeeded")).toBeTruthy();
  expect(controllers).toHaveLength(0);
  await waitFor(() =>
    expect(
      history.filter((cursor) => cursor === "older").length,
    ).toBeGreaterThan(1),
  );
});

it.each(["session", "thread", "create_thread"])(
  "edits the %s delivery target without a live subscription",
  async (mode) => {
    const user = userEvent.setup();
    const bodies: Schema["ScheduleUpdate"][] = [];
    const streams: string[] = [];
    show("/workspace/ws_one/schedules/sch_one", async (request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/stream")) {
        streams.push(path);
        return live(request, []);
      }
      if (path.endsWith("/status")) return Response.json(status);
      if (path.endsWith("/occurrences") || path.endsWith("/revisions"))
        return Response.json({ items: [], next_cursor: null });
      if (path === "/api/v1/agents")
        return Response.json({
          items: [{ id: schedule.invocation.agent_id, name: "Reporter" }],
          next_cursor: null,
        });
      if (request.method === "PATCH") {
        bodies.push(await request.json());
        return Response.json({ ...schedule, version: 2 });
      }
      return Response.json(schedule);
    });
    await user.click(
      await screen.findByRole("button", { name: "Edit schedule" }),
    );
    const dialog = within(screen.getByRole("dialog"));
    if (mode !== "session") {
      await user.click(
        dialog.getByRole("button", { name: "Reuse one thread" }),
      );
      if (mode === "thread")
        await user.type(
          dialog.getByLabelText("Thread ID (optional)"),
          "thread_existing",
        );
    }
    if (mode !== "thread") {
      await user.clear(dialog.getByLabelText("Session ID (optional)"));
      if (mode === "session")
        await user.type(
          dialog.getByLabelText("Session ID (optional)"),
          "sess_existing",
        );
    }
    await user.click(dialog.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0].target).toEqual({
      mode: mode === "session" ? "new_thread" : "thread",
      session_id: mode === "session" ? "sess_existing" : null,
      thread_id: mode === "thread" ? "thread_existing" : null,
    });
    expect(streams).toEqual([]);
  },
);

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
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "More schedule actions" }));
  expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Run once" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Run once" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit schedule" })).toBeNull();
  expect(
    screen.getByText(
      "Only the account that created this schedule can edit it. You are signed in with a different account.",
    ),
  ).toBeTruthy();
});

it("shows every saved input part and links to the target agent and session", async () => {
  const instructions =
    "First line\n" + "Long instruction. ".repeat(300) + "\nFinal line";
  const content: Schema["Schedule"]["invocation"]["payload"]["content"] = [
    { type: "text", text: instructions },
    {
      type: "json",
      value: { audience: "team", include: ["issues", "pull requests"] },
    },
    { type: "text", text: "Send the meeting reminder after the report." },
  ];
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, []);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    // The backend authorizes the creator, independently of the execution principal.
    return Response.json({
      ...schedule,
      principal_id: "usr_executor",
      description: "Summary only",
      invocation: { ...schedule.invocation, payload: { content } },
    });
  });
  const section = within(
    await screen.findByRole("region", { name: "Execution instructions" }),
  );
  expect(
    section.getByText((_text, node) => node?.textContent === instructions),
  ).toBeTruthy();
  expect(section.getByText(/"audience": "team"/).textContent).toContain(
    '"pull requests"',
  );
  expect(
    section.getByText("Send the meeting reminder after the report."),
  ).toBeTruthy();
  expect(
    (await screen.findByRole("link", { name: "Reporter" })).getAttribute(
      "href",
    ),
  ).toBe(`/workspace/ws_one/agents/${schedule.invocation.agent_id}`);
  expect(
    screen
      .getByRole("link", { name: schedule.session_id })
      .getAttribute("href"),
  ).toBe("/workspace/ws_one/sessions/ses_one");
  expect(section.queryByText("Target agent")).toBeNull();
  expect(section.queryByText("Target session")).toBeNull();
  expect(section.queryByText("Agent version")).toBeNull();
  expect(screen.getByText("Schedule description: Summary only")).toBeTruthy();
  expect(section.queryByText(/Summary only/)).toBeNull();
  expect(screen.getByRole("button", { name: "Edit schedule" })).toBeTruthy();
});

it.each([
  {
    state: "archived",
    verbs: ["write", "run"],
    reason: "Archived schedules cannot be edited.",
  },
  {
    state: "completed",
    verbs: ["write", "run"],
    reason: "Schedules that have finished cannot be edited.",
  },
  {
    state: "paused",
    verbs: ["run"],
    reason:
      "Editing this schedule requires both write and run permissions in this workspace.",
  },
  {
    state: "paused",
    verbs: ["write"],
    reason:
      "Editing this schedule requires both write and run permissions in this workspace.",
  },
])(
  "explains why a $state schedule with $verbs permissions cannot be edited",
  async ({ state, verbs, reason }) => {
    permissions = verbs;
    show("/workspace/ws_one/schedules/sch_one", (request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith("/stream")) return live(request, []);
      if (path.endsWith("/status")) return Response.json(status);
      if (path.endsWith("/occurrences"))
        return Response.json({ items: [], next_cursor: null });
      return Response.json({ ...schedule, state });
    });
    expect(await screen.findByText(reason)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit schedule" })).toBeNull();
    expect(
      screen.getByRole("region", { name: "Execution instructions" }),
    ).toBeTruthy();
  },
);

it("keeps an open draft's original ETag after a concurrent configuration change", async () => {
  const user = userEvent.setup(),
    controllers: ReadableStreamDefaultController<Uint8Array>[] = [],
    tags: string[] = [],
    updates: Request[] = [];
  let version = 1;
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, controllers);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    if (path.endsWith("/revisions"))
      return Response.json({ items: [], next_cursor: null });
    if (path === "/api/v1/agents")
      return Response.json({
        items: [{ id: "agt_0123456789abcdef0123", name: "Reporter" }],
        next_cursor: null,
      });
    if (request.method === "PATCH") {
      updates.push(request);
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
  expect(
    within(dialog).queryByRole("checkbox", { name: "Enable immediately" }),
  ).toBeNull();
  await user.type(within(dialog).getByLabelText("Name"), " edited");
  version = 2;
  expect(controllers).toHaveLength(0);
  await user.click(
    within(dialog).getByRole("button", { name: "Save changes" }),
  );
  expect(await screen.findByText("Schedule changed")).toBeTruthy();
  expect(tags).toEqual(['"sch_one:1"']);
  expect(await updates[0].json()).not.toHaveProperty("state");
  expect(
    (within(dialog).getByLabelText("Name") as HTMLInputElement).value,
  ).toBe("Morning report edited");
});

it("keeps completed one-time history visible while only offering archive", async () => {
  show("/workspace/ws_one/schedules/sch_one", (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, []);
    if (path.endsWith("/status"))
      return Response.json({ ...status, has_unfinished_work: true });
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [occurrence()], next_cursor: null });
    return Response.json({
      ...schedule,
      state: "completed",
      pause_reason: null,
      schedule: { type: "once", run_at: at },
    });
  });
  expect(await screen.findByText("Scheduling finished")).toBeTruthy();
  expect(await screen.findByText("No input submitted")).toBeTruthy();
  expect(screen.getByText("Pending or unfinished work")).toBeTruthy();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "More schedule actions" }));
  expect(await screen.findByRole("menuitem", { name: "Archive" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Run once" })).toBeNull();
  for (const name of ["Enable", "Pause", "Edit schedule", "Run once"])
    expect(screen.queryByRole("button", { name })).toBeNull();
});

it("preserves an unchanged one-time instant when editing metadata after its time has passed", async () => {
  const user = userEvent.setup();
  const once = {
    ...schedule,
    schedule: { type: "once", run_at: "2020-10-21T09:30:15.123456Z" },
  };
  let saved: Schema["ScheduleUpdate"] | undefined;
  show("/workspace/ws_one/schedules/sch_one", async (request) => {
    const path = new URL(request.url).pathname;
    if (path.endsWith("/stream")) return live(request, []);
    if (path.endsWith("/status")) return Response.json(status);
    if (path.endsWith("/occurrences"))
      return Response.json({ items: [], next_cursor: null });
    if (path === "/api/v1/agents")
      return Response.json({
        items: [{ id: "agt_one", name: "Reporter" }],
        next_cursor: null,
      });
    if (request.method === "PATCH") saved = await request.json();
    return Response.json(once);
  });
  await user.click(
    await screen.findByRole("button", { name: "Edit schedule" }),
  );
  const dialog = screen.getByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), " edited");
  await user.click(
    within(dialog).getByRole("button", { name: "Save changes" }),
  );
  await waitFor(() => expect(saved?.schedule).toEqual(once.schedule));
  expect(saved?.name).toBe("Morning report edited");
});
