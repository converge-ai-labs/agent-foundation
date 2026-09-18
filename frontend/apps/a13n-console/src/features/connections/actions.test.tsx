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
  DELETE: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
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
    status: "ready",
    version: 2,
    safe_metadata: {},
    source: {
      kind: "mcp",
      auth_mode: "none",
      endpoint_url: "https://mcp.example",
    },
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
      expect.objectContaining({
        body: { name: "Renamed", expected_version: 2 },
      }),
    ),
  );
  cache.clear();
});

for (const kind of ["connector", "mcp"] as const) {
  for (const action of kind === "connector"
    ? ["Enable", "Revoke", "Delete"]
    : ["Enable", "Delete"]) {
    it(`${kind} ${action} follows a refreshed resource version without remounting`, async () => {
      const user = userEvent.setup();
      const cache = new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      });
      const resource = {
        id: "connection_test",
        workspace_id: "ws_test",
        name: "Test connection",
        status: "disabled",
        version: 2,
        safe_metadata: {},
        source:
          kind === "connector"
            ? { kind, provider_id: "cnr_test", connector_key: "github" }
            : { kind, auth_mode: "none", endpoint_url: "https://mcp.example" },
        credential_configured: false,
      };
      const queryKey = ["connections", "ws_test", resource.id];
      cache.setQueryData(queryKey, resource);
      http.GET.mockResolvedValue({
        data: { ...resource, version: 3 },
        response: new Response(),
      });
      http.POST.mockResolvedValue({
        data: { ...resource, version: 4 },
        response: new Response(),
      });
      http.DELETE.mockResolvedValue({
        data: {
          connection_id: resource.id,
          local_status: "deleted",
          remote_status: "not_required",
        },
        response: new Response(),
      });
      render(
        <QueryClientProvider client={cache}>
          {kind === "connector" ? (
            <ConnectionDetails
              connectionId={resource.id}
              onClose={vi.fn()}
              onCleanup={vi.fn()}
            />
          ) : (
            <ConnectionDetails
              connectionId={resource.id}
              onClose={vi.fn()}
              onCleanup={vi.fn()}
            />
          )}
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
      await waitFor(() => {
        if (action === "Delete")
          expect(http.DELETE).toHaveBeenCalledWith(
            "/api/v1/connections/{connection_id}",
            expect.objectContaining({
              params: expect.objectContaining({
                query: { expected_version: 3 },
              }),
            }),
          );
        else
          expect(http.POST).toHaveBeenCalledWith(
            `/api/v1/connections/{connection_id}/${action === "Revoke" ? "connector/revoke" : "enable"}`,
            expect.objectContaining({ body: { expected_version: 3 } }),
          );
      });
      cache.clear();
    });
  }
}
