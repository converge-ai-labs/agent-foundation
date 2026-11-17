import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { ToastProvider } from "a13n-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../service-client";
import { i18n } from "../i18n";
import { AppPluginsProvider, catalogKey } from "./registry";
import { AppRoute, WorkspaceHome } from "./views";
import { AppContext, useApp } from "./context";
import { AppManagement } from "./management";
import { SettingsLayout } from "../features/settings/layout";
import { WorkspaceSettings } from "../features/settings/pages";

const state = vi.hoisted(() => ({
  client: null as Client | null,
  workspaceId: "ws_one",
  organizationAdmin: true,
}));
vi.mock("../auth/context", () => ({
  useClient: () => state.client,
  useAuth: () => ({
    data: {
      user: {
        value: { id: "usr_demo", name: "Demo", email: "demo@example.com" },
      },
    },
  }),
}));
vi.mock("../layout/workspace", () => {
  const useAccess = () => ({
    organization: {
      id: "org_demo",
      name: "Demo",
      permissions: ["read", "admin"],
    },
    workspace: { id: state.workspaceId, name: "Demo" },
    workspaces: [],
    can: () => true,
    organizationCan: () => state.organizationAdmin,
    basePath: `/workspace/${state.workspaceId}`,
  });
  return { useAccess, useWorkspace: useAccess };
});
let cache: QueryClient;
let requests: Request[];
let enabled: boolean;
let catalogRead: (() => Promise<unknown>) | undefined;
let policyWrite: (() => Promise<Response>) | undefined;
const inventory = () => ({
  id: "org_demo",
  version: enabled ? 2 : 1,
  permissions: ["read", "admin"],
  items: [
    {
      app_id: "acme.reports",
      name: "Reports",
      distribution: "reports",
      version: "1.0",
      factory_keys: [],
      enabled,
      frontend: enabled
        ? { bridge: 1, entry: "./fixture.tsx", styles: [] }
        : null,
    },
  ],
});
beforeEach(async () => {
  requests = [];
  enabled = true;
  catalogRead = undefined;
  policyWrite = undefined;
  state.workspaceId = "ws_one";
  state.organizationAdmin = true;
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  state.client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf-proof" },
    maxReadRetries: 0,
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      const path = new URL(request.url).pathname;
      let value: unknown;
      if (path.startsWith("/api/v1/workspaces/"))
        value = {
          id: state.workspaceId,
          name: "Demo",
          version: 1,
          permissions: ["read", "admin"],
        };
      else if (path.endsWith("/summary"))
        value = { title: request.headers.get("X-Workspace-ID") };
      else if (path.endsWith("/preview")) value = { ready: true };
      else if (request.method === "PUT") {
        if (policyWrite) return policyWrite();
        enabled = (await request.json()).enabled;
        value = inventory();
      } else value = catalogRead ? await catalogRead() : inventory();
      return new Response(JSON.stringify(value), {
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  await i18n.changeLanguage("en");
});
afterEach(() => {
  cleanup();
  state.client?.close();
  cache.clear();
  vi.restoreAllMocks();
});
function Runtime({ path }: { path: string }) {
  return (
    <QueryClientProvider client={cache}>
      <ToastProvider closeLabel="Dismiss">
        <MemoryRouter initialEntries={[path]}>
          <AppPluginsProvider>
            <Link to="/agents">Core agents</Link>
            <Link to="/apps/acme.reports/stateful/two">Open report</Link>
            <Routes>
              <Route path="/apps/:appId/:pageId/*" element={<AppRoute />} />
              <Route path="/home" element={<WorkspaceHome />} />
              <Route path="/agents" element={<h1>Core agents page</h1>} />
              <Route path="/manage" element={<AppManagement />} />
              <Route
                path="/workspace/:workspaceId/settings/:section"
                element={<WorkspaceSettings />}
              />
              <Route
                path="/organization/settings/:section"
                element={
                  <SettingsLayout
                    scope="organization"
                    content={{ "app-plugins": <AppManagement /> }}
                  />
                }
              />
            </Routes>
          </AppPluginsProvider>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
function mount(path: string) {
  return render(<Runtime path={path} />);
}
it("loads an enabled module into host providers, scopes requests and separates workspace caches", async () => {
  const mounted = mount("/apps/acme.reports/overview");
  await screen.findByRole("heading", { name: "Installed report" });
  await screen.findByText("ws_one");
  expect(screen.getByText("Host cache")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  await waitFor(() =>
    expect(requests.some((request) => request.method === "POST")).toBe(true),
  );
  const mutation = requests.find((request) => request.method === "POST")!;
  expect(mutation.headers.get("X-CSRF-Token")).toBe("csrf-proof");
  expect(mutation.headers.get("X-Workspace-ID")).toBe("ws_one");
  state.workspaceId = "ws_two";
  // A fresh mount in another workspace must request and cache another representation.
  mounted.unmount();
  mount("/apps/acme.reports/overview");
  await screen.findByText("ws_two");
  expect(
    cache.getQueryData(["app", "acme.reports", "org_demo", "ws_one", "report"]),
  ).toEqual({ title: "ws_one" });
  expect(
    cache.getQueryData(["app", "acme.reports", "org_demo", "ws_two", "report"]),
  ).toEqual({ title: "ws_two" });
});
it("keeps core navigation usable after an App render failure", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  mount("/apps/acme.reports/failure");
  await screen.findByText("App render failed");
  fireEvent.click(screen.getByRole("link", { name: "Core agents" }));
  await screen.findByRole("heading", { name: "Core agents page" });
});
it("renders contributed settings through their actual metadata and content owners", async () => {
  mount("/workspace/ws_one/settings/app.acme.reports.reporting");
  await screen.findByRole("heading", { name: "Report settings" });
  await screen.findByRole("heading", { name: "Installed report" });
  expect(
    screen
      .getByRole("link", { name: "Report settings" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(screen.getByText("Workspace reports")).toBeTruthy();
});
it("manages policy through the organization precondition and removes disabled contributions", async () => {
  mount("/manage");
  const toggle = await screen.findByRole("switch", { name: "Reports" });
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(requests.some((request) => request.method === "PUT")).toBe(true),
  );
  const mutation = requests.find((request) => request.method === "PUT")!;
  expect(mutation.headers.get("If-Match")).toBe('"org_demo:2"');
  expect(mutation.headers.get("X-CSRF-Token")).toBe("csrf-proof");
  await waitFor(() =>
    expect(toggle.getAttribute("aria-checked")).toBe("false"),
  );
});

it("keeps a committed policy response when an older catalog read finishes later", async () => {
  mount("/manage");
  const toggle = await screen.findByRole("switch", { name: "Reports" });
  const oldInventory = inventory();
  let finishRead!: (value: unknown) => void;
  const delayedRead = new Promise<unknown>((resolve) => {
    finishRead = resolve;
  });
  const read = vi.fn(() => delayedRead);
  catalogRead = read;
  let refetch!: Promise<void>;
  act(() => {
    refetch = cache.refetchQueries({
      queryKey: catalogKey("org_demo"),
      exact: true,
    });
  });
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(toggle.getAttribute("aria-checked")).toBe("false"),
  );
  const committed = cache.getQueryData(catalogKey("org_demo"));
  await act(async () => {
    finishRead(oldInventory);
    await refetch;
    await delayedRead;
  });
  expect(cache.getQueryData(catalogKey("org_demo"))).toEqual(committed);
  expect(toggle.getAttribute("aria-checked")).toBe("false");
});
it("contains malformed module metadata before it reaches core navigation", async () => {
  const malformed = inventory();
  malformed.items[0].frontend!.entry = "./invalid-fixture.tsx";
  catalogRead = async () => malformed;
  mount("/apps/acme.reports/overview");
  await screen.findByText(
    "Incomplete or invalid App contribution text: overview",
  );
  fireEvent.click(screen.getByRole("link", { name: "Core agents" }));
  await screen.findByRole("heading", { name: "Core agents page" });
});
it("does not mount disabled Apps and keeps their business requests out of the transport", async () => {
  enabled = false;
  mount("/apps/acme.reports/overview");
  await screen.findByRole("heading", { name: "App page not found" });
  expect(
    requests.every((request) =>
      new URL(request.url).pathname.endsWith("/app-plugins"),
    ),
  ).toBe(true);
  expect(
    screen.queryByRole("heading", { name: "Installed report" }),
  ).toBeNull();
});
it("keeps Agents reachable when multiple Apps claim workspace home", async () => {
  const competing = inventory();
  competing.items.push({ ...competing.items[0], app_id: "other.reports" });
  catalogRead = async () => competing;
  mount("/home");
  await screen.findByRole("heading", { name: "Workspace home unavailable" });
  expect(
    screen.queryByRole("heading", { name: "Installed report" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Core agents" }));
  await screen.findByRole("heading", { name: "Core agents page" });
});
it("routes workspace home through the named replacement without replacing Agents", async () => {
  mount("/home");
  await screen.findByRole("heading", { name: "Installed report" });
  fireEvent.click(screen.getByRole("link", { name: "Core agents" }));
  await screen.findByRole("heading", { name: "Core agents page" });
});

it("normalizes App request paths and rejects paths leaving the namespace before transport", async () => {
  const { result } = renderHook(() => useApp(), {
    wrapper: ({ children }) => (
      <AppContext.Provider value="acme.reports">{children}</AppContext.Provider>
    ),
  });
  for (const path of [
    "/../other/preview",
    "/%2e%2e/other/preview",
    "/.%2e/other/preview",
    "/../../organizations/org_demo/app-plugins",
    "/\\../other/preview",
    "//other/preview",
    "https://other.example/preview",
  ]) {
    expect(() => result.current.request(path, { method: "POST" })).toThrow(
      TypeError,
    );
  }
  expect(requests).toHaveLength(0);
  await result.current.request("/reports/../preview?mode=draft", {
    method: "POST",
  });
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toBe(
    "https://service.example/api/v1/apps/acme.reports/preview?mode=draft",
  );
  expect(requests[0].headers.get("X-Workspace-ID")).toBe("ws_one");
  expect(requests[0].headers.get("X-CSRF-Token")).toBe("csrf-proof");
});
it("preserves App local state and focused input across its subroutes", async () => {
  mount("/apps/acme.reports/stateful/one");
  const input = await screen.findByRole("textbox", { name: "Report draft" });
  fireEvent.change(input, { target: { value: "Keep this draft" } });
  input.focus();
  fireEvent.click(screen.getByRole("link", { name: "Next report" }));
  await screen.findByText("/apps/acme.reports/stateful/two");
  expect(screen.getByRole("textbox", { name: "Report draft" })).toBe(input);
  expect((input as HTMLInputElement).value).toBe("Keep this draft");
  expect(document.activeElement).toBe(input);
});
it("recovers a failed App boundary when navigating to another subroute", async () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  mount("/apps/acme.reports/stateful/one");
  fireEvent.click(await screen.findByRole("link", { name: "Broken report" }));
  await screen.findByText("Subpage failed");
  fireEvent.click(screen.getByRole("link", { name: "Open report" }));
  await screen.findByRole("textbox", { name: "Report draft" });
});
it.each([
  ["/apps/acme.reports/overview", "Installed report"],
  ["/workspace/ws_one/settings/app.acme.reports.reporting", "Report settings"],
  ["/manage", undefined],
] as const)(
  "keeps confirmed content after a catalog refetch fails at %s",
  async (path, heading) => {
    mount(path);
    if (heading) await screen.findByRole("heading", { name: heading });
    else await screen.findByRole("switch", { name: "Reports" });
    catalogRead = async () => {
      throw new Error("Catalog refresh failed");
    };
    await act(async () => {
      await cache.refetchQueries({
        queryKey: catalogKey("org_demo"),
        exact: true,
      });
    });
    await waitFor(() =>
      expect(cache.getQueryState(catalogKey("org_demo"))?.error).toBeTruthy(),
    );
    if (heading)
      expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    else expect(screen.getByRole("switch", { name: "Reports" })).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: "App unavailable" }),
    ).toBeNull();
  },
);
it("keeps failed policy updates locked until a fresh catalog read succeeds", async () => {
  mount("/manage");
  const toggle = await screen.findByRole("switch", { name: "Reports" });
  policyWrite = async () => {
    throw new Error("Policy response lost");
  };
  fireEvent.click(toggle);
  await screen.findByText(
    "The App change could not be confirmed. Reload installed apps before changing an App again.",
  );
  const reload = screen.getByRole("button", { name: "Reload installed apps" });
  expect(toggle.hasAttribute("data-disabled")).toBe(true);
  let failRead!: (reason: unknown) => void;
  const delayedRead = new Promise<unknown>((_, reject) => {
    failRead = reject;
  });
  const read = vi.fn(() => delayedRead);
  catalogRead = read;
  fireEvent.click(reload);
  await waitFor(() => expect(read).toHaveBeenCalledOnce());
  expect(toggle.hasAttribute("data-disabled")).toBe(true);
  expect(reload.hasAttribute("disabled")).toBe(true);
  await act(async () => {
    failRead(new Error("Catalog read failed"));
  });
  await waitFor(() =>
    expect(cache.getQueryState(catalogKey("org_demo"))?.fetchStatus).toBe(
      "idle",
    ),
  );
  expect(screen.getByRole("switch", { name: "Reports" })).toBe(toggle);
  expect(
    screen.getByText(
      "Installed apps could not be reloaded. The last confirmed state is shown.",
    ),
  ).toBeTruthy();
  expect(toggle.hasAttribute("data-disabled")).toBe(true);
  expect(
    screen
      .getByRole("button", { name: "Reload installed apps" })
      .hasAttribute("disabled"),
  ).toBe(false);
  const fresh = { ...inventory(), version: 3 };
  let finishFreshRead!: (value: unknown) => void;
  catalogRead = () =>
    new Promise((resolve) => {
      finishFreshRead = resolve;
    });
  reload.focus();
  fireEvent.click(reload);
  await waitFor(() => expect(reload.hasAttribute("disabled")).toBe(true));
  // Browsers blur a native button while its loading state disables it.
  reload.blur();
  await act(async () => {
    finishFreshRead(fresh);
  });
  await waitFor(() => expect(toggle.hasAttribute("data-disabled")).toBe(false));
  expect(screen.getByRole("button", { name: "Reload installed apps" })).toBe(
    reload,
  );
  expect(document.activeElement).toBe(reload);
  expect(screen.queryByRole("alert")).toBeNull();
  policyWrite = undefined;
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(requests.filter((request) => request.method === "PUT")).toHaveLength(
      2,
    ),
  );
  expect(
    requests
      .filter((request) => request.method === "PUT")[1]
      .headers.get("If-Match"),
  ).toBe('"org_demo:3"');
});

it.each(["/apps/acme.reports/overview", "/manage"])(
  "shows catalog failure when no confirmed inventory exists at %s",
  async (path) => {
    catalogRead = async () => {
      throw new Error("Initial catalog read failed");
    };
    mount(path);
    await screen.findByText("Initial catalog read failed");
    expect(screen.queryByRole("switch", { name: "Reports" })).toBeNull();
    expect(
      screen.queryByRole("heading", { name: "Installed report" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "Core agents" }));
    await screen.findByRole("heading", { name: "Core agents page" });
  },
);

it("resets App component state when its host workspace changes", async () => {
  const path = "/apps/acme.reports/stateful/one";
  const mounted = mount(path);
  const input = await screen.findByRole("textbox", { name: "Report draft" });
  fireEvent.change(input, { target: { value: "Workspace one draft" } });
  state.workspaceId = "ws_two";
  mounted.rerender(<Runtime path={path} />);
  const replacement = screen.getByRole("textbox", { name: "Report draft" });
  expect(replacement).not.toBe(input);
  expect((replacement as HTMLInputElement).value).toBe("");
});

it("explains a stale policy switch inline without claiming an editor draft", async () => {
  policyWrite = async () =>
    new Response(
      JSON.stringify({
        error: {
          code: "precondition_failed",
          message: "Policy changed",
          details: {},
          request_id: "req_policy",
        },
      }),
      { status: 412, headers: { "Content-Type": "application/json" } },
    );
  mount("/manage");
  const toggle = await screen.findByRole("switch", { name: "Reports" });
  expect(toggle.getAttribute("aria-describedby")).toBe(
    "app-plugin-acme.reports-description",
  );
  expect(
    document.querySelector('label[for="app-plugin-acme.reports"]')?.textContent,
  ).toBe("Reports");
  fireEvent.click(toggle);
  await screen.findByRole("alert");
  expect(
    screen.getByText(
      "The App change could not be confirmed. Reload installed apps before changing an App again.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/Your draft is preserved/)).toBeNull();
  expect(toggle.hasAttribute("data-disabled")).toBe(true);
});

it("offers catalog retry and a workspace-scoped exit from an unavailable App", async () => {
  catalogRead = async () => {
    throw new Error("Catalog temporarily offline");
  };
  mount("/apps/acme.reports/overview");
  await screen.findByRole("heading", { name: "App unavailable" });
  expect(
    screen.getByRole("link", { name: "Go to agents" }).getAttribute("href"),
  ).toBe("/workspace/ws_one/agents");
  catalogRead = undefined;
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByRole("heading", { name: "Installed report" });
});

it("offers reload and an exit when an installed frontend cannot load", async () => {
  const malformed = inventory();
  malformed.items[0].frontend!.entry = "./invalid-fixture.tsx";
  catalogRead = async () => malformed;
  mount("/apps/acme.reports/overview");
  await screen.findByRole("heading", { name: "App unavailable" });
  expect(screen.getByRole("button", { name: "Reload Console" })).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Go to agents" }).getAttribute("href"),
  ).toBe("/workspace/ws_one/agents");
});

it("gives members a usable missing-App exit without directing them to admin settings", async () => {
  state.organizationAdmin = false;
  enabled = false;
  mount("/apps/acme.reports/overview");
  await screen.findByRole("heading", { name: "App page not found" });
  expect(
    screen.getByText(
      "Choose another page or contact your organization administrator.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByText(
      "Enable the App in organization settings, or choose another page.",
    ),
  ).toBeNull();
  expect(
    screen.getByRole("link", { name: "Go to agents" }).getAttribute("href"),
  ).toBe("/workspace/ws_one/agents");
});
