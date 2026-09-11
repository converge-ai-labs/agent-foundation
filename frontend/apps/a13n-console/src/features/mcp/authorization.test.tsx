import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { MCPEditor } from "./editor";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn(), PUT: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("uses the saved app and refreshed version for reconnect after authorization fails", async () => {
  const initial: Schema["MCPConnection"] = {
    id: "mcpc_test",
    organization_id: "org_test",
    name: "Test OAuth connection",
    endpoint_url: "https://mcp.example",
    status_reason: null,
    credential_configured: false,
    credential_generation: 0,
    created_by: { principal_id: "usr_test", principal_type: "user" },
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
    workspace_id: "ws_test",
    version: 1,
    status: "pending",
    auth_mode: "oauth",
    static_header_names: [],
  };
  const updated = { ...initial, version: 2 };
  const failed = { ...updated, version: 3, status: "action_required" };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-client") ? null : failed,
    response: new Response(),
  }));
  http.PUT.mockResolvedValue({ data: updated, response: new Response() });
  http.POST.mockImplementation(async (path: string) => {
    if (path.endsWith("oauth-discovery"))
      return {
        data: {
          issuer_url: "https://auth.example",
          redirect_uri: "https://service.example/api/v1/oauth/mcp/callback/key",
          token_endpoint_auth_methods_supported: ["client_secret_post"],
          authorization_response_iss_parameter_supported: false,
        },
        response: new Response(),
      };
    if (path.endsWith("authorize")) throw new Error("Provider unavailable");
    return { data: failed, response: new Response() };
  });
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  cache.setQueryData(["mcp-connections", "ws_test", initial.id], initial);
  render(
    <QueryClientProvider client={cache}>
      <MCPEditor
        connectionId={initial.id}
        controlledOpen
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Configure your OAuth app" }),
  );
  await user.type(
    await screen.findByRole("textbox", { name: "Client ID" }),
    "my-app",
  );
  await user.type(screen.getByLabelText("Client secret"), "private-secret");
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "Save and authorize" }));
  await screen.findByText("Provider unavailable");
  expect(http.PUT.mock.calls[0][1].body).toMatchObject({
    expected_version: 1,
    client: {
      client_id: "my-app",
      client_secret: "private-secret",
      allow_missing_issuer: true,
    },
  });
  expect(
    http.POST.mock.calls.find(([path]) => path.endsWith("authorize"))?.[1].body,
  ).toEqual({ expected_version: 2 });
  await user.click(screen.getByRole("button", { name: "Verify connection" }));
  await waitFor(() =>
    expect(
      http.POST.mock.calls.find(([path]) => path.endsWith("reconnect"))?.[1]
        .body,
    ).toEqual({ expected_version: 3 }),
  );
});
