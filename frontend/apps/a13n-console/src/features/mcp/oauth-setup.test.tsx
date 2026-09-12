import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { MCPOAuthSetup } from "./oauth-setup";

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
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const connection: Schema["Connection"] = {
  id: "mcpc_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  name: "OAuth connection",
  source: {
    kind: "mcp",
    endpoint_url: "https://mcp.example/mcp",
    auth_mode: "oauth",
  },
  status: "pending",
  status_reason: null,
  version: 1,
  credential_configured: false,
  authorization_generation: 1,
  created_by: { principal_id: "usr_test", principal_type: "user" },
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};

const discovery = {
  type: "configure_oauth_client",
  issuer_url: "https://auth.example",
  redirect_uri: "https://application.example/connections/callback",
  token_endpoint_auth_methods: ["client_secret_basic"],
  grant_types: ["authorization_code", "client_credentials"],
  client_registration: "dynamic",
} satisfies Schema["MCPOAuthSetupAction"];

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function renderSetup(value = connection, autoStart = true) {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MCPOAuthSetup
        connection={value}
        onConnectionChange={vi.fn()}
        autoStart={autoStart}
      />
    </QueryClientProvider>,
  );
}

it("offers verification retry without repeating OAuth metadata discovery", async () => {
  http.POST.mockResolvedValue({
    data: { ...connection, status: "ready" },
    response: new Response(),
  });
  renderSetup({ ...connection, credential_configured: true });

  await userEvent.click(
    screen.getByRole("button", { name: "Retry verification" }),
  );

  expect(http.GET).not.toHaveBeenCalled();
  expect(http.POST.mock.calls[0][0]).toMatch(/check$/);
});

it("continues automatic browser authorization without asking for client details", async () => {
  http.POST.mockResolvedValue({
    data: {
      client: null,
      next_action: { type: "start_authorization" },
    },
    response: new Response(),
  });

  renderSetup();

  await waitFor(() =>
    expect(http.start).toHaveBeenCalledWith(
      expect.anything(),
      connection,
      "/workspace/test",
    ),
  );
  expect(screen.queryByLabelText("Client ID")).toBeNull();
});

it("opens manual setup with a focused client ID and copyable callback", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  http.POST.mockResolvedValue({
    data: {
      client: null,
      next_action: {
        ...discovery,
        type: "configure_oauth_client",
        client_registration: "manual",
      },
    },
    response: new Response(),
  });

  renderSetup(connection, false);

  const clientId = await screen.findByLabelText("Client ID");
  expect(document.activeElement).toBe(clientId);
  expect((clientId as HTMLInputElement).autocomplete).toBe("off");
  expect(
    (screen.getByLabelText("Client secret") as HTMLInputElement).autocomplete,
  ).toBe("new-password");
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Copy callback URL" }));
  expect(write).toHaveBeenCalledWith(discovery.redirect_uri);
});

it("connects a saved machine client without starting browser authorization", async () => {
  const client = {
    issuer_url: discovery.issuer_url,
    client_id: "machine-client",
    token_endpoint_auth_method: "client_secret_basic",
    grant_type: "client_credentials",
    source: "pre_registered",
  } satisfies Schema["MCPOAuthClientConfiguration"];
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-setup")
      ? {
          client,
          next_action: { type: "authenticate_client_credentials" },
        }
      : { ...connection, status: "ready", credential_configured: true },
    response: new Response(),
  }));

  renderSetup();

  await waitFor(() =>
    expect(
      http.POST.mock.calls.some(([path]) => path.endsWith("authorizations")),
    ).toBe(true),
  );
  expect(http.start).not.toHaveBeenCalled();
});

it("opens machine-only discovery as client setup and connects without a callback", async () => {
  const machineDiscovery = {
    ...discovery,
    redirect_uri: null,
    grant_types: ["client_credentials"],
  } satisfies Schema["MCPOAuthSetupAction"];
  http.PUT.mockResolvedValue({
    data: { ...connection, version: 2 },
    response: new Response(),
  });
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-setup")
      ? {
          client: null,
          next_action: {
            ...machineDiscovery,
            type: "configure_oauth_client",
          },
        }
      : { ...connection, status: "ready", credential_configured: true },
    response: new Response(),
  }));

  renderSetup();
  await userEvent.type(await screen.findByLabelText("Client ID"), "machine");
  await userEvent.type(screen.getByLabelText("Client secret"), "secret");
  expect(screen.queryByText("Callback URL")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and connect" }),
  );

  await waitFor(() => expect(http.PUT).toHaveBeenCalledOnce());
  expect(http.PUT.mock.calls[0][1].body.client.grant_type).toBe(
    "client_credentials",
  );
  await waitFor(() =>
    expect(
      http.POST.mock.calls.some(([path]) => path.endsWith("authorizations")),
    ).toBe(true),
  );
  expect(http.start).not.toHaveBeenCalled();
});
