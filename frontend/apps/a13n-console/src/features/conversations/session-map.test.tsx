import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "@converge.ai/a13n";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import type { Schema } from "../../shared/api";
import { useState } from "react";
import { SessionMap } from "./session-map";

let client: Client;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: vi.fn(),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/default",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: "en" },
    t: (key: string, values: Record<string, unknown> = {}) =>
      key.replace(/{{(\w+)}}/g, (_, name: string) => String(values[name])),
  }),
}));
afterEach(() => {
  cleanup();
  client.close();
  vi.restoreAllMocks();
});

const threads = [
  {
    id: "thread-root",
    session_id: "session",
    origin_thread_id: null,
    origin_run_id: null,
    origin_kind: "initial",
    created_at: "2026-09-12T00:00:00Z",
  },
  {
    id: "thread-branch",
    session_id: "session",
    origin_thread_id: "thread-root",
    origin_run_id: "run-root",
    origin_kind: "fork",
    created_at: "2026-09-12T00:01:00Z",
  },
] as Schema["ThreadResource"][];
const runs = [
  {
    id: "run-root",
    thread_id: "thread-root",
    session_id: "session",
    input: {
      content: [{ type: "text", text: "Review the release checklist" }],
    },
    input_text: null,
    status: "completed",
    trigger_type: "user",
    created_at: "2026-09-12T00:00:00Z",
  },
  {
    id: "run-branch",
    thread_id: "thread-branch",
    session_id: "session",
    input_text: "Explore a different proposal",
    status: "completed",
    trigger_type: "user",
    created_at: "2026-09-12T00:01:00Z",
  },
];
function MapHarness() {
  const [open, setOpen] = useState(true);
  return open ? (
    <SessionMap threads={threads} onClose={() => setOpen(false)} />
  ) : null;
}
function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}
function mount({
  stream = "complete",
  withHistory = false,
}: {
  stream?: "complete" | "gap" | "truncated" | "forbidden";
  withHistory?: boolean;
} = {}) {
  const requests: string[] = [];
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const path = new URL(new Request(input, init).url).pathname;
      requests.push(path);
      if (path.endsWith("/stream")) {
        if (stream === "forbidden")
          return Response.json(
            { error: { code: "forbidden", message: "Stream access denied" } },
            { status: 403 },
          );
        const runId = path.split("/").at(-2)!;
        const records = [
          {
            event_type: "agui.custom",
            payload: {
              name: "a13n.harness.lifecycle",
              value: {
                event: {
                  payload: {
                    type: "model_request_started",
                    request_id: "model-request-1",
                  },
                },
              },
            },
          },
          {
            event_type: "agui.text_message_start",
            item_id: "reply",
            payload: { role: "assistant", item_kind: "text_message" },
          },
          {
            event_type: "agui.text_message_content",
            item_id: "reply",
            payload: { delta: "Release reviewed" },
          },
          { event_type: "run.completed", payload: {} },
        ];
        if (stream === "truncated") records.pop();
        const body =
          stream === "gap"
            ? "event: run_stream.replay_gap\ndata: {}\n\n"
            : records
                .map(
                  (record, index) =>
                    `id: 1-${index}\nevent: ${record.event_type}\ndata: ${JSON.stringify({ schema_version: "1", event_id: `event-${index}`, run_id: runId, thread_id: "thread-root", run_attempt_id: "attempt", harness_run_id: "harness", occurred_at: "2026-09-12T00:00:00Z", ...record })}\n\n`,
                )
                .join("");
        return new Response(body, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      return Response.json({
        items: [
          ...(withHistory
            ? [1, 2].map((index) => ({
                ...runs[0],
                id: `run-earlier-${index}`,
                input: null,
                input_text: `Earlier request ${index}`,
                created_at: `2026-09-11T00:00:0${index}Z`,
              }))
            : []),
          ...runs,
        ].filter((run) => path.includes(run.thread_id)),
        next_cursor: null,
      });
    },
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter
        initialEntries={[
          "/workspace/default/sessions/session/threads/thread-branch/runs/run-branch",
        ]}
      >
        <Routes>
          <Route
            path="/workspace/default/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={
              <>
                <MapHarness />
                <Location />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return requests;
}

it("nests branches at their origin, previews on hover, and selects the exact run", async () => {
  const requests = mount();
  const user = userEvent.setup();
  const root = await screen.findByRole("link", {
    name: /Review the release checklist/,
  });
  const branch = screen.getByRole("link", {
    name: /Explore a different proposal/,
  });
  expect(root.closest("li")?.contains(branch)).toBe(true);
  expect(branch.getAttribute("aria-current")).toBe("page");
  expect(screen.getByText("2 threads")).toBeTruthy();
  expect(requests.some((path) => path.endsWith("/stream"))).toBe(false);
  await user.hover(root);
  expect(await screen.findByRole("tooltip")).toBeTruthy();
  expect(
    within(screen.getByRole("tooltip")).getByText(
      "Review the release checklist",
    ),
  ).toBeTruthy();
  expect(screen.getByTestId("location").textContent).toContain("run-branch");
  await user.unhover(root);
  await user.click(
    screen.getAllByRole("button", { name: "Inspect steps for Run 1" })[0]!,
  );
  expect(await screen.findByText("Step 1")).toBeTruthy();
  expect(requests.filter((path) => path.endsWith("/stream"))).toHaveLength(1);
  await user.click(root);
  expect(
    screen.getByRole("complementary", { name: "Session map" }),
  ).toBeTruthy();
  expect(screen.getByTestId("location").textContent).toContain(
    "thread-root/runs/run-root",
  );
});

it("distinguishes unavailable execution history and closes only on explicit dismissal", async () => {
  mount({ stream: "gap" });
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /Review the release checklist/ });
  await user.click(
    screen.getAllByRole("button", { name: "Inspect steps for Run 1" })[0]!,
  );
  expect(
    await screen.findByText("Execution history is unavailable or incomplete."),
  ).toBeTruthy();
  expect(screen.queryByText("0 steps")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Close session map" }));
  expect(screen.queryByRole("complementary")).toBeNull();
});

