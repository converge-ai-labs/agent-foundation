// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { BrowserApp } from "./app";
import { Realtime } from "./transport/realtime";

const paths: string[] = [];
const sockets: string[] = [];
const closedSockets: string[] = [];
let permission = "view";
let scope = "thread";
let targetRole = "ordinary";
let workerCount = 0;
let workers: (typeof worker)[] = [];
let selectedWorker: typeof worker | undefined;
const subscriptions = new Map<string, Parameters<Realtime["subscribe"]>[0]>();
const listParams: URLSearchParams[] = [];
const thread = {
  thread_id: "thread-one",
  title: "Design together",
  created_at: "2026-10-19",
  metadata_version: 1,
  archived: false,
  continuation_state: "selected",
  root_activity: { state: "inactive" },
  configuration: {
    version: 1,
    agent_source: { kind: "agent", id: "assistant" },
    project_id: "project-one",
  },
};
const worker = {
  ...thread,
  thread_id: "thread-worker",
  role: "worker",
  title: "Build sidebar",
  coordinator_thread_id: "thread-one",
};
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  paths.length = 0;
  sockets.length = 0;
  closedSockets.length = 0;
  permission = "view";
  scope = "thread";
  targetRole = "ordinary";
  workerCount = 0;
  workers = [];
  selectedWorker = undefined;
  subscriptions.clear();
  listParams.length = 0;
  localStorage.clear();
  sessionStorage.clear();
  localStorage.setItem("a13n-harness-ui.api-key", "owner-key-untouched");
  window.history.replaceState(null, "", "/share/share-one#key=guest-only");
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
  vi.stubGlobal(
    "WebSocket",
    class {
      static OPEN = 1;
      readyState = 0;
      constructor(private readonly url: URL) {
        sockets.push(String(url));
      }
      close() {
        closedSockets.push(String(this.url));
      }
      send() {}
    },
  );
  vi.spyOn(Realtime.prototype, "subscribe").mockImplementation(
    (subscription) => {
      expect(subscription.stream).toBe("focus");
      subscriptions.set(subscription.root!, subscription);
      const close = () => {};
      close.restart = () => {};
      close.retry = () => {};
      return close;
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      paths.push(path);
      expect(window.location.hash).toBe("");
      expect(request.headers.get("Authorization")).toBe("Bearer guest-only");
      const prefix = "/api/shared/share-one";
      const missing = () =>
        json(
          { error: { code: "share_unavailable", message: "Unavailable" } },
          401,
        );
      const lead = {
        ...thread,
        role: targetRole,
        unarchived_worker_count: workerCount,
      };
      if (path === prefix + "/access")
        return json({
          target_role: scope === "thread" ? targetRole : null,
          title: scope === "thread" ? "Design together" : "Design studio",
          grant: {
            share_id: "share-one",
            scope,
            target_id: scope === "thread" ? "thread-one" : "project-one",
            permission,
            created_at: "2026-10-19",
            expires_at: "2027-10-20",
          },
        });
      if (path === prefix + "/threads") {
        listParams.push(url.searchParams);
        const list =
          url.searchParams.has("coordinator_thread_id") ||
          url.searchParams.has("query")
            ? workers
            : [lead];
        return json({ threads: list, total: list.length });
      }
      if (path === prefix + "/threads/thread-worker" && !selectedWorker)
        return missing();
      const equivalent = path.replace("/thread-worker", "/thread-one");
      if (equivalent === prefix + "/threads/thread-one")
        return json({
          thread: path === equivalent ? lead : selectedWorker,
          available_actions: permission === "view" ? [] : ["run"],
          continuation_id: "saved",
          model_display: {
            agent_name: "Assistant",
            model_name: "GPT-5.4",
            model_identity: "openai:gpt-5.4",
            thinking_summary: "High",
            fast: "on",
          },
        });
      if (equivalent === prefix + "/threads/thread-one/context-usage")
        return json({
          thread_id: path === equivalent ? "thread-one" : "thread-worker",
          context_window: 200000,
          latest_request_tokens: 18500,
        });
      if (equivalent === prefix + "/threads/thread-one/usage")
        return json({
          first_observed_at: "2026-10-19",
          root: { model_requests: 1, tokens: [] },
          models: [],
          other_models: { model_requests: 0 },
          combined: {
            provider_receipts: 0,
            model_requests: 1,
            unknown_model_costs: 0,
            model_cost_usd: "0.042",
            tokens: [
              ["input_tokens", 18500],
              ["output_tokens", 1200],
            ],
          },
        });
      if (equivalent === prefix + "/threads/thread-one/transcript")
        return json({
          continuation_id: "saved",
          entries: [],
          turns: [],
          next_cursor: null,
        });
      if (equivalent === prefix + "/threads/thread-one/work")
        return json({ tasks: { total: 0 }, notes: { total: 0 }, children: {} });
      if (equivalent === prefix + "/threads/thread-one/inputs")
        return json({ turns: [], next_cursor: null });
      if (equivalent === prefix + "/threads/thread-one/children")
        return json({ executions: [], next_cursor: null });
      throw new Error(`Unadmitted guest request: ${request.method} ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("opens a thread guest directly without owner queries or a draft socket", async () => {
  render(<BrowserApp />);
  await screen.findByText("View-only link - Following this chat live.");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.getByRole("region", { name: "Message composer" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "Attach files" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(screen.getByText("GPT-5.4")).toBeTruthy();
  expect(screen.getByText("High")).toBeTruthy();
  expect(await screen.findByText("18.5k")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Cost details" }).textContent,
  ).toContain("0.042");
  expect(screen.queryByRole("button", { name: "Start voice" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBe(
    "owner-key-untouched",
  );
  expect(sessionStorage.getItem("a13n-harness-ui.share.share-one")).toBe(
    "guest-only",
  );
  expect(paths.every((path) => path.startsWith("/api/shared/share-one/"))).toBe(
    true,
  );
  expect(sockets).toHaveLength(1);
  expect(sockets[0]).toContain("/api/shared/share-one/presence/connect");
});

it("mounts the shared composer for prompt guests without owner controls", async () => {
  permission = "prompt";
  targetRole = "coordinator";
  render(<BrowserApp />);
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(screen.getByText("Coordinator")).toBeTruthy();
  expect(screen.getByText("GPT-5.4")).toBeTruthy();
  expect(screen.getByText("High")).toBeTruthy();
  expect(await screen.findByText("18.5k")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Goal" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Clear context/i })).toBeNull();
  expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Coordinator" })).toBeNull();
  await waitFor(() =>
    expect(
      sockets.some((url) =>
        url.endsWith("/api/shared/share-one/threads/thread-one/draft/connect"),
      ),
    ).toBe(true),
  );
});

it("keeps Project navigation inside the sharing basename", async () => {
  scope = "project";
  render(<BrowserApp />);
  const link = await screen.findByRole("link", { name: /Design together/ });
  expect(link.getAttribute("href")).toBe("/share/share-one/threads/thread-one");
  expect(screen.queryByRole("button", { name: "New chat" })).toBeNull();
  fireEvent.click(link);
  await screen.findByText("View-only link - Following this chat live.");
  expect(window.location.pathname).toBe("/share/share-one/threads/thread-one");
});

it("shows expired sharing access rather than instance login", async () => {
  vi.mocked(fetch).mockResolvedValue(
    json({ error: { code: "share_unavailable", message: "Unavailable" } }, 401),
  );
  render(<BrowserApp />);
  await screen.findByRole("heading", { name: "This link is unavailable" });
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBe(
    "owner-key-untouched",
  );
});

it("recovers uncertain Project creation by reading its exact identity without another create", async () => {
  scope = "project";
  permission = "prompt";
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  let createdId = "";
  let writes = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const request = input as Request;
    const path = new URL(request.url).pathname;
    if (request.method === "POST" && path.endsWith("/threads")) {
      writes++;
      createdId = (await request.json()).thread_id;
      throw new TypeError("Connection lost after creation");
    }
    if (createdId && path.endsWith(`/threads/${createdId}`))
      return json({
        thread: { ...thread, thread_id: createdId },
        available_actions: ["run"],
      });
    if (createdId && path.includes(createdId))
      return originalFetch(
        new Request(request.url.replace(createdId, "thread-one"), request),
        init,
      );
    return originalFetch(input, init);
  });
  vi.mocked(Realtime.prototype.subscribe).mockImplementation(() => {
    const close = () => {};
    close.restart = close;
    close.retry = close;
    return close;
  });
  render(<BrowserApp />);
  fireEvent.click(
    (await screen.findAllByRole("button", { name: "New chat" }))[0],
  );
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(window.location.pathname).toBe(
    `/share/share-one/threads/${createdId}`,
  );
  expect(writes).toBe(1);
});

it("shows the Coordinator rail only when workers exist and refreshes immediately on a live count change", async () => {
  targetRole = "coordinator";
  render(<BrowserApp />);
  await screen.findByText("View-only link - Following this chat live.");
  expect(screen.getByText("Coordinator")).toBeTruthy();
  expect(
    screen.queryByRole("complementary", { name: "Shared coordinator" }),
  ).toBeNull();
  await waitFor(() => expect(subscriptions.has("thread-one")).toBe(true));
  const workerRequests = () =>
    listParams.filter(
      (params) => params.get("coordinator_thread_id") === "thread-one",
    );
  await waitFor(() => expect(workerRequests()).toHaveLength(1));
  workers = [worker];
  selectedWorker = worker;
  act(() =>
    subscriptions.get("thread-one")?.receive({
      kind: "snapshot",
      resume_cursor: "cursor-one",
      snapshot: {
        epoch: "epoch-one",
        cutover_sequence: 0,
        thread: {
          thread: {
            ...thread,
            role: "coordinator",
            unarchived_worker_count: 1,
          },
          continuation_id: "saved",
        },
      },
    }),
  );
  const link = await screen.findByRole("link", { name: /Build sidebar/ });
  expect(workerRequests()).toHaveLength(2);
  fireEvent.click(link);
  await screen.findByRole("link", { name: "Managed by Design together" });
  expect(
    screen.getByText("View-only link - Following this chat live."),
  ).toBeTruthy();
  expect(link.getAttribute("aria-current")).toBe("page");
});

it("pins an archived worker opened directly and keeps a valid grant when the worker disappears", async () => {
  targetRole = "coordinator";
  selectedWorker = { ...worker, archived: true };
  window.history.replaceState(
    null,
    "",
    "/share/share-one/threads/thread-worker#key=guest-only",
  );
  render(<BrowserApp />);
  await screen.findByRole("link", { name: /Build sidebar/ });
  fireEvent.click(
    await screen.findByRole("link", { name: /^Design together/ }),
  );
  await screen.findByText("View-only link - Following this chat live.");
  selectedWorker = undefined;
  window.history.replaceState(
    null,
    "",
    "/share/share-one/threads/thread-worker",
  );
  // The active grant is checked independently of the stale conversation.
  // Returning from history can reuse its fresh cache, so reload the guest tab.
  cleanup();
  const closedBefore = closedSockets.length;
  const accessedBefore = paths.filter((path) =>
    path.endsWith("/access"),
  ).length;
  render(<BrowserApp />);
  await screen.findByRole("heading", { name: "Chat not found" });
  await waitFor(() =>
    expect(
      paths.filter((path) => path.endsWith("/access")).length,
    ).toBeGreaterThan(accessedBefore + 1),
  );
  expect(screen.queryByText("This link is unavailable")).toBeNull();
  expect(closedSockets.slice(closedBefore)).toEqual([
    expect.stringContaining("/presence/connect"),
  ]);
  const openedBeforeReturn = sockets.length;
  fireEvent.click(screen.getByRole("link", { name: "Back to shared chat" }));
  await screen.findByText("View-only link - Following this chat live.");
  expect(sockets.slice(openedBeforeReturn)).toEqual([
    expect.stringContaining("/presence/connect"),
  ]);
});

it("loads Project workers on disclosure and searches workers without the independent filter", async () => {
  scope = "project";
  targetRole = "coordinator";
  workerCount = 1;
  workers = [worker];
  render(<BrowserApp />);
  const expand = await screen.findByRole("button", {
    name: "Workers for Design together",
  });
  expect(listParams[0].get("independent_only")).toBe("true");
  expect(listParams.some((params) => params.has("coordinator_thread_id"))).toBe(
    false,
  );
  fireEvent.click(expand);
  await screen.findByRole("link", { name: /Build sidebar/ });
  expect(
    listParams.some(
      (params) => params.get("coordinator_thread_id") === "thread-one",
    ),
  ).toBe(true);
  fireEvent.change(screen.getByRole("searchbox"), {
    target: { value: "Build" },
  });
  await screen.findByLabelText("Coordinator worker");
  const search = listParams.find((params) => params.get("query") === "Build");
  expect(search?.has("independent_only")).toBe(false);
  expect(search?.has("coordinator_thread_id")).toBe(false);
});

it("expands the selected worker's Project Coordinator and allows manual collapse", async () => {
  scope = "project";
  targetRole = "coordinator";
  workerCount = 1;
  selectedWorker = worker;
  workers = [worker];
  window.history.replaceState(
    null,
    "",
    "/share/share-one/threads/thread-worker#key=guest-only",
  );
  render(<BrowserApp />);

  const link = await screen.findByRole("link", { name: /Build sidebar/ });
  expect(link.getAttribute("aria-current")).toBe("page");
  const toggle = screen.getByRole("button", {
    name: "Workers for Design together",
    expanded: true,
  });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("link", { name: /Build sidebar/ })).toBeNull();

  const owner = await screen.findByRole("link", {
    name: "Managed by Design together",
  });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(owner);
  expect(window.location.pathname).toBe("/share/share-one/threads/thread-one");
  await screen.findByRole("link", { name: /Build sidebar/ });
  expect(toggle.getAttribute("aria-expanded")).toBe("true");

  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("link", { name: /Build sidebar/ })).toBeNull();
});
