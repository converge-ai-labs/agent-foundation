// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parse } from "yaml";
import { BrowserApp } from "./app";
import { Realtime } from "./transport/realtime";
import * as push from "./shell/push";
import { IDBFactory } from "fake-indexeddb";
import { onlineManager } from "@tanstack/react-query";

const status = {
  api_version: "1",
  version: "1.2.3rc2",
  app: { state: "ready", object_count: 0 },
  features: { page_presence: false },
  access: "api_key",
  host: "localhost",
};
const source = {
  relative_path: "agents/assistant.yaml",
  source_digest: "a",
  resource_kind: "agent",
  resource_ids: ["agent-assistant"],
  writable: true,
  content_available: true,
  generation_digest: "g",
  content:
    'schema_version: "1"\nkind: agent\nid: agent-assistant\nname: Assistant\nmodel: null\ninstructions: Original\n',
};
function json(body: unknown, code = 200) {
  return new Response(JSON.stringify(body), {
    status: code,
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(request: Request): Response | Promise<Response> {
  const path = decodeURIComponent(new URL(request.url).pathname);
  if (path === "/api/status") return json(status);
  if (path === "/api/threads/activity")
    return json({ rows: [], total: 0, next_cursor: null });
  if (path === "/api/setup")
    return json({
      needed: true,
      providers: [],
      agents: {},
      projects: {},
      project_paths: {},
      configuration_path: "/tmp/config.yaml",
      system_prompt: "Be helpful.",
    });
  if (path === "/api/models/choices")
    return json({
      connections: [
        {
          id: "codex",
          account: {
            provider: "codex",
            label: "Codex",
            login_methods: ["device", "browser"],
          },
        },
        {
          id: "grok-subscription",
          account: {
            provider: "grok",
            label: "Grok",
            login_methods: ["device", "browser"],
          },
        },
      ],
    });
  if (path === "/api/agents/agent-assistant/tool-proxy")
    return json({
      agent_id: "agent-assistant",
      tool_proxy: { groups: {} },
      sources: [],
    });
  if (path === "/api/selectors")
    return json({
      agents: [],
      environments: [],
      harness_plugins: [],
      environment_run_extensions: [],
      mcp_servers: [],
    });
  if (path === "/api/configuration/sources")
    return json({ generation_digest: "g", sources: [source] });
  if (path === "/api/configuration/sources/agents/assistant.yaml")
    return json(source);
  return json([]);
}
beforeEach(() => {
  vi.spyOn(Realtime.prototype, "subscribe").mockImplementation(
    (subscription) => {
      let active = true;
      const emit = () =>
        queueMicrotask(() => {
          if (active)
            subscription.receive({
              kind: "open",
              resume_cursor: "epoch:1",
              resumed: false,
            });
        });
      emit();
      const close = () => {
        active = false;
      };
      close.restart = emit;
      close.retry = emit;
      return close;
    },
  );
  vi.stubGlobal("indexedDB", new IDBFactory());
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
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  // jsdom has no layout or Web Animations API.
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  localStorage.clear();
  window.history.replaceState(null, "", "/");
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => Promise.resolve(fixture(request))),
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("observes Memory with the shared Thread viewer and scoped files without mounting an editor", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "retained-key");
  window.history.replaceState(null, "", "/memory");
  const reads: string[] = [];
  const thread = {
    thread_id: "memory-global",
    memory_scope: "global",
    title: "Global memory",
    created_at: "2026-09-01",
    updated_at: "2026-09-01",
    metadata_version: 1,
    archived: false,
    continuation_state: "selected",
    root_activity: { state: "inactive" },
    configuration: {
      version: 1,
      agent_source: { kind: "memory", id: "memory" },
      environment_profile_id: "environment-native",
    },
  };
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    const url = new URL(request.url);
    reads.push(url.pathname + url.search);
    expect(request.method).toBe("GET");
    if (url.pathname === "/api/status")
      return json({
        ...status,
        app: {
          ...status.app,
          memory_organization: {
            memory_enabled: true,
            availability: "disabled",
          },
        },
      });
    if (url.pathname === "/api/projects")
      return json([{ project_id: "project-main", name: "Main" }]);
    if (url.pathname === "/api/threads") {
      expect(url.searchParams.get("memory")).toBe("true");
      return json({
        threads: url.searchParams.has("project_id") ? [] : [thread],
        total: 1,
      });
    }
    if (url.pathname === "/api/threads/memory-global")
      return json({ thread, available_actions: [], continuation_id: "saved" });
    if (url.pathname === "/api/threads/memory-global/mcp/inputs")
      return json([]);
    if (url.pathname === "/api/threads/memory-global/inputs")
      return json({ turns: [], next_cursor: null });
    if (url.pathname.endsWith("/transcript"))
      return json({
        continuation_id: "saved",
        entries: [],
        turns: [],
        next_cursor: null,
      });
    if (url.pathname.endsWith("/children"))
      return json({ executions: [], next_cursor: null });
    if (url.pathname === "/api/memory/files")
      return json([{ path: "MEMORY.md" }]);
    if (url.pathname === "/api/memory/file")
      return json({
        path: "MEMORY.md",
        text: url.searchParams.has("project_id")
          ? "Project-only memory"
          : "Global-only memory",
      });
    return fixture(request);
  });
  render(<BrowserApp />);
  await screen.findByText("Global-only memory");
  await screen.findByRole("button", { name: "Inspect & usage" });
  await waitFor(() =>
    expect(
      vi
        .mocked(Realtime.prototype.subscribe)
        .mock.calls.some(
          ([channel]) =>
            channel.stream === "focus" && channel.root === "memory-global",
        ),
    ).toBe(true),
  );
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  expect(
    reads.some((path) =>
      /memory-global\/draft|configuration-preview/.test(path),
    ),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Inspect & usage" }));
  await screen.findByRole("dialog");
  expect(screen.getByText("Inspect")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("link", { name: "Main" }));
  await screen.findByText("Project-only memory");
  await screen.findByText("No organization history yet");
  expect(screen.queryByText("Global-only memory")).toBeNull();
  expect(screen.queryByRole("button", { name: "Inspect & usage" })).toBeNull();
  expect(reads.some((path) => path.includes("project_id=project-main"))).toBe(
    true,
  );
});

it("keeps a public startup shell instead of flashing login while checking a retained key", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "retained-key");
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(path);
      if (path === "/api/status") await delayed;
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  expect(screen.getByText("Connecting to Harness UI…")).toBeTruthy();
  expect(screen.queryByLabelText("API key")).toBeNull();
  expect(screen.queryByLabelText("Name")).toBeNull();
  expect(requests).toEqual(["/api/status"]);
  release();
  await screen.findByLabelText("Name");
  expect(
    screen.queryByRole("heading", { name: "Log in to Harness UI" }),
  ).toBeNull();
  expect(window.location.pathname + window.location.search).toBe(
    "/settings/source?path=agents%2Fassistant.yaml",
  );
});