it("collapses consecutive runs without hiding their fork point or child thread", async () => {
  mount({ withHistory: true });
  const user = userEvent.setup();
  const branch = await screen.findByRole("link", {
    name: /Explore a different proposal/,
  });
  const group = screen.getByRole("button", { name: /Runs 1–2/ });
  expect(group.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("link", { name: /Earlier request/ })).toBeNull();
  await user.click(group);
  expect(screen.getAllByRole("link", { name: /Earlier request/ })).toHaveLength(
    2,
  );
  await user.click(group);
  expect(screen.queryByRole("link", { name: /Earlier request/ })).toBeNull();
  expect(branch.isConnected).toBe(true);
  expect(
    screen.getByRole("link", { name: /Run 3.*Review the release checklist/ }),
  ).toBeTruthy();
});

it("does not report a complete count when replay ends without a terminal event", async () => {
  mount({ stream: "truncated" });
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /Review the release checklist/ });
  await user.click(
    screen.getAllByRole("button", { name: "Inspect steps for Run 1" })[0]!,
  );
  expect(
    await screen.findByText("Execution history is unavailable or incomplete."),
  ).toBeTruthy();
  expect(screen.queryByText("1 steps")).toBeNull();
});

it("keeps an access error separate from an empty or expired history", async () => {
  mount({ stream: "forbidden" });
  const user = userEvent.setup();
  await screen.findByRole("link", { name: /Review the release checklist/ });
  await user.click(
    screen.getAllByRole("button", { name: "Inspect steps for Run 1" })[0]!,
  );
  expect(await screen.findByText("Stream access denied")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
  expect(
    screen.queryByText("Execution history is unavailable or incomplete."),
  ).toBeNull();
  expect(screen.queryByText("0 steps")).toBeNull();
});
