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
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
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

function mount(definition = connector) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <ConnectionSetup connection={connection} connector={definition} />
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
    screen.getByRole("button", { name: "Start authorization" }),
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

it("explains missing authentication configuration without offering an invalid form", () => {
  mount({
    ...connector,
    authentication_methods: [],
    setup_schema: { not: {} },
  });
  expect(
    screen.getByText(
      "Configure an OAuth2 auth config in the provider before connecting.",
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
    screen.getByRole("button", { name: "Start authorization" }),
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