it("distinguishes an unavailable listener from rejected credentials and retries the same key", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "retained-key");
  let unavailable = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (unavailable) throw new TypeError("Network unavailable");
      expect(request.headers.get("Authorization")).toBe("Bearer retained-key");
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  await screen.findByText(
    "Unable to reach the server. Check your connection and try again.",
  );
  expect(screen.queryByLabelText("API key")).toBeNull();
  unavailable = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
  await screen.findByText("1.2.3rc2");
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBe("retained-key");
});

it("consumes the fragment before all network requests and retains only an accepted key", async () => {
  window.history.replaceState(null, "", "/#api_key=generated%2Bkey&keep=yes");
  const fetcher = vi.fn((request: Request) => {
    expect(window.location.hash).toBe("#keep=yes");
    expect(request.headers.get("Authorization")).toBe("Bearer generated+key");
    expect(request.url).not.toContain("generated");
    return Promise.resolve(fixture(request));
  });
  vi.stubGlobal("fetch", fetcher);
  render(<BrowserApp />);
  await screen.findByText("1.2.3rc2");
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBe("generated+key");
  expect(document.body.textContent).not.toContain("generated+key");
});

it("accepts a replacement key after authentication failure without replaying mutations", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "old-key");
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) =>
      Promise.resolve(
        request.headers.get("Authorization") === "Bearer old-key"
          ? json({}, 401)
          : fixture(request),
      ),
    ),
  );
  render(<BrowserApp />);
  await screen.findByText(
    "Access expired. Enter the API key printed by this server.",
  );
  fireEvent.change(screen.getByLabelText("Instance API key"), {
    target: { value: "new-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  await screen.findByText("1.2.3rc2");
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBe("new-key");
});

it("supports explicit server bypass without inventing a frontend version", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      expect(request.headers.has("Authorization")).toBe(false);
      return Promise.resolve(
        decodeURIComponent(new URL(request.url).pathname) === "/api/status"
          ? json({ ...status, version: "0.0.0", access: "dangerous_bypass" })
          : fixture(request),
      );
    }),
  );
  render(<BrowserApp />);
  await screen.findByText("0.0.0");
  expect(screen.getByText(/Instance authentication is disabled/)).toBeTruthy();
});

it("rejects incompatible status without caching access", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({})));
  render(<BrowserApp />);
  await screen.findByText(
    "This server returned an incompatible status response.",
  );
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBeNull();
});

it("forgets the retained credential and closes protected views", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "remembered");
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) =>
      Promise.resolve(
        request.headers.has("Authorization") ? fixture(request) : json({}, 401),
      ),
    ),
  );
  render(<BrowserApp />);
  await screen.findByText("1.2.3rc2");
  fireEvent.click(screen.getByRole("button", { name: "Log out" }));
  await screen.findByText(
    "Access expired. Enter the API key printed by this server.",
  );
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBeNull();
  expect(
    screen.queryByRole("heading", { name: "What would you like to build?" }),
  ).toBeNull();
});

