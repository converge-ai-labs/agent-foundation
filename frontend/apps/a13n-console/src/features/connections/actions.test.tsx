// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionDetails } from "./editor";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("../connectors/setup", () => ({ ConnectionSetup: () => null }));
vi.mock("../mcp/tools", () => ({ MCPTools: () => null }));
vi.mock("../../shared/resource-modal-title", () => ({
  ResourceModalTitle: () => "Connection",
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("submits a renamed Connection from the bottom action row", async () => {
  const user = userEvent.setup();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const resource = {
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
  };
  cache.setQueryData(["connections", "ws_test", resource.id], resource);
  http.PATCH.mockResolvedValue({
    data: { ...resource, name: "Renamed" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={cache}>
      <ConnectionDetails
        connectionId={resource.id}
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const name = await screen.findByRole("textbox", { name: "Name" });
  expect(screen.queryByText("Connection actions")).toBeNull();
  await user.clear(name);
  await user.type(name, "Renamed");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      "/api/v1/connections/{connection_id}",
      {
        params: {
          path: { connection_id: "connection_test" },
        },
        headers: { "If-Match": '"connection_test:2"' },
        body: { name: "Renamed" },
      },
    ),
  );
  cache.clear();
});

for (const kind of ["connector", "mcp"] as const) {
  for (const action of ["Enable", "Revoke"]) {
    it(`${kind} ${action} follows a refreshed resource version without remounting`, async () => {
      const user = userEvent.setup();
      const cache = new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      });
      const resource = {
        id: "connection_test",
        workspace_id: "ws_test",
        name: "Test connection",
        status: "ready",
        enabled: false,
        version: 2,
        ...(kind === "connector"
          ? {
              type: "composio",
              auth: "account",
              connector_provider_id: "cprov_test",
              config: { app: "github", actions: ["GITHUB_GET_REPO"] },
            }
          : {
              type: "mcp",
              auth: "bearer",
              config: { url: "https://mcp.example" },
            }),
        credential_configured: false,
      };
      const queryKey = ["connections", "ws_test", resource.id];
      cache.setQueryData(queryKey, resource);
      http.GET.mockResolvedValue({
        data: { ...resource, version: 3 },
        response: new Response(),
      });
      const updated = { ...resource, version: 4, failure: null };
      http.PATCH.mockResolvedValue({
        data: { ...updated, enabled: true },
        response: new Response(),
      });
      http.POST.mockResolvedValue({
        data: { ...updated, remote_revocation: "skipped" },
        response: new Response(),
      });
      const onCleanup = vi.fn();
      render(
        <QueryClientProvider client={cache}>
          <ConnectionDetails
            connectionId={resource.id}
            onClose={vi.fn()}
            onCleanup={onCleanup}
          />
        </QueryClientProvider>,
      );
      await screen.findByRole("button", { name: "Connection actions" });
      if (kind === "mcp" && action === "Enable") {
        await user.clear(screen.getByRole("textbox", { name: "Name" }));
        await user.type(
          screen.getByRole("textbox", { name: "Name" }),
          "Unsaved name",
        );
        await user.click(screen.getByRole("tab", { name: "Authorization" }));
        await user.click(screen.getByRole("tab", { name: "Details" }));
        expect(
          screen.getByRole<HTMLInputElement>("textbox", { name: "Name" }).value,
        ).toBe("Unsaved name");
      }
      await act(async () => {
        cache.setQueryData(queryKey, { ...resource, version: 3 });
      });
      await user.click(
        screen.getByRole("button", { name: "Connection actions" }),
      );
      await user.click(await screen.findByRole("menuitem", { name: action }));
      await user.click(
        screen.getByRole("button", {
          name:
            action === "Revoke"
              ? "Revoke authorization"
              : `${action} connection`,
        }),
      );
      const request = {
        params: {
          path: { connection_id: resource.id },
        },
        headers: { "If-Match": '"connection_test:3"' },
      };
      if (action === "Revoke") {
        await waitFor(() =>
          expect(http.POST).toHaveBeenCalledWith(
            "/api/v1/connections/{connection_id}/revoke",
            request,
          ),
        );
        expect(onCleanup).toHaveBeenCalledWith("skipped");
      } else
        // Availability is a field of the connection, changed like its name.
        await waitFor(() =>
          expect(http.PATCH).toHaveBeenCalledWith(
            "/api/v1/connections/{connection_id}",
            { ...request, body: { enabled: true } },
          ),
        );
      cache.clear();
    });
  }
}

it("offers no revoke for a connection without a credential", async () => {
  const user = userEvent.setup();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const resource = {
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
    last_test: null,
  };
  cache.setQueryData(["connections", "ws_test", resource.id], resource);
  render(
    <QueryClientProvider client={cache}>
      <ConnectionDetails
        connectionId={resource.id}
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText("No check has run for this connection."),
  ).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Connection actions" }));
  expect(
    await screen.findByRole("menuitem", { name: "Disable" }),
  ).not.toBeNull();
  expect(screen.queryByRole("menuitem", { name: "Revoke" })).toBeNull();
  cache.clear();
});

it("shows the persisted outcome of the last check", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const resource = {
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
    last_test: {
      connection_version: 2,
      status: "failed",
      message: "The server did not answer in time",
      tested_at: "2026-09-12T00:00:00Z",
    },
  };
  cache.setQueryData(["connections", "ws_test", resource.id], resource);
  render(
    <QueryClientProvider client={cache}>
      <ConnectionDetails
        connectionId={resource.id}
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  expect(
    await screen.findByText("The server did not answer in time"),
  ).not.toBeNull();
  expect(
    document.querySelector('[data-state="failed"]')?.textContent,
  ).toBeTruthy();
  cache.clear();
});
