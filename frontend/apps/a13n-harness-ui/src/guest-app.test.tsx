// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
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
let permission = "view";
let scope = "thread";
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
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  paths.length = 0;
  sockets.length = 0;
  permission = "view";
  scope = "thread";
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
      constructor(url: URL) {
        sockets.push(String(url));
      }
      close() {}
      send() {}
    },
  );
  vi.spyOn(Realtime.prototype, "subscribe").mockImplementation(
    (subscription) => {
      expect(subscription.stream).toBe("focus");
      expect(subscription.root).toBe("thread-one");
      const close = () => {};
      close.restart = () => {};
      close.retry = () => {};
      return close;
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      paths.push(path);
      expect(window.location.hash).toBe("");
      expect(request.headers.get("Authorization")).toBe("Bearer guest-only");
      const prefix = "/api/shared/share-one";
      if (path === prefix + "/access")
        return json({
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
      if (path === prefix + "/threads")
        return json({ threads: [thread], total: 1 });
      if (path === prefix + "/threads/thread-one")
        return json({
          thread,
          available_actions: permission === "view" ? [] : ["run"],
          continuation_id: "saved",
        });
      if (path === prefix + "/threads/thread-one/transcript")
        return json({
          continuation_id: "saved",
          entries: [],
          turns: [],
          next_cursor: null,
        });
      if (path === prefix + "/threads/thread-one/work")
        return json({ tasks: { total: 0 }, notes: { total: 0 }, children: {} });
      if (path === prefix + "/threads/thread-one/inputs")
        return json({ turns: [], next_cursor: null });
      if (path === prefix + "/threads/thread-one/children")
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
  await screen.findByText("View-only link · Following this chat live.");
  expect(screen.queryByRole("textbox")).toBeNull();
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
  render(<BrowserApp />);
  await screen.findByRole("textbox", { name: "Shared prompt" });
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
  await screen.findByText("View-only link · Following this chat live.");
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