it("keeps logout cleanup authenticated and warns when background delivery cannot be disabled", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "remembered");
  const cleanupPush = vi
    .spyOn(push, "disablePush")
    .mockImplementation(async (transport) => {
      if (!transport) return;
      // A real request proves logout has not closed the transport yet.
      const response = await transport.fetch(
        "/api/push/subscriptions/subscription-one",
        { method: "DELETE" },
      );
      expect(response.status).toBe(204);
      throw new Error("Browser and server cleanup could not be confirmed");
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      if (request.method === "DELETE") {
        expect(request.headers.get("Authorization")).toBe("Bearer remembered");
        expect(request.signal.aborted).toBe(false);
        return new Response(null, { status: 204 });
      }
      return request.headers.has("Authorization")
        ? fixture(request)
        : json({}, 401);
    }),
  );
  render(<BrowserApp />);
  await screen.findByText("1.2.3rc2");
  fireEvent.click(screen.getByRole("button", { name: "Log out" }));
  await screen.findByRole("heading", { name: "Log in to Harness UI" });
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    expect.stringContaining(
      "Block notifications in this site's browser settings",
    ),
  );
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBeNull();
  expect(cleanupPush).toHaveBeenCalled();
});

it("retains dirty source fields through navigation and external invalidation", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  render(<BrowserApp />);
  const name = await screen.findByLabelText("Name");
  fireEvent.change(name, { target: { value: "My draft" } });
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  await screen.findByRole("heading", { name: "Projects" });
  fireEvent.click(screen.getByRole("link", { name: "Advanced" }));
  fireEvent.click(await screen.findByRole("link", { name: /agent-assistant/ }));
  await waitFor(() =>
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "My draft",
    ),
  );
  expect(screen.getByText(/Unsaved changes/)).toBeTruthy();
});

it("does not pretend unavailable MCP source is an editable empty document", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=mcp%2Fprivate.json",
  );
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) =>
      Promise.resolve(
        decodeURIComponent(new URL(request.url).pathname) ===
          "/api/configuration/sources/mcp/private.json"
          ? json({
              ...source,
              relative_path: "mcp/private.json",
              resource_kind: "mcp_server",
              resource_ids: ["mcp-one", "mcp-two"],
              content_available: false,
              content: null,
            })
          : fixture(request),
      ),
    ),
  );
  render(<BrowserApp />);
  await screen.findByRole("heading", {
    name: "Saved configuration is hidden",
  });
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Replace configuration file" }),
  ).toBeTruthy();
  expect(screen.getByText(/mcp-one, mcp-two/)).toBeTruthy();
});

it("reopens an unpublished resource draft without treating it as a missing server source", async () => {
  window.history.replaceState(null, "", "/settings/resources");
  render(<BrowserApp />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Add configuration" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Unpublished model" },
  });
  fireEvent.click(screen.getByRole("link", { name: "Advanced" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Add configuration" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() =>
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Unpublished model",
    ),
  );
  expect(screen.getByRole("button", { name: "Create model" })).toBeTruthy();
  expect(window.location.search).toContain("new=1");
});

it("keeps incomplete existing configuration in focused repair without initialization", async () => {
  window.history.replaceState(null, "", "/setup");
  render(<BrowserApp />);
  await screen.findByRole("heading", { name: "Setup & readiness" });
  expect(
    screen.getByRole("link", { name: "Repair agent connection" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Save and start/ })).toBeNull();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([request]) => (request as Request).method === "GET"),
  ).toBe(true);
});

it("attempts offline publication immediately and does not queue it for reconnect", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  let writes = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      if (request.method === "PUT") {
        writes++;
        return Promise.reject(new TypeError("Offline request failed"));
      }
      return Promise.resolve(fixture(request));
    }),
  );
  render(<BrowserApp />);
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Offline edit" },
  });
  try {
    onlineManager.setOnline(false);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByText(
      "Unable to reach the server. Check your connection and try again.",
    );
    expect(writes).toBe(1);
    onlineManager.setOnline(true);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(writes).toBe(1);
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Offline edit",
    );
  } finally {
    onlineManager.setOnline(true);
  }
});

it("keeps a dirty draft when summary invalidation discovers an external publication", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  let accepted = source;
  let summary!: (frame: unknown) => void;
  vi.mocked(Realtime.prototype.subscribe).mockImplementation((subscription) => {
    summary = subscription.receive;
    const close = () => {};
    close.restart = () => {};
    close.retry = () => {};
    return close;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/configuration/sources/agents/assistant.yaml")
        return Promise.resolve(json(accepted));
      return Promise.resolve(fixture(request));
    }),
  );
  render(<BrowserApp />);
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Retained local name" },
  });
  accepted = {
    ...source,
    source_digest: "new-generation",
    content: source.content.replace("Assistant", "Other author"),
  };
  summary({
    kind: "invalidation",
    resume_cursor: "epoch:2",
    event: { kind: "configuration" },
  });
  await screen.findByText(/This configuration changed elsewhere/);
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
    "Retained local name",
  );
});

