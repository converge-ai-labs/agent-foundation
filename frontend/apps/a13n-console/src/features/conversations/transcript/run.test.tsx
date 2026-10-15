// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../../service-client";
import { fixtureRun, fixtureThread, textInput } from "./fixture";
import { RunContent } from "./run";
import type { DisplayItem } from "../display";
import { runExecution } from "../execution";
import { presentItems } from "../projection";

let displayItems: DisplayItem[] = [];

let client: Client;
let cache: QueryClient;
const requests: Request[] = [];
/** Sessions the Console starts carry its label; applications' do not. */
let labels: Record<string, string>;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const stream = vi.hoisted(() => ({
  calls: [] as { live?: boolean }[],
}));
vi.mock("../run-display", () => ({
  useRunDisplay: (_runId: string, options: { live?: boolean } = {}) => {
    stream.calls.push(options);
    return {
      displayItems,
      items: presentItems(displayItems),
      state: "connected",
      execution: {
        ...runExecution(fixtureRun(), displayItems),
        coverage: "complete",
      },
    };
  },
}));
vi.mock("./history", () => ({ HistoryTranscript: () => null }));
vi.mock("../agents/queries", () => ({ useAgent: () => ({ data: null }) }));
vi.mock("./inbox", () => ({ ThreadInbox: () => <p>Thread inbox</p> }));
beforeEach(() => {
  displayItems = [];
  requests.length = 0;
  stream.calls.length = 0;
  labels = { "a13n.console": "debug" };
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/runs/run/contents/cnt_observation"))
        return Response.json({
          id: "cnt_observation",
          media_type: "application/json",
          value: { detail: "Full tool invocation details" },
        });
      if (path.endsWith("/threads/thread"))
        return Response.json(
          fixtureThread({
            id: "thread",
            session_id: "session",
            last_run_id: "run",
          }),
        );
      if (path.endsWith("/threads/thread/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/threads"))
        return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/sessions/session"))
        return Response.json({
          id: "session",
          workspace_id: "workspace",
          labels,
          created_by_id: "usr_1",
          last_run_id: "run",
          version: 1,
          created_at: "2026-09-20T10:00:00.000Z",
          updated_at: "2026-09-20T10:00:12.000Z",
        });
      if (path.endsWith("/runs/run"))
        return Response.json(
          fixtureRun({
            id: "run",
            thread_id: "thread",
            session_id: "session",
            agent_id: "agent",
            status: "failed",
            input: textInput("Build an agent"),
            output: null,
            revision_selection: "pinned",
            options: { max_usage: { requests: 4 } },
          }),
        );
      throw new Error(`Unexpected request: ${path}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
  vi.unstubAllGlobals();
});
function show(entry = "/") {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[entry]}>
        <RunContent runId="run" threadId="thread" sessionId="session" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it("prefills a stopped run's message to resubmit it instead of retrying it", async () => {
  show();
  expect(await screen.findByText("Build an agent")).toBeTruthy();
  expect(screen.getByText("Thread inbox")).toBeTruthy();
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "",
  );
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Resubmit" }));
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "Build an agent",
  );
  expect(requests.every((request) => request.method === "GET")).toBe(true);
});
it("offers the same resubmission beside a stopped run's outcome in Debug", async () => {
  show("/?view=debug");
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "Resubmit" }));
  expect(screen.getByRole("textbox", { name: "Message" })).toHaveProperty(
    "value",
    "Build an agent",
  );
});
it("opens an application's session at the Debug level", async () => {
  labels = {};
  show();
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(screen.getByText("Run failed")).toBeTruthy();
});
it("reads the level from the URL and follows the Thread at either level", async () => {
  show();
  expect(await screen.findByText("Build an agent")).toBeTruthy();
  expect(stream.calls.at(-1)).toEqual({ live: true });
  cleanup();
  show("/?view=debug");
  // Debug reads one run as a section, with its terminal fact in the timeline.
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(stream.calls.at(-1)).toEqual({ live: true });
  expect(screen.getByText("Run failed")).toBeTruthy();
  // The details card stays lazy; only the navigator's lineage read happens.
  const panels = requests.filter(
    (request) => new URL(request.url).pathname.split("/").at(-1) === "attempts",
  );
  expect(panels).toEqual([]);
});

it("keeps retained observations inside Debug and loads their full content on demand", async () => {
  const name = "a13n.pydantic_ai.function_tool_call";
  displayItems = [
    {
      id: "itm_observation",
      ordinal: 1,
      kind: "observation",
      state: "completed",
      first_stream_id: "1-1",
      last_stream_id: "1-1",
      started_at: "2026-09-20T10:00:01.000Z",
      content: { name, value: { preview: "Tool invocation" } },
      content_refs: {
        value: {
          id: "cnt_observation",
          size_bytes: 50000,
          media_type: "application/json",
          preview: '{"detail":"Full tool invocation',
        },
      },
    },
  ];
  show("/?view=debug");
  const user = userEvent.setup();
  const observations = await screen.findByRole("button", {
    name: /1 other observations/,
  });
  expect(screen.queryByText(name)).toBeNull();
  expect(requests.some((request) => request.url.includes("/contents/"))).toBe(
    false,
  );
  await user.click(observations);
  expect(screen.getAllByText(name)).toHaveLength(1);
  await user.click(screen.getByText("Full content: Details"));
  await user.click(screen.getByRole("button", { name: "Expand full content" }));
  expect(await screen.findByText(/Full tool invocation details/)).toBeTruthy();
  expect(
    requests.filter((request) =>
      request.url.includes("/runs/run/contents/cnt_observation"),
    ),
  ).toHaveLength(1);
  await user.click(observations);
  expect(screen.queryByText(name)).toBeNull();
});
