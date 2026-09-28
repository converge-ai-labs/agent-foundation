import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import type { Schema } from "../../shared/api";
import { MCPOAuthSetup } from "./oauth-setup";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  authorize: vi.fn(),
  start: vi.fn(),
}));
vi.mock("../connections/authorization-context", () => ({
  authorizeConnection: http.authorize,
  startBrowserAuthorization: http.start,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const callback = "https://service.example/api/v1/connections/callback";
const connection: Schema["Connection"] = {
  id: "conn_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "mcp",
  name: "OAuth connection",
  config: { url: "https://mcp.example/mcp", headers: [], oauth: null },
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
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};
const registrationFailed = new ApiError(
  503,
  "unavailable",
  "The authorization server could not be used",
  { dependency: "oauth", reason: "client_registration_failed" },
  null,
);

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function renderSetup(value = connection, autoStart = true) {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/redirect-uri")
      ? { redirect_uri: callback }
      : { ...value, status: "ready", credential_configured: true },
    response: new Response(),
  }));
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  const onConnectionChange = vi.fn();
  render(
    <QueryClientProvider client={cache}>
      <MCPOAuthSetup
        connection={value}
        onConnectionChange={onConnectionChange}
        autoStart={autoStart}
      />
    </QueryClientProvider>,
  );
  return onConnectionChange;
}

it("continues automatic browser authorization without asking for client details", async () => {
  http.start.mockReturnValue(new Promise(() => {}));

  renderSetup();

  await waitFor(() =>
    expect(http.start).toHaveBeenCalledWith(
      expect.anything(),
      connection,
      "/workspace/test",
    ),
  );
  expect(screen.queryByLabelText("Client ID")).toBeNull();
  expect(http.authorize).not.toHaveBeenCalled();
});

it("opens manual setup with a focused client ID and the Service callback when registration fails", async () => {
  const user = userEvent.setup();
  const write = vi.spyOn(navigator.clipboard, "writeText");
  http.start.mockRejectedValue(registrationFailed);

  renderSetup();

  const clientId = await screen.findByLabelText("Client ID");
  expect(document.activeElement).toBe(clientId);
  expect((clientId as HTMLInputElement).autocomplete).toBe("off");
  expect(screen.queryByLabelText("Client secret")).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  await user.click(
    await screen.findByRole("button", { name: "Copy callback URL" }),
  );
  expect(write).toHaveBeenCalledWith(callback);
  expect(http.GET).toHaveBeenCalledWith(
    "/api/v1/connections/redirect-uri",
    expect.anything(),
  );
});

it("connects a saved machine client without starting browser authorization", async () => {
  const machine = {
    ...connection,
    client_secret_configured: true,
    config: {
      ...connection.config,
      oauth: {
        client_id: "machine-client",
        token_endpoint_auth_method: "client_secret_basic" as const,
        grant_type: "client_credentials" as const,
      },
    },
  };
  http.authorize.mockResolvedValue({ redirect_url: null, expires_at: null });

  const onConnectionChange = renderSetup(machine);

  await waitFor(() =>
    expect(http.authorize).toHaveBeenCalledWith(expect.anything(), machine),
  );
  await waitFor(() =>
    expect(onConnectionChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: "ready" }),
    ),
  );
  expect(http.start).not.toHaveBeenCalled();
});

it("chooses a machine account and connects without a callback", async () => {
  const user = userEvent.setup();
  const saved = { ...connection, version: 2 };
  http.start.mockRejectedValue(registrationFailed);
  http.PATCH.mockResolvedValue({ data: saved, response: new Response() });
  http.authorize.mockResolvedValue({ redirect_url: null, expires_at: null });

  renderSetup();
  await user.click(
    await screen.findByRole("combobox", { name: "OAuth grant" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Machine account" }),
  );
  expect(screen.queryByText("Callback URL")).toBeNull();
  await user.type(screen.getByLabelText("Client ID"), "machine");
  const secret = screen.getByLabelText("Client secret") as HTMLInputElement;
  expect(secret.autocomplete).toBe("new-password");
  await user.type(secret, "secret");
  await user.click(screen.getByRole("button", { name: "Save and connect" }));

  await waitFor(() =>
    expect(http.authorize).toHaveBeenCalledWith(expect.anything(), saved),
  );
  expect(http.PATCH.mock.calls[0][1].body).toEqual({
    config: {
      ...connection.config,
      oauth: {
        client_id: "machine",
        token_endpoint_auth_method: "client_secret_basic",
        grant_type: "client_credentials",
      },
    },
    client_secret: "secret",
  });
  expect(http.start).toHaveBeenCalledOnce();
});

it("edits an already registered app and keeps its stored secret", async () => {
  const configured = {
    ...connection,
    status: "ready" as const,
    credential_configured: true,
    client_secret_configured: true,
    config: {
      ...connection.config,
      oauth: {
        client_id: "existing-client",
        scopes: ["read"],
        token_endpoint_auth_method: "client_secret_post" as const,
      },
    },
  };
  const saved = { ...configured, version: 2 };
  http.PATCH.mockResolvedValue({ data: saved, response: new Response() });
  http.start.mockReturnValue(new Promise(() => {}));

  renderSetup(configured, false);
  await userEvent.click(
    await screen.findByRole("button", { name: "Use your own OAuth app" }),
  );

  const clientId = (await screen.findByLabelText(
    "Client ID",
  )) as HTMLInputElement;
  expect(clientId.value).toBe("existing-client");
  expect(await screen.findByText(callback)).not.toBeNull();
  expect(
    screen.getByRole("button", { name: "Use automatic setup" }),
  ).not.toBeNull();
  expect(
    (screen.getByLabelText("Client secret") as HTMLInputElement).required,
  ).toBe(false);
  await userEvent.click(
    screen.getByRole("button", { name: "Save and authorize" }),
  );

  await waitFor(() =>
    expect(http.start).toHaveBeenCalledWith(
      expect.anything(),
      saved,
      "/workspace/test",
    ),
  );
  expect(http.PATCH.mock.calls[0][1]).toEqual({
    params: { path: { connection_id: "conn_test" } },
    headers: { "If-Match": '"conn_test:1"' },
    body: {
      config: {
        ...configured.config,
        oauth: {
          client_id: "existing-client",
          scopes: ["read"],
          token_endpoint_auth_method: "client_secret_post",
          grant_type: "authorization_code",
        },
      },
    },
  });
});

it("requires a new secret when the client changes", async () => {
  const configured = {
    ...connection,
    client_secret_configured: true,
    config: {
      ...connection.config,
      oauth: {
        client_id: "existing-client",
        token_endpoint_auth_method: "client_secret_basic" as const,
      },
    },
  };
  renderSetup(configured, false);
  await userEvent.click(
    await screen.findByRole("button", { name: "Use your own OAuth app" }),
  );
  await userEvent.type(await screen.findByLabelText("Client ID"), "-2");
  expect(
    (screen.getByLabelText("Client secret") as HTMLInputElement).required,
  ).toBe(true);
});