it("retains source edits across rejected access and reauthentication", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  let expire = false;
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) =>
      Promise.resolve(expire ? json({}, 401) : fixture(request)),
    ),
  );
  render(<BrowserApp />);
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Before access expired" },
  });
  expire = true;
  fireEvent.click(
    screen.getByText("Advanced configuration", { selector: "summary" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Check configuration" }));
  await screen.findByText(
    "Access expired. Enter the API key printed by this server.",
  );
  expect(screen.getByText(/unsaved/i)).toBeTruthy();
  expire = false;
  fireEvent.change(screen.getByLabelText("Instance API key"), {
    target: { value: "replacement" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  expect(
    ((await screen.findByLabelText("Name")) as HTMLInputElement).value,
  ).toBe("Before access expired");
});

it("collapses and restores desktop navigation without losing expanded Projects", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "retained-key");
  vi.mocked(fetch).mockImplementation(async (input) => {
    const request = input as Request;
    if (new URL(request.url).pathname === "/api/projects")
      return json([{ project_id: "project-main", name: "Main" }]);
    return fixture(request);
  });
  render(<BrowserApp />);
  const sidebar = await screen.findByRole("complementary", {
    name: "Workbench navigation",
  });
  const project = await within(sidebar).findByRole("button", {
    name: "Main",
    expanded: false,
  });
  await userEvent.click(project);
  await userEvent.click(
    within(sidebar).getByRole("button", { name: "Collapse navigation" }),
  );
  const expand = await screen.findByRole("button", {
    name: "Expand navigation",
  });
  expect(localStorage.getItem("a13n-harness-ui.sidebar")).toBe("collapsed");
  await waitFor(() => expect(document.activeElement).toBe(expand));
  await userEvent.keyboard("{Enter}");
  await waitFor(() =>
    expect(document.activeElement).toBe(
      within(sidebar).getByRole("button", { name: "Collapse navigation" }),
    ),
  );
  expect(
    within(sidebar).getByRole("button", { name: "Main", expanded: true }),
  ).toBe(project);
  expect(localStorage.getItem("a13n-harness-ui.sidebar")).toBe("expanded");
});

it("works without browser storage", async () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("Storage disabled");
  });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("Storage disabled");
  });
  const remove = vi
    .spyOn(Storage.prototype, "removeItem")
    .mockImplementation(() => {
      throw new Error("Storage disabled");
    });
  try {
    render(<BrowserApp />);
    await screen.findByRole("heading", {
      name: "What would you like to build?",
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Switch to dark theme" }),
    );
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  } finally {
    get.mockRestore();
    set.mockRestore();
    remove.mockRestore();
  }
});

