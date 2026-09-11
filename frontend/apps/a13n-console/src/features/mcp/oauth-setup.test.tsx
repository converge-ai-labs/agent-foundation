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

const connection: Schema["MCPConnection"] = {
  id: "mcpc_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  name: "OAuth connection",
  endpoint_url: "https://mcp.example/mcp",
  auth_mode: "oauth",
  status: "pending",
  status_reason: null,
  version: 1,
  credential_configured: false,
  credential_generation: 0,
  static_header_names: [],
  created_by: { principal_id: "usr_test", principal_type: "user" },
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};

const discovery: Schema["MCPOAuthDiscovery"] = {
  issuer_url: "https://auth.example",
  redirect_uri: "https://service.example/api/v1/oauth/mcp/callback/key",
  token_endpoint_auth_methods_supported: ["client_secret_basic"],
  grant_types_supported: ["authorization_code", "client_credentials"],
  client_registration: "dynamic",
  authorization_response_iss_parameter_supported: true,
};

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
  expect(http.POST.mock.calls[0][0]).toMatch(/reconnect$/);
});

it("continues automatic browser authorization without asking for client details", async () => {
  http.GET.mockResolvedValue({ data: null, response: new Response() });
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-discovery")
      ? discovery
      : {
          id: "mos_test",
          authorization_url: "https://auth.example/authorize",
          expires_at: "2026-09-12T00:10:00Z",
        },
    response: new Response(),
  }));

  renderSetup();

  await waitFor(() =>
    expect(
      http.POST.mock.calls.some(([path]) => path.endsWith("authorize")),
    ).toBe(true),
  );
  expect(screen.queryByLabelText("Client ID")).toBeNull();
});

it("opens manual setup with a focused client ID and copyable callback", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  http.GET.mockResolvedValue({ data: null, response: new Response() });
  http.POST.mockResolvedValue({
    data: { ...discovery, client_registration: "manual" },
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
  http.GET.mockResolvedValue({
    data: {
      issuer_url: discovery.issuer_url,
      client_id: "machine-client",
      token_endpoint_auth_method: "client_secret_basic",
      grant_type: "client_credentials",
      source: "pre_registered",
    },
    response: new Response(),
  });
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-discovery")
      ? discovery
      : { ...connection, status: "ready", credential_configured: true },
    response: new Response(),
  }));

  renderSetup();

  await waitFor(() =>
    expect(
      http.POST.mock.calls.some(([path]) => path.endsWith("authenticate")),
    ).toBe(true),
  );
  expect(
    http.POST.mock.calls.some(([path]) => path.endsWith("authorize")),
  ).toBe(false);
});

it("opens machine-only discovery as client setup and connects without a callback", async () => {
  const machineDiscovery = {
    ...discovery,
    redirect_uri: null,
    grant_types_supported: ["client_credentials"],
  } satisfies Schema["MCPOAuthDiscovery"];
  http.GET.mockResolvedValue({ data: null, response: new Response() });
  http.PUT.mockResolvedValue({
    data: { ...connection, version: 2 },
    response: new Response(),
  });
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("oauth-discovery")
      ? machineDiscovery
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
      http.POST.mock.calls.some(([path]) => path.endsWith("authenticate")),
    ).toBe(true),
  );
  expect(
    http.POST.mock.calls.some(([path]) => path.endsWith("authorize")),
  ).toBe(false);
});
