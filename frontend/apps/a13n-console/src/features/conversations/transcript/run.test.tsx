// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
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
import { MemoryRouter, Route, Routes, useNavigate } from "react-router";
import { createClient, type Client } from "../../../service-client";
import {
  fixtureRun,
  fixtureRunPage,
  fixtureThread,
  textInput,
} from "./fixture";
import { RunContent, RunPage } from "./run";
import type { DisplayItem } from "../display";
import { runExecution } from "../execution";
import { presentItems } from "../projection";

let displayItems: DisplayItem[] = [];
let navigation = false;
let pendingDetail: Promise<void> | undefined;
let pendingItems: Promise<void> | undefined;
let firstOrdinal = 1;
const loadEarlier = vi.fn();

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
  calls: [] as string[],
}));
vi.mock("../run-display", () => ({
  useRunDisplay: (runId: string) => {
    stream.calls.push(runId);
    return {
      first: firstOrdinal,
      history: {
        read: { isFetching: false },
        earlier: { isPending: false, error: null },
        loadEarlier,
      },
      displayItems,
      items: presentItems(displayItems),
      connected: true,
      execution: {
        ...runExecution(fixtureRun(), displayItems),
        coverage: "complete",
      },
    };
  },
}));
vi.mock("./history", () => ({
  HistoryTranscript: () => <button>Load earlier runs</button>,
}));
vi.mock("../agents/queries", () => ({ useAgent: () => ({ data: null }) }));
vi.mock("./inbox", () => ({ ThreadInbox: () => <p>Thread inbox</p> }));
beforeEach(() => {
  displayItems = [];
  navigation = false;
  pendingDetail = undefined;
  pendingItems = undefined;
  firstOrdinal = 1;
  loadEarlier.mockClear();
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
      if (path.endsWith("/items")) {
        if (path.endsWith("/runs/run_4/items")) await pendingItems;
        return Response.json({
          items: [],
          total: 0,
          immutable_before: 1,
          complete: true,
          next_cursor: null,
        });
      }
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
      if (path.endsWith("/threads/thread/runs")) {
        const url = new URL(request.url);
        const first = url.searchParams.has("cursor") ? 4 : 6;
        return Response.json(
          fixtureRunPage(
            navigation
              ? [first, first - 1].map((n) =>
                  fixtureRun({
                    id: `run_${n}`,
                    thread_id: "thread",
                    session_id: "session",
                    created_at: new Date(n * 1000).toISOString(),
                  }),
                )
              : [],
            {
              total: navigation ? 6 : 0,
              first_number: first,
              next_cursor: first === 6 ? "older" : null,
              previous_cursor: null,
            },
          ),
        );
      }
      if (navigation && /\/runs\/run_\d+$/.test(path)) {
        const id = path.split("/").at(-1)!;
        if (id === "run_4") await pendingDetail;
        return Response.json(
          fixtureRun({
            id,
            thread_id: "thread",
            session_id: "session",
            agent_id: "agent",
            input: textInput(`Message ${id}`),
            status: "completed",
          }),
        );
      }
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
it("reads the level from the URL and shows the run's display at either level", async () => {
  show();
  expect(await screen.findByText("Build an agent")).toBeTruthy();
  expect(stream.calls.at(-1)).toBe("run");
  cleanup();
  show("/?view=debug");
  // Debug reads one run as a section, with its terminal fact in the timeline.
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(stream.calls.at(-1)).toBe("run");
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

it("keeps loaded Run rows, scroll and focus through routed inspection and Back", async () => {
  navigation = true;
  let finishDetail!: () => void;
  let finishItems!: () => void;
  pendingItems = new Promise<void>((resolve) => {
    finishItems = resolve;
  });
  pendingDetail = new Promise<void>((resolve) => {
    finishDetail = resolve;
  });
  const scrollIntoView = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
  function Back() {
    const navigate = useNavigate();
    return <button onClick={() => navigate(-1)}>Back</button>;
  }
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[
          "/workspace/design/sessions/session/threads/thread/runs/run_6?view=debug",
        ]}
      >
        <Back />
        <Routes>
          <Route
            path="/workspace/design/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={<RunPage />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const panel = await screen.findByRole("navigation", { name: "Runs" });
  const list = within(panel);
  await list.findByText("Run 6");
  const oldContent = await screen.findByText("Message run_6");
  const queue = screen.getByText("Thread inbox");
  const dock = queue.closest("[data-thread-dock]");
  expect(dock).not.toBeNull();
  await user.click(list.getByRole("button", { name: "Earlier runs" }));
  const selected = await list.findByRole("button", { name: /^Run 4/ });
  const rows = list.getAllByRole("button");
  panel.scrollTop = 90;
  scrollIntoView.mockClear();
  await user.click(selected);
  await waitFor(() =>
    expect(
      requests.some((r) => new URL(r.url).pathname.endsWith("/runs/run_4")),
    ).toBe(true),
  );
  const expectStable = () => {
    expect(screen.getByRole("navigation", { name: "Runs" })).toBe(panel);
    expect(list.getAllByRole("button")).toEqual(rows);
    expect(panel.scrollTop).toBe(90);
    expect(document.activeElement).toBe(selected);
    expect(selected.getAttribute("aria-current")).toBe("true");
  };
  expectStable();
  expect(screen.getByText("Message run_6")).toBe(oldContent);
  expect(oldContent.closest("[inert]")).not.toBeNull();
  expect(screen.getByText("Loading run…")).toBeTruthy();
  expect(document.querySelector('[data-variant="detail"]')).toBeNull();
  expect(screen.getByText("Thread inbox")).toBe(queue);
  await act(async () => {
    finishDetail();
  });
  expect(screen.getByText("Message run_6")).toBe(oldContent);
  await act(async () => {
    finishItems();
  });
  await screen.findByText("Message run_4");
  expect(screen.queryByText("Loading run…")).toBeNull();
  expect(screen.getByText("Thread inbox")).toBe(queue);
  expect(queue.closest("[data-thread-dock]")).toBe(dock);
  expectStable();
  expect(scrollIntoView.mock.instances).not.toContain(selected);
  await user.click(screen.getByRole("button", { name: "Back" }));
  await waitFor(() =>
    expect(
      list.getByRole("button", { name: /^Run 6/ }).getAttribute("aria-current"),
    ).toBe("true"),
  );
  expect(list.getAllByRole("button")).toEqual(rows);
  expect(screen.queryByText("Loading run…")).toBeNull();
  expect(screen.getByText("Thread inbox")).toBe(queue);
  expect(
    requests.filter((r) =>
      new URL(r.url).pathname.endsWith("/runs/run_6/items"),
    ),
  ).toHaveLength(1);
  // The selected Run's detail may load, but its list must not be anchored again.
  const lists = requests.filter((r) =>
    new URL(r.url).pathname.endsWith("/threads/thread/runs"),
  );
  expect(lists.map((r) => new URL(r.url).searchParams.get("anchor"))).toEqual([
    "run_6",
    null,
    "run_4",
    "run_6",
  ]);
  scrollIntoView.mockRestore();
});

it("keeps earlier content inside its Debug Run instead of stacking history controls above it", async () => {
  firstOrdinal = 201;
  show("/?view=debug");
  const earlier = await screen.findByRole("button", {
    name: "Load earlier content",
  });
  expect(earlier.closest("[data-run]")?.getAttribute("data-run")).toBe("run");
  expect(
    screen.queryByRole("button", { name: "Load earlier runs" }),
  ).toBeNull();
  await userEvent.setup().click(earlier);
  expect(loadEarlier).toHaveBeenCalledWith(201, expect.any(Function));
});

it("ignores a late item response after selecting another Run", async () => {
  navigation = true;
  let finish!: () => void;
  pendingItems = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const view = (id: string) => (
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/?view=debug"]}>
        <RunContent runId={id} threadId="thread" sessionId="session" />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const rendered = render(view("run_6"));
  await screen.findByText("Message run_6");
  rendered.rerender(view("run_4"));
  await screen.findByText("Loading run…");
  rendered.rerender(view("run_3"));
  await screen.findByText("Message run_3");
  await act(async () => {
    finish();
  });
  expect(screen.queryByText("Message run_4")).toBeNull();
  expect(screen.getByText("Message run_3")).toBeTruthy();
  expect(screen.queryByText("Loading run…")).toBeNull();
});

it.each(["chat", "debug"])(
  "keeps an external session read-only in explicit %s view",
  async (level) => {
    labels = {};
    show(`/?view=${level}`);
    await screen.findByText(
      level === "debug" ? "Run failed" : "Build an agent",
    );
    expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Resubmit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Create branch/ })).toBeNull();
    expect(requests.every((request) => request.method === "GET")).toBe(true);
  },
);
