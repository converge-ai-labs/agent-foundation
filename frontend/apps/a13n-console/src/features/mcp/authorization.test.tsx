import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionDetails } from "../connections/editor";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  start: vi.fn(),
}));
vi.mock("../connections/authorization-context", () => ({
  startBrowserAuthorization: http.start,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("uses the saved app and refreshed version for reconnect after authorization fails", async () => {
  const initial: Schema["Connection"] = {
    id: "conn_test",
    organization_id: "org_test",
    workspace_id: "ws_test",
    type: "mcp",
    name: "Test OAuth connection",
    config: { url: "https://mcp.example", headers: [], oauth: null },
    auth: "oauth",
    connector_provider_id: null,
    status: "pending",
    failure: null,
    credential_configured: false,
    client_secret_configured: false,
    authorization_pending: false,
    last_test: null,
    enabled: true,
    version: 1,
    created_by_id: "usr_test",
    updated_by_id: "usr_test",
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
  };
  const saved = {
    ...initial,
    version: 2,
    config: {
      ...initial.config,
      oauth: {
        client_id: "my-app",
        token_endpoint_auth_method: "none" as const,
        grant_type: "authorization_code" as const,
      },
    },
  };
  const refreshed = { ...saved, version: 3 };
  http.start.mockRejectedValue(new Error("Provider unavailable"));
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/redirect-uri")
      ? { redirect_uri: "https://service.example/api/v1/connections/callback" }
      : path.endsWith("/mcp-servers")
        ? { items: [], next_cursor: null }
        : refreshed,
    response: new Response(),
  }));
  http.PATCH.mockResolvedValue({ data: saved, response: new Response() });
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  cache.setQueryData(["connections", "ws_test", initial.id], initial);
  render(
    <QueryClientProvider client={cache}>
      <ConnectionDetails
        connectionId={initial.id}
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Use your own OAuth app" }),
  );
  await user.type(
    await screen.findByRole("textbox", { name: "Client ID" }),
    "my-app",
  );
  await user.click(screen.getByRole("button", { name: "Save and authorize" }));
  expect(http.PATCH).toHaveBeenCalledWith(
    "/api/v1/connections/{connection_id}",
    {
      params: { path: { connection_id: "conn_test" } },
      headers: { "If-Match": '"conn_test:1"' },
      body: { config: saved.config },
    },
  );
  await waitFor(() =>
    expect(http.start).toHaveBeenCalledWith(
      expect.anything(),
      saved,
      "/workspace/test",
    ),
  );
  await user.click(
    await screen.findByRole("button", { name: "Continue authorization" }),
  );
  await waitFor(() =>
    expect(http.start).toHaveBeenLastCalledWith(
      expect.anything(),
      refreshed,
      "/workspace/test",
    ),
  );
});
