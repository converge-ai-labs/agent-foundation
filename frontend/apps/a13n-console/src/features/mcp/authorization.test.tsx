import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ConnectionDetails } from "../connections/editor";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PUT: vi.fn(),
  start: vi.fn(),
}));
vi.mock("../connections/authorization-context", () => ({
  startBrowserAuthorization: http.start,
}));
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
  const initial: Schema["Connection"] = {
    id: "mcpc_test",
    organization_id: "org_test",
    name: "Test OAuth connection",
    source: {
      kind: "mcp",
      endpoint_url: "https://mcp.example",
      auth_mode: "oauth",
    },
    status_reason: null,
    credential_configured: false,
    authorization_generation: 1,
    created_by: { principal_id: "usr_test", principal_type: "user" },
    created_at: "2026-09-11T00:00:00Z",
    updated_at: "2026-09-11T00:00:00Z",
    workspace_id: "ws_test",
    version: 1,
    status: "pending",
  };
  http.start.mockRejectedValue(new Error("Provider unavailable"));
  const updated = { ...initial, version: 2 };
  const failed = {
    ...updated,
    version: 3,
    status: "pending",
    credential_configured: true,
  };
  http.GET.mockResolvedValue({ data: failed, response: new Response() });
  http.PUT.mockResolvedValue({ data: updated, response: new Response() });
  http.POST.mockImplementation(async (path: string) => {
    if (path.endsWith("oauth-setup"))
      return {
        data: {
          client: null,
          next_action: {
            type: "configure_oauth_client",
            issuer_url: "https://auth.example",
            redirect_uri: "https://application.example/connections/callback",
            grant_types: ["authorization_code"],
            client_registration: "manual",
            token_endpoint_auth_methods: ["client_secret_post"],
          },
        },
        response: new Response(),
      };
    if (path.endsWith("authorizations"))
      throw new Error("Provider unavailable");
    return { data: failed, response: new Response() };
  });
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
        controlledOpen
        onClose={vi.fn()}
        onCleanup={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(
    await screen.findByRole("textbox", { name: "Client ID" }),
    "my-app",
  );
  await user.type(screen.getByLabelText("Client secret"), "private-secret");
  await user.click(screen.getByRole("button", { name: "Save and authorize" }));
  expect(http.PUT.mock.calls[0][1].body).toMatchObject({
    expected_version: 1,
    client: {
      client_id: "my-app",
      client_secret: "private-secret",
      grant_type: "authorization_code",
    },
  });
  await waitFor(() =>
    expect(http.start).toHaveBeenCalledWith(
      expect.anything(),
      updated,
      "/workspace/test",
    ),
  );
  await user.click(screen.getByRole("button", { name: "Retry verification" }));
  await waitFor(() =>
    expect(
      http.POST.mock.calls.find(([path]) => path.endsWith("check"))?.[1].body,
    ).toEqual({ expected_version: 3 }),
  );
});
