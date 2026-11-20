import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionsPage } from "./page";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
const permissions = vi.hoisted(() => ({ write: true }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: (action: string) => action !== "write" || permissions.write,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

it("keeps provider navigation available beside empty-state connection setup", async () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(),
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      <MemoryRouter>
        <ConnectionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("No connections yet");
  const create = screen.getByRole("button", { name: "New connection" });
  expect(create.closest("header")).toBeNull();
  const manage = screen.getByRole("link", { name: "Manage providers" });
  expect(manage.closest("header")).not.toBeNull();
  expect(manage.getAttribute("href")).toBe(
    "/workspace/ws_test/settings/providers?category=connectors",
  );
  await userEvent.setup().click(create);
  expect(
    await screen.findByRole("dialog", { name: "New connection" }),
  ).toBeTruthy();
  expect(
    await screen.findByRole("button", { name: /Custom remote MCP/ }),
  ).toBeTruthy();
});

afterEach(() => {
  vi.resetAllMocks();
  permissions.write = true;
});

it("confirms a row action without opening connection details and preserves read-only access", async () => {
  const user = userEvent.setup();
  const connection = {
    id: "connection_test",
    workspace_id: "ws_test",
    name: "Test connection",
    type: "mcp",
    status: "ready",
    enabled: true,
    version: 2,
    auth: "none",
    config: { url: "https://mcp.example" },
    credential_configured: false,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  };
  http.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/connections"
        ? { items: [connection], next_cursor: null }
        : path === "/api/v1/connections/{connection_id}"
          ? connection
          : { items: [], next_cursor: null },
    response: new Response(),
  }));
  http.PATCH.mockImplementation(async () => {
    connection.enabled = false;
    connection.version = 3;
    return { data: { ...connection }, response: new Response() };
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = () => (
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ConnectionsPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const { rerender } = render(view());
  const actions = await screen.findByRole("button", {
    name: "Connection actions",
  });
  actions.focus();
  await user.keyboard("{Enter}");
  await user.click(await screen.findByRole("menuitem", { name: "Disable" }));
  expect(
    screen.queryByRole("complementary", { name: "Connection details" }),
  ).toBeNull();
  expect(http.PATCH).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Disable connection" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      "/api/v1/connections/{connection_id}",
      {
        params: { path: { connection_id: connection.id } },
        headers: { "If-Match": '\"connection_test:2\"' },
        body: { enabled: false },
      },
    ),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(actions);
  expect(await screen.findByRole("menuitem", { name: "Enable" })).toBeTruthy();
  await user.keyboard("{Escape}");

  permissions.write = false;
  rerender(view());
  await user.click(actions);
  expect(await screen.findByRole("menuitem", { name: "Details" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Enable" })).toBeNull();
  expect(
    screen.queryByRole("menuitem", { name: "Revoke authorization" }),
  ).toBeNull();
  await user.click(screen.getByRole("menuitem", { name: "Details" }));
  expect(
    await screen.findByRole("complementary", { name: "Connection details" }),
  ).toBeTruthy();
  cache.clear();
});

it("shows a revoked connection as not authorized and filters authorization progress separately", async () => {
  const user = userEvent.setup();
  const base = {
    workspace_id: "ws_test",
    type: "mcp",
    enabled: true,
    version: 2,
    auth: "oauth",
    config: { url: "https://mcp.example" },
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    status: "ready",
    credential_configured: true,
    authorization_pending: false,
  };
  const connection = { ...base, id: "connection_revoked", name: "Notion" };
  const authorizing = {
    ...base,
    id: "connection_authorizing",
    name: "New account",
    status: "pending",
    credential_configured: false,
    authorization_pending: true,
  };
  http.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/connections"
        ? { items: [{ ...connection }, authorizing], next_cursor: null }
        : { items: [], next_cursor: null },
    response: new Response(),
  }));
  http.POST.mockImplementation(async () => {
    Object.assign(connection, {
      status: "pending",
      credential_configured: false,
      version: 3,
    });
    return {
      data: { ...connection, remote_revocation: "skipped" },
      response: new Response(),
    };
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ConnectionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const row = (await screen.findByText("Notion")).closest("tr")!;
  await user.click(
    within(row).getByRole("button", { name: "Connection actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Revoke authorization" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Revoke authorization" }),
  );
  expect(await screen.findByText("Connection revoked")).toBeTruthy();
  expect(await within(row).findByText("Not authorized")).toBeTruthy();
  expect(
    screen.getByText(
      "a13n has stopped using this connection. Its settings are kept so you can authorize again.",
    ),
  ).toBeTruthy();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(
    within(row).getByRole("button", { name: "Connection actions" }),
  );
  expect(await screen.findByRole("menuitem", { name: "Details" })).toBeTruthy();
  expect(
    screen.queryByRole("menuitem", { name: "Revoke authorization" }),
  ).toBeNull();
  expect(
    screen.queryByRole("menuitem", { name: "Cancel authorization" }),
  ).toBeNull();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("combobox", { name: /Status/ }));
  await user.click(await screen.findByRole("option", { name: "Authorizing" }));
  await waitFor(() => expect(screen.queryByText("Notion")).toBeNull());
  expect(await screen.findByText("New account")).toBeTruthy();
  cache.clear();
});

it("refreshes an active authorization until it expires, then stops polling", async () => {
  const connection = {
    id: "connection_expiring",
    workspace_id: "ws_test",
    name: "Expiring authorization",
    type: "mcp",
    status: "pending",
    enabled: true,
    version: 2,
    auth: "oauth",
    config: { url: "https://mcp.example" },
    credential_configured: false,
    authorization_pending: true,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
  };
  let reads = 0;
  http.GET.mockImplementation(async (path: string) => {
    if (path === "/api/v1/connections") reads++;
    return {
      data: {
        items: path === "/api/v1/connections" ? [{ ...connection }] : [],
        next_cursor: null,
      },
      response: new Response(),
    };
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ConnectionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  try {
    expect(
      await screen.findByText("Authorizing", { exact: true }),
    ).toBeTruthy();
    connection.authorization_pending = false;
    const before = reads;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(
      await screen.findByText("Not authorized", { exact: true }),
    ).toBeTruthy();
    expect(reads).toBe(before + 1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(reads).toBe(before + 1);
  } finally {
    view.unmount();
    cache.clear();
    vi.useRealTimers();
  }
});