it("retains login across navigation, requires switch authorization and respects completed cancellation", async () => {
  window.history.replaceState(null, "", "/settings/accounts");
  const starts: Record<string, unknown>[] = [];
  let succeeded = false;
  let login = {
    session_id: "login-one",
    provider: "codex",
    method: "device",
    state: "failed",
    error_code: "account_switch_confirmation_required",
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.startsWith("/api/auth/accounts/"))
        return json({
          provider: path.split("/").at(-1),
          usable: succeeded,
          source: "file",
          expiry: "not_applicable",
          required_action: "none",
        });
      if (path === "/api/auth/logins") {
        starts.push(await request.json());
        if (starts.length > 1)
          login = { ...login, state: "waiting", error_code: "" };
        return json(login);
      }
      if (path === "/api/auth/logins/login-one") {
        if (request.method === "DELETE") {
          succeeded = true;
          login = { ...login, state: "succeeded" };
        }
        return json(login);
      }
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  await screen.findByRole("heading", { name: "Accounts & API keys" });
  fireEvent.click(
    (await screen.findAllByRole("button", { name: "Connect account" }))[0],
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Switch account…" }),
  );
  expect(starts).toHaveLength(1);
  expect(starts[0].allow_account_switch).toBe(false);
  fireEvent.click(
    screen.getByRole("button", { name: "Allow switch and log in" }),
  );
  await screen.findByRole("button", { name: "Cancel login" });
  fireEvent.click(screen.getByRole("link", { name: "Projects" }));
  await screen.findByRole("heading", { name: "Projects" });
  fireEvent.click(screen.getByRole("link", { name: "Accounts & API keys" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel login" }));
  await screen.findByText("Login: succeeded");
  expect(starts[1].allow_account_switch).toBe(true);
  expect(screen.queryByText("Login: cancelled")).toBeNull();
});

it.each([
  ["kind: project\nroots: [null]\n", /Repair the host root list/],
  [
    "kind: agent\ncapabilities: null\n",
    /Repair the capability, subagent or tool list/,
  ],
])(
  "keeps incomplete advanced YAML repairable: %s",
  async (content, message) => {
    window.history.replaceState(
      null,
      "",
      "/settings/source?path=agents%2Fassistant.yaml",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn((request: Request) =>
        Promise.resolve(
          decodeURIComponent(new URL(request.url).pathname) ===
            "/api/configuration/sources/agents/assistant.yaml"
            ? json({ ...source, content })
            : fixture(request),
        ),
      ),
    );
    render(<BrowserApp />);
    await screen.findByText(message);
    fireEvent.click(
      screen.getByText("Advanced configuration", { selector: "summary" }),
    );
    expect(screen.getByText("Configuration file")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Check configuration" }),
    ).toBeTruthy();
  },
);

it("adds a configurable capability to the chosen agent and preserves unrelated configuration on save", async () => {
  window.history.replaceState(null, "", "/settings/capabilities");
  const original =
    source.content +
    "capabilities:\n  - capability: existing\n    configuration: {keep: true}\ncustom_options: {retain: 42}\n";
  let saved = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/selectors")
        return json({
          agents: [{ agent_id: "agent-assistant", name: "Assistant" }],
          environments: [],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
        });
      if (path === "/api/catalog")
        return json([
          {
            kind: "capability",
            key: "available",
            source: "pydantic",
            configurable: true,
          },
          {
            kind: "capability",
            key: "ambiguous",
            source: "installed",
            distribution_name: "example-capabilities",
            configurable: false,
          },
        ]);
      if (path === "/api/configuration/sources/agents/assistant.yaml") {
        if (request.method === "PUT") {
          saved = (await request.json()).content;
          return json({ source_digest: "saved" });
        }
        return json({ ...source, content: original });
      }
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  const user = userEvent.setup();
  const catalog = await screen.findByRole("region", {
    name: "Installed capabilities",
  });
  expect(await within(catalog).findByText("available")).toBeTruthy();
  expect(within(catalog).getByText("Built-in")).toBeTruthy();
  expect(within(catalog).queryByText("pydantic")).toBeNull();
  expect(within(catalog).getByText("example-capabilities")).toBeTruthy();
  expect(within(catalog).getByText("Unavailable to configure")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  await user.click(
    await screen.findByRole("link", { name: "Edit agent capabilities" }),
  );
  expect(window.location.search).toBe("?path=agents%2Fassistant.yaml");
  fireEvent.change(await screen.findByLabelText("Instructions"), {
    target: { value: "Unfinished instructions" },
  });
  await user.click(screen.getByRole("link", { name: "Capabilities" }));
  await user.click(
    await screen.findByRole("link", { name: "Edit agent capabilities" }),
  );
  expect(
    ((await screen.findByLabelText("Instructions")) as HTMLTextAreaElement)
      .value,
  ).toBe("Unfinished instructions");
  await user.click(
    await screen.findByRole("combobox", { name: "Add capability" }),
  );
  expect(screen.queryByRole("option", { name: "ambiguous" })).toBeNull();
  await user.click(await screen.findByRole("option", { name: "available" }));
  expect(saved).toBe("");
  expect(
    screen.getByRole("button", { name: "Remove capability available" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(saved).toContain("capability: available"));
  expect(saved).toContain("keep: true");
  expect(saved).toContain("retain: 42");
  expect(saved).toContain("instructions: Unfinished instructions");
});

it("opens the owning agent editor to repair malformed capability YAML", async () => {
  window.history.replaceState(null, "", "/settings/capabilities");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/selectors")
        return json({
          agents: [{ agent_id: "agent-assistant", name: "Assistant" }],
          environments: [],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
        });
      if (path === "/api/configuration/sources/agents/assistant.yaml")
        return json({ ...source, content: "capabilities: [" });
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  fireEvent.click(
    await screen.findByRole("link", { name: "Edit agent capabilities" }),
  );
  await screen.findByText(/Structured fields are unavailable/);
  fireEvent.click(
    screen.getByText("Advanced configuration", { selector: "summary" }),
  );
  expect(screen.getByText("Configuration file")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Check configuration" }),
  ).toBeTruthy();
});

it.each(["read-only", "missing"])(
  "keeps the capability catalog available with a %s agent source",
  async (availability) => {
    window.history.replaceState(null, "", "/settings/capabilities");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = decodeURIComponent(new URL(request.url).pathname);
        if (path === "/api/selectors")
          return json({
            agents: [{ agent_id: "agent-assistant", name: "Assistant" }],
          });
        if (path === "/api/catalog")
          return json([
            { kind: "capability", key: "available", configurable: true },
          ]);
        if (path === "/api/configuration/sources")
          return json({
            sources:
              availability === "missing"
                ? []
                : [{ ...source, writable: false }],
          });
        if (path === "/api/configuration/sources/agents/assistant.yaml")
          return json({ ...source, writable: false });
        return fixture(request);
      }),
    );
    render(<BrowserApp />);
    expect(await screen.findByText("Available to agents")).toBeTruthy();
    if (availability === "missing") {
      await screen.findByText(
        "This agent has no configuration file available here.",
      );
    } else {
      fireEvent.click(
        await screen.findByRole("link", { name: "View agent configuration" }),
      );
      expect(
        (await screen.findByLabelText("YAML source")).getAttribute(
          "contenteditable",
        ),
      ).toBe("false");
    }
    expect(
      screen.queryByRole("link", { name: "Edit agent capabilities" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.every(([request]) => (request as Request).method === "GET"),
    ).toBe(true);
  },
);

it.each([false, true])(
  "keeps capability discovery independent from agent availability (load failure: %s)",
  async (failed) => {
    window.history.replaceState(null, "", "/settings/capabilities");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (request: Request) => {
        const path = new URL(request.url).pathname;
        if (path === "/api/selectors" && failed)
          return json(
            {
              error: { code: "unavailable", message: "Agent list unavailable" },
            },
            503,
          );
        if (path === "/api/catalog")
          return json([
            { kind: "capability", key: "available", configurable: true },
          ]);
        return fixture(request);
      }),
    );
    render(<BrowserApp />);
    expect(await screen.findByText("Available to agents")).toBeTruthy();
    if (failed) {
      await screen.findByText("Agent list unavailable");
      expect(screen.queryByRole("button", { name: "Add agent" })).toBeNull();
    } else {
      expect(
        await screen.findByRole("button", { name: "Add agent" }),
      ).toBeTruthy();
    }
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  },
);

it("reopens a one-click agent draft from its own settings list without writing or fetching a nonexistent file", async () => {
  window.history.replaceState(null, "", "/settings/agents");
  const fetcher = vi.mocked(fetch);
  render(<BrowserApp />);
  fireEvent.click(await screen.findByRole("button", { name: "Add agent" }));
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Unfinished assistant" },
  });
  const draftPath = new URLSearchParams(window.location.search).get("path")!;
  expect(
    screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.getByRole("link", { name: "Advanced" }).getAttribute("aria-current"),
  ).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Agents" }));
  fireEvent.click(
    await screen.findByRole("link", {
      name: /Unfinished assistant.*Unsaved draft/,
    }),
  );
  expect(
    ((await screen.findByLabelText("Name")) as HTMLInputElement).value,
  ).toBe("Unfinished assistant");
  expect(window.location.search).toContain("new=1");
  expect(
    screen.getByRole("link", { name: "Agents" }).getAttribute("aria-current"),
  ).toBe("page");
  expect(
    fetcher.mock.calls.some(
      ([request]) => (request as Request).method === "PUT",
    ),
  ).toBe(false);
  expect(
    fetcher.mock.calls.some(([request]) =>
      decodeURIComponent((request as Request).url).includes(
        `/sources/${draftPath}`,
      ),
    ),
  ).toBe(false);
});

it("shows built-in environments as read-only and does not offer configuration for an unavailable provider", async () => {
  window.history.replaceState(null, "", "/settings/environments");
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/selectors")
        return json({
          agents: [],
          environments: [
            {
              profile_id: "environment-native",
              name: "Full Control",
              mode: "full-control",
              release_owned: true,
            },
          ],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
        });
      if (path === "/api/catalog")
        return json([
          {
            kind: "environment_provider",
            key: "unavailable-provider",
            configurable: false,
          },
        ]);
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  await screen.findByText("Built in");
  const profiles = screen.getByRole("region", {
    name: "Local execution profiles",
  });
  expect(
    within(profiles).getByRole("button", { name: "Add local profile" }),
  ).toBeTruthy();
  const providers = screen.getByText("Installed environment providers", {
    selector: "summary",
  });
  expect(providers.closest("details")?.open).toBe(false);
  fireEvent.click(providers);
  await screen.findByText("Unavailable to configure");
  expect(screen.queryByRole("link", { name: /Full Control/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Configure" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Terminal" })).toBeNull();
});

it("edits project defaults directly while preserving unknown project fields", async () => {
  window.history.replaceState(null, "", "/projects/project-test");
  const projectSource = {
    ...source,
    relative_path: "projects/test.yaml",
    resource_kind: "project",
    resource_ids: ["project-test"],
    content:
      'schema_version: "1"\nkind: project\nid: project-test\nname: Test project\nroots: [{path: /test}]\ndefaults: {}\ncustom_options: {retain: true}\n',
  };
  let saved = "";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/projects")
        return json([
          {
            project_id: "project-test",
            name: "Test project",
            roots: ["/test"],
          },
        ]);
      if (path === "/api/selectors")
        return json({
          agents: [{ agent_id: "agent-assistant", name: "Assistant" }],
          environments: [],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
        });
      if (path === "/api/configuration/sources")
        return json({ generation_digest: "g", sources: [projectSource] });
      if (path === "/api/configuration/sources/projects/test.yaml") {
        if (request.method === "PUT") {
          saved = (await request.json()).content;
          return json({ source_digest: "saved" });
        }
        return json(projectSource);
      }
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Default agent" }),
  );
  await user.click(await screen.findByRole("option", { name: "Assistant" }));
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(saved).toContain("agent: agent-assistant"));
  expect(saved).toContain("retain: true");
  expect(saved).toContain("/test");
});

