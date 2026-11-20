import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionsPage } from "./page";

const http = vi.hoisted(() => ({ GET: vi.fn(), PATCH: vi.fn() }));
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
  expect(screen.queryByRole("menuitem", { name: "Revoke" })).toBeNull();
  await user.click(screen.getByRole("menuitem", { name: "Details" }));
  expect(
    await screen.findByRole("complementary", { name: "Connection details" }),
  ).toBeTruthy();
  cache.clear();
});
