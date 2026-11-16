import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../service-client";
import { i18n } from "../i18n";
import { AppPluginsProvider, catalogKey } from "./registry";
import { AppRoute, WorkspaceHome } from "./views";
import { AppManagement } from "./management";
import { SettingsLayout } from "../features/settings/layout";
import { WorkspaceSettings } from "../features/settings/pages";

const state = vi.hoisted(() => ({
  client: null as Client | null,
  workspaceId: "ws_one",
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
    organizationCan: () => true,
    basePath: `/workspace/${state.workspaceId}`,
  });
  return { useAccess, useWorkspace: useAccess };
});
let cache: QueryClient;
let requests: Request[];
let enabled: boolean;
let catalogRead: (() => Promise<unknown>) | undefined;
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
  state.workspaceId = "ws_one";
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
function mount(path: string) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[path]}>
        <AppPluginsProvider>
          <Link to="/agents">Core agents</Link>
          <Routes>
            <Route path="/apps/:appId/:pageId" element={<AppRoute />} />
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
    </QueryClientProvider>,
  );
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
  const toggle = await screen.findByRole("switch", { name: "Enable Reports" });
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
  const toggle = await screen.findByRole("switch", { name: "Enable Reports" });
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