it("persists generated and edited collaboration names across visits and rejects empty replacements", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "test-key");
  let component = render(<BrowserApp />);
  await screen.findByRole("button", {
    name: /^Your collaboration name: Guest /,
  });
  const generated = localStorage.getItem("a13n-harness-ui.display-name");
  expect(generated).toMatch(/^Guest [a-f0-9]{6}$/);
  component.unmount();
  component = render(<BrowserApp />);
  fireEvent.click(
    await screen.findByRole("button", {
      name: `Your collaboration name: ${generated}`,
    }),
  );
  const input = await screen.findByLabelText("Your display name");
  fireEvent.change(input, { target: { value: "   " } });
  expect(
    screen.getByRole("button", { name: "Save name" }).hasAttribute("disabled"),
  ).toBe(true);
  expect(localStorage.getItem("a13n-harness-ui.display-name")).toBe(generated);
  fireEvent.change(input, { target: { value: "  Alex  " } });
  fireEvent.click(screen.getByRole("button", { name: "Save name" }));
  await screen.findByRole("button", { name: "Your collaboration name: Alex" });
  expect(localStorage.getItem("a13n-harness-ui.display-name")).toBe("Alex");
  component.unmount();
  render(<BrowserApp />);
  await screen.findByRole("button", { name: "Your collaboration name: Alex" });
});

