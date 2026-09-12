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
  if (path === "/api/events")
    return new Promise((_resolve, reject) =>
      request.signal.addEventListener(
        "abort",
        () => reject(new DOMException("Aborted", "AbortError")),
        { once: true },
      ),
    );
  return json([]);
}
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
  }));
  // jsdom has no layout; real CodeMirror geometry is covered by browser QA.
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
  vi.unstubAllGlobals();
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
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "new-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Forget API key" }));
  await screen.findByText(
    "Access expired. Enter the API key printed by this server.",
  );
  expect(localStorage.getItem("a13n-harness-ui.api-key")).toBeNull();
  expect(screen.queryByRole("heading", { name: "Your workbench" })).toBeNull();
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
  fireEvent.click(screen.getByRole("link", { name: "Resources" }));
  fireEvent.click(await screen.findByRole("link", { name: /agent-assistant/ }));
  await waitFor(() =>
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "My draft",
    ),
  );
  expect(screen.getByText(/Unsaved draft/)).toBeTruthy();
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
    name: "Source content is not available",
  });
  expect(screen.queryByRole("button", { name: "Publish" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Start explicit complete replacement" }),
  ).toBeTruthy();
  expect(screen.getByText(/mcp-one, mcp-two/)).toBeTruthy();
});

it("reopens an unpublished resource draft without treating it as a missing server source", async () => {
  window.history.replaceState(null, "", "/settings/resources");
  render(<BrowserApp />);
  fireEvent.click(await screen.findByRole("button", { name: "New resource" }));
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Unpublished model" },
  });
  fireEvent.click(screen.getByRole("link", { name: "Resources" }));
  fireEvent.click(await screen.findByRole("button", { name: "New resource" }));
  fireEvent.click(screen.getByRole("button", { name: "Create draft" }));
  await waitFor(() =>
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe(
      "Unpublished model",
    ),
  );
  expect(screen.getByRole("button", { name: "Publish" })).toBeTruthy();
  expect(window.location.search).toContain("new=1");
});

it("submits only additional instructions and consumes setup preview before an uncertain publication", async () => {
  window.history.replaceState(null, "", "/setup");
  const writes: Record<string, unknown>[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/setup/preview") {
        const body = await request.json();
        writes.push(body);
        return json({ files: {}, preserved_paths: [], project_paths: [] });
      }
      if (path === "/api/setup/apply") throw new TypeError("Connection lost");
      return fixture(request);
    }),
  );
  render(<BrowserApp />);
  expect(
    (
      (await screen.findByLabelText(
        "Additional agent instructions",
      )) as HTMLTextAreaElement
    ).value,
  ).toBe("");
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Preview files",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Preview files" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Publish setup",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  expect(writes[0].instructions).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Publish setup" }));
  await screen.findByText("Connection lost");
  expect(
    (screen.getByRole("button", { name: "Publish setup" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(screen.getByText(/Publication may have changed files/)).toBeTruthy();
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
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await screen.findByText("Offline request failed");
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
  let summary!: ReadableStreamDefaultController<Uint8Array>;
  vi.stubGlobal(
    "fetch",
    vi.fn((request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (path === "/api/events")
        return Promise.resolve(
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                summary = controller;
              },
            }),
          ),
        );
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
  summary.enqueue(
    new TextEncoder().encode(
      'data: {"kind":"invalidation","resume_cursor":"epoch:2","event":{"kind":"configuration"}}\n\n',
    ),
  );
  await screen.findByText(/The accepted source changed elsewhere/);
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
  fireEvent.click(screen.getByRole("button", { name: "Validate" }));
  await screen.findByText(
    "Access expired. Enter the API key printed by this server.",
  );
  expect(screen.getByText(/Local resource drafts are retained/)).toBeTruthy();
  expire = false;
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "replacement" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
  expect(
    ((await screen.findByLabelText("Name")) as HTMLInputElement).value,
  ).toBe("Before access expired");
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
    await screen.findByRole("heading", { name: "Your workbench" });
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
  await screen.findByRole("heading", { name: "Provider accounts" });
  fireEvent.click(screen.getAllByRole("button", { name: "Log in" })[0]);
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
  fireEvent.click(screen.getByRole("link", { name: "Provider accounts" }));
  fireEvent.click(await screen.findByRole("button", { name: "Cancel login" }));
  await screen.findByText("Login: succeeded");
  expect(starts[1].allow_account_switch).toBe(true);
  expect(screen.queryByText("Login: cancelled")).toBeNull();
});

it.each([
  ["kind: project\nroots: [null]\n", /Repair the host root list/],
  [
    "kind: agent\ncapabilities: null\n",
    /Repair the capability, child or tool list/,
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
    expect(screen.getByText("Advanced source")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Validate" })).toBeTruthy();
  },
);
