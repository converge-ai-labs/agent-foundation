// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { ConnectionSetup } from "./setup";
import { clearAuthorization, readAuthorization } from "./authorization-context";

const http = vi.hoisted(() => ({ POST: vi.fn(), GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "ws_test" },
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
  clearAuthorization();
  vi.resetAllMocks();
});

const connection = {
  id: "cconn_test",
  connector_provider_id: "cnr_test",
  connector_key: "github",
  name: "GitHub",
  status: "pending",
  version: 1,
} as Schema["ConnectorConnection"];
const connector = {
  key: "github",
  connector_provider_id: "cnr_test",
  name: "GitHub",
  authentication_methods: ["OAUTH2"],
  setup_schema: {
    type: "object",
    properties: {
      auth_config_id: { const: "ac_test" },
      toolkit_version: { const: "20260903_01" },
    },
    required: ["auth_config_id", "toolkit_version"],
  },
} as Schema["Connector"];

function mount(definition = connector, resource = connection) {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/connectors/{connector_key}")
      ? definition
      : path.endsWith("{connector_provider_id}")
        ? { type: "composio" }
        : resource,
    response: new Response(),
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <ConnectionSetup connection={resource} connector={definition} />
    </QueryClientProvider>,
  );
}

it("submits fixed schema values and binds same-tab authorization to the returned attempt", async () => {
  const expiry = new Date(Date.now() + 60_000).toISOString();
  http.POST.mockResolvedValue({
    data: {
      attempt_id: "csa_test",
      requires_browser_callback: true,
      redirect_url: "https://connect.composio.dev/link/test",
      expires_at: expiry,
      connection,
    },
    response: new Response(),
  });
  http.GET.mockResolvedValue({ data: connection, response: new Response() });
  mount();
  expect(screen.queryByRole("textbox", { name: "toolkit_version" })).toBeNull();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  const link = await screen.findByRole("link", {
    name: "Continue authorization",
  });
  expect(link.getAttribute("target")).toBe("_self");
  const context = readAuthorization();
  expect(context?.attempt_id).toBe("csa_test");
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/connector-connections/{connection_id}/setup",
      expect.objectContaining({
        body: expect.objectContaining({
          browser_nonce: context?.browser_nonce,
          setup: { auth_config_id: "ac_test", toolkit_version: "20260903_01" },
        }),
      }),
    ),
  );
});

it("explains missing authentication configuration without offering an invalid form", async () => {
  mount({
    ...connector,
    unavailable_reason:
      "Create an OAuth2 auth config in Composio Dashboard to connect this application.",
    authentication_methods: [],
    setup_schema: { not: {} },
  });
  expect(
    await screen.findByText(
      "Create an OAuth2 auth config in Composio Dashboard to connect this application.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Start authorization" }),
  ).toBeNull();
  expect(http.POST).not.toHaveBeenCalled();
});

it("restarts a lost link only on an explicit click with fresh browser proof", async () => {
  const expiry = new Date(Date.now() + 60_000).toISOString();
  http.POST.mockResolvedValue({
    data: {
      attempt_id: "csa_lost",
      requires_browser_callback: true,
      redirect_url: null,
      expires_at: expiry,
      connection,
    },
    response: new Response(),
  });
  http.GET.mockResolvedValue({ data: connection, response: new Response() });
  mount();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  const restart = await screen.findByRole("button", {
    name: "Restart authorization",
  });
  const original = readAuthorization();
  expect(http.POST).toHaveBeenCalledTimes(1);
  await userEvent.click(restart);
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(2));
  const [path, request] = http.POST.mock.calls[1];
  expect(path).toBe("/api/v1/connector-connections/{connection_id}/reconnect");
  expect(request.body.browser_nonce).not.toBe(original?.browser_nonce);
});

it("retains a created connection when authorization fails and retries the same command", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/connectors/{connector_key}")
      ? connector
      : { ...connection, workspace_id: "ws_test" },
    response: new Response(),
  }));
  http.POST.mockImplementation(async (path: string) => {
    if (path === "/api/v1/workspaces/{workspace}/connector-connections")
      return { data: connection, response: new Response() };
    throw new Error("Response lost");
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <ConnectionSetup connector={connector} />
    </QueryClientProvider>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Retry authorization request" }),
  );
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(3));
  expect(
    http.POST.mock.calls.filter(
      ([path]) =>
        path === "/api/v1/workspaces/{workspace}/connector-connections",
    ),
  ).toHaveLength(1);
  expect(http.POST.mock.calls[2]).toEqual(http.POST.mock.calls[1]);
});

it("explains unsupported reauthorization and stops offering the rejected action", async () => {
  const { ApiError } = await import("@converge.ai/a13n");
  http.POST.mockRejectedValue(
    new ApiError(
      409,
      "reconnect_unsupported",
      "Reauthorization unsupported",
      {},
      null,
    ),
  );
  mount();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  expect(
    await screen.findByText(
      "This provider cannot reauthorize an existing account. Create a new connection, authorize it, then select it in your agent settings.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Start authorization" }),
  ).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "Back to connections" })
      .getAttribute("href"),
  ).toBe("/workspace/design/connections");
  expect(http.POST).toHaveBeenCalledTimes(1);
});

it("does not offer in-place authorization for a ready Composio account", async () => {
  http.GET.mockResolvedValue({
    data: { type: "composio" },
    response: new Response(),
  });
  mount(connector, { ...connection, status: "ready" });
  expect(
    await screen.findByText(
      "This provider cannot reauthorize an existing account. Create a new connection, authorize it, then select it in your agent settings.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Start authorization" }),
  ).toBeNull();
  expect(http.POST).not.toHaveBeenCalled();
});

it("offers an explicit restart after reopening an already-started setup", async () => {
  const { ApiError } = await import("@converge.ai/a13n");
  http.POST.mockRejectedValueOnce(
    new ApiError(
      409,
      "setup_already_started",
      "Authorization started",
      {},
      null,
    ),
  ).mockResolvedValue({
    data: {
      attempt_id: "csa_restart",
      requires_browser_callback: true,
      redirect_url: "https://connect.composio.dev/link/restart",
      expires_at: new Date(Date.now() + 60_000).toISOString(),
      connection,
    },
    response: new Response(),
  });
  http.GET.mockResolvedValue({ data: connection, response: new Response() });
  mount();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  const restart = await screen.findByRole("button", {
    name: "Restart authorization",
  });
  expect(http.POST).toHaveBeenCalledTimes(1);
  const original = readAuthorization();
  await userEvent.click(restart);
  await screen.findByRole("link", { name: "Continue authorization" });
  expect(http.POST.mock.calls[1][0]).toBe(
    "/api/v1/connector-connections/{connection_id}/reconnect",
  );
  expect(readAuthorization()?.browser_nonce).not.toBe(original?.browser_nonce);
  expect(readAuthorization()?.attempt_id).toBe("csa_restart");
});