it("configures Sidekick in General without changing defaults or starting conversations", async () => {
  window.history.replaceState(null, "", "/settings");
  const rootSource = {
    ...source,
    relative_path: "custom-root.yaml",
    resource_kind: "root",
    resource_ids: [],
  };
  let content =
    '# Keep this comment\nschema_version: "1"\ndefaults: {agent: agent-assistant}\nprocess: {log_level: DEBUG}\nwebui: {custom: keep}\n';
  let writes = 0;
  let submissions = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/configuration/sources")
        return json({
          generation_digest: "g",
          sources: [
            rootSource,
            {
              ...source,
              relative_path: "models/worker.yaml",
              resource_kind: "model",
              resource_ids: ["model-worker"],
            },
          ],
        });
      if (path === "/api/configuration/sources/custom-root.yaml") {
        if (request.method === "PUT") {
          content = (await request.json()).content;
          writes += 1;
          return json({ source_digest: `saved-${writes}` });
        }
        return json({
          ...rootSource,
          source_digest: `saved-${writes}`,
          content,
        });
      }
      if (path === "/api/selectors")
        return json({
          agents: [
            {
              agent_id: "agent-assistant",
              name: "Assistant",
              model_id: "model-main",
            },
            {
              agent_id: "agent-worker",
              name: "Worker",
              model_id: "model-worker",
            },
            { agent_id: "agent-empty", name: "No model", model_id: null },
          ],
          models: [
            {
              model_id: "model-worker",
              name: "Worker model",
              route: "openai-responses:custom",
            },
          ],
          environments: [],
          harness_plugins: [],
          environment_run_extensions: [],
          mcp_servers: [],
        });
      if (request.method === "POST" && path.startsWith("/api/threads"))
        submissions += 1;
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  const user = userEvent.setup();
  expect(
    (await screen.findByRole("combobox", { name: "Sidekick" })).textContent,
  ).toContain("Enabled");
  expect(
    screen.getByRole("combobox", { name: "Sidekick agent" }).textContent,
  ).toContain("Inherit current agent");
  await user.click(screen.getByRole("combobox", { name: "Sidekick model" }));
  await user.click(await screen.findByRole("option", { name: "Worker model" }));
  expect(parse(content).webui.sidekick).toBeUndefined();
  await user.click(screen.getByRole("combobox", { name: "Sidekick agent" }));
  await user.click(await screen.findByRole("option", { name: "Worker" }));
  expect(writes).toBe(0);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(writes).toBe(1));
  expect(parse(content).webui.sidekick).toEqual({
    agent: "agent-worker",
    model: "model-worker",
  });
  expect(content).toContain("agent-assistant");
  expect(content).toContain("Keep this comment");
  expect(content).toContain("DEBUG");
  expect(content).toContain("custom: keep");
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Sidekick agent" }).textContent,
    ).toContain("Worker"),
  );
  await user.click(screen.getByRole("combobox", { name: "Sidekick" }));
  await user.click(await screen.findByRole("option", { name: "Disabled" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(writes).toBe(2));
  expect(parse(content).webui.sidekick).toBeNull();
  expect(content).toContain("custom: keep");
  expect(content).toContain("agent-assistant");
  expect(submissions).toBe(0);
});

it("persists the initial collaboration color and restores explicit changes", async () => {
  localStorage.setItem("a13n-harness-ui.api-key", "test-key");
  const random = vi.spyOn(Math, "random").mockReturnValue(0.5);
  try {
    const component = render(<BrowserApp />);
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: /^Your collaboration name:/,
      }),
    );
    const color = await screen.findByRole("combobox", { name: "Your color" });
    expect(color.textContent).toBe("Purple");
    expect(localStorage.getItem("a13n-harness-ui.color")).toBe("#7c3aed");
    component.unmount();

    random.mockReturnValue(0.99);
    const reopened = render(<BrowserApp />);
    await user.click(
      await screen.findByRole("button", {
        name: /^Your collaboration name:/,
      }),
    );
    const remembered = await screen.findByRole("combobox", {
      name: "Your color",
    });
    expect(remembered.textContent).toBe("Purple");
    await user.click(remembered);
    await user.click(await screen.findByRole("option", { name: "Green" }));
    expect(remembered.textContent).toBe("Green");
    await waitFor(() =>
      expect(localStorage.getItem("a13n-harness-ui.color")).toBe("#059669"),
    );
    reopened.unmount();

    render(<BrowserApp />);
    await user.click(
      await screen.findByRole("button", {
        name: /^Your collaboration name:/,
      }),
    );
    expect(
      (await screen.findByRole("combobox", { name: "Your color" })).textContent,
    ).toBe("Green");
  } finally {
    random.mockRestore();
  }
});

it("keeps destructive configuration actions secondary and requires named confirmation", async () => {
  window.history.replaceState(
    null,
    "",
    "/settings/source?path=agents%2Fassistant.yaml",
  );
  render(<BrowserApp />);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "More configuration actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Delete configuration" }),
  );
  const dialog = await screen.findByRole("dialog", {
    name: "Delete this configuration?",
  });
  expect(dialog.textContent).toContain("agents/assistant.yaml");
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([request]) => (request as Request).method === "DELETE"),
  ).toBe(false);
});

it("shows one connection notice for server loss and reconciles on retry without replaying writes", async () => {
  let unavailable = true;
  let summaryRequests = 0;
  vi.mocked(Realtime.prototype.subscribe).mockImplementation((subscription) => {
    let active = true;
    const emit = () => {
      summaryRequests++;
      queueMicrotask(() => {
        if (!active) return;
        if (unavailable) subscription.state("Reconnecting");
        else
          subscription.receive({
            kind: "open",
            resume_cursor: "restored",
            resumed: false,
          });
      });
    };
    emit();
    const close = () => {
      active = false;
    };
    close.restart = emit;
    close.retry = emit;
    return close;
  });
  const fetcher = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/status") return json(status);
    if (unavailable) throw new TypeError("Failed to fetch");
    return fixture(request);
  });
  vi.stubGlobal("fetch", fetcher);
  render(<BrowserApp />);
  await screen.findByRole("status", { name: "Server connection" });
  await waitFor(() => expect(screen.queryAllByRole("alert")).toHaveLength(0));
  expect(screen.getAllByText("Connection interrupted")).toHaveLength(1);
  expect(screen.queryByText("Failed to fetch")).toBeNull();
  expect(screen.queryByText("0 online")).toBeNull();
  expect(
    screen.getByRole("heading", { name: "What would you like to build?" }),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();

  const attempts = summaryRequests;
  fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
  await waitFor(() => expect(summaryRequests).toBeGreaterThan(attempts));
  expect(
    screen.getByRole("status", { name: "Server connection" }),
  ).toBeTruthy();

  unavailable = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry now" }));
  await waitFor(() => {
    expect(
      screen.queryByRole("status", { name: "Server connection" }),
    ).toBeNull();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });
  expect(
    fetcher.mock.calls.every(
      ([request]) =>
        request.method === "GET" ||
        (request.method === "POST" &&
          new URL(request.url).pathname ===
            "/api/threads/configuration-preview"),
    ),
  ).toBe(true);
});

it("synchronizes quick rename with the mounted clean Project editor before saving other fields", async () => {
  window.history.replaceState(null, "", "/projects/project-test");
  const projectSource = {
    ...source,
    relative_path: "projects/test.yaml",
    resource_kind: "project",
    resource_ids: ["project-test"],
    content:
      'schema_version: "1"\nkind: project\nid: project-test\nname: Test project\nroots: [{path: /test}]\ndefaults: {}\n',
  };
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/projects")
        return json([
          {
            project_id: "project-test",
            name: parse(projectSource.content).name,
            roots: ["/test"],
          },
        ]);
      if (path === "/api/configuration/sources")
        return json({ generation_digest: "g", sources: [projectSource] });
      if (path === "/api/configuration/sources/projects/test.yaml") {
        if (request.method === "PUT") {
          projectSource.content = (await request.json()).content;
          writes.push(projectSource.content);
          projectSource.source_digest = `saved-${writes.length}`;
          return json({ source_digest: projectSource.source_digest });
        }
        return json(projectSource);
      }
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  const user = userEvent.setup();
  const nameField = await screen.findByRole("textbox", {
    name: "Name",
  });
  expect((nameField as HTMLInputElement).value).toBe("Test project");
  await user.click(
    screen.getByRole("button", { name: "Actions for Test project" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Rename project" }),
  );
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Project name" }),
    { target: { value: "Renamed" } },
  );
  await user.click(screen.getByRole("button", { name: "Save name" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() =>
    expect((nameField as HTMLInputElement).value).toBe("Renamed"),
  );
  expect(
    screen
      .getByRole("button", { name: "Save changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.change(screen.getByRole("textbox", { name: "Server directory" }), {
    target: { value: "/changed" },
  });
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(parse(writes[1])).toMatchObject({
    id: "project-test",
    name: "Renamed",
    roots: [{ path: "/changed" }],
  });
});
