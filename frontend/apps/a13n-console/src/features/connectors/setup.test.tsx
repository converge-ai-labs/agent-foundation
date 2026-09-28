// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { ConnectionSetup } from "./setup";
const mocks = vi.hoisted(() => ({
  POST: vi.fn(),
  GET: vi.fn(),
  PATCH: vi.fn(),
  start: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { POST: mocks.POST, GET: mocks.GET, PATCH: mocks.PATCH },
    workspace: () => ({ POST: mocks.POST, GET: mocks.GET, PATCH: mocks.PATCH }),
  }),
}));
vi.mock("../connections/authorization-context", () => ({
  startBrowserAuthorization: mocks.start,
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "ws_test", name: "Design" },
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
const setup = { auth_config_id: "ac_test", toolkit_version: "20260903_01" };
const connection: Schema["Connection"] = {
  id: "conn_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "composio",
  name: "GitHub",
  config: { app: "github", actions: ["GITHUB_GET_REPO"], setup },
  auth: "account",
  connector_provider_id: "cprov_test",
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
const connector: Schema["ConnectorApp"] = {
  key: "github",
  name: "GitHub",
  description: null,
  logo_url: null,
  unavailable_reason: null,
  authentication_methods: ["OAUTH2"],
  setup_schema: {
    type: "object",
    properties: {
      auth_config_id: { const: "ac_test" },
      toolkit_version: { const: "20260903_01" },
    },
    required: ["auth_config_id", "toolkit_version"],
  },
};
const provider: Schema["Provider"] = {
  id: "cprov_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "composio",
  name: "Composio",
  config: {},
  credential_configured: true,
  header_names: [],
  enabled: true,
  version: 1,
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};
const action = (name: string): Schema["ToolInfo"] => ({
  name,
  description: null,
  input_schema: {},
});
function mount(
  resource?: Schema["Connection"],
  {
    app = connector,
    actions = [action("GITHUB_GET_REPO")],
  }: { app?: Schema["ConnectorApp"]; actions?: Schema["ToolInfo"][] } = {},
) {
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/apps/{app}/actions")
      ? { items: actions, next_cursor: null }
      : path.endsWith("/apps/{app}")
        ? app
        : (resource ?? connection),
    response: new Response(),
  }));
  mocks.PATCH.mockImplementation(async () => ({
    data: { ...(resource ?? connection), version: 9 },
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
      {resource ? (
        <ConnectionSetup connection={resource} />
      ) : (
        <ConnectionSetup connector={connector} provider={provider} />
      )}
    </QueryClientProvider>,
  );
}
it("saves fixed setup options with the configuration before the common browser authorization", async () => {
  mount(connection);
  expect(screen.queryByRole("textbox", { name: "toolkit_version" })).toBeNull();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  await waitFor(() =>
    expect(mocks.start).toHaveBeenCalledWith(
      expect.anything(),
      { ...connection, version: 9 },
      "/workspace/design",
    ),
  );
  expect(mocks.PATCH).toHaveBeenCalledWith(
    "/api/v1/connections/{connection_id}",
    {
      params: { path: { connection_id: "conn_test" } },
      headers: { "If-Match": '"conn_test:1"' },
      body: { config: connection.config },
    },
  );
});
it("reauthorizes a ready account under its stable connection ID", async () => {
  const ready = { ...connection, status: "ready" as const, version: 7 };
  mount(ready);
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  await waitFor(() =>
    expect(mocks.start).toHaveBeenCalledWith(
      expect.anything(),
      { ...ready, version: 9 },
      expect.anything(),
    ),
  );
  expect(mocks.POST).not.toHaveBeenCalled();
});
it("creates the connection with every app action that fits, then retains it when authorization fails", async () => {
  mocks.POST.mockResolvedValue({ data: connection, response: new Response() });
  mocks.start
    .mockRejectedValueOnce(new Error("Response lost"))
    .mockResolvedValueOnce({ id: "auth_retry" });
  mount();
  await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await screen.findByText("Response lost");
  await userEvent.click(
    screen.getByRole("button", { name: "Authorize connection" }),
  );
  await waitFor(() => expect(mocks.start).toHaveBeenCalledTimes(2));
  expect(mocks.POST).toHaveBeenCalledExactlyOnceWith("/api/v1/connections", {
    body: {
      type: "composio",
      name: "GitHub",
      config: connection.config,
      auth: "account",
      connector_provider_id: "cprov_test",
    },
  });
  expect(mocks.start.mock.calls[0][1]).toBe(connection);
  expect(mocks.start.mock.calls[1][1]).toEqual({ ...connection, version: 9 });
});
it("asks for a choice when the app offers more tools than a connection may", async () => {
  mocks.POST.mockResolvedValue({ data: connection, response: new Response() });
  mocks.start.mockReturnValue(new Promise(() => {}));
  const actions = Array.from({ length: 130 }, (_, index) =>
    action(`GITHUB_ACTION_${index}`),
  );
  mount(undefined, { actions });
  await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await screen.findByText("Select at least one tool.");
  expect(mocks.POST).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("checkbox", { name: "GITHUB_ACTION_1" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(mocks.start).toHaveBeenCalledOnce());
  expect(mocks.POST.mock.calls[0][1].body.config.actions).toEqual([
    "GITHUB_ACTION_1",
  ]);
});
it("keeps the saved tools of an existing connection", async () => {
  mount(connection, {
    actions: [action("GITHUB_GET_REPO"), action("GITHUB_LIST_ISSUES")],
  });
  const saved = await screen.findByRole("checkbox", {
    name: "GITHUB_GET_REPO",
  });
  expect(saved.getAttribute("aria-checked")).toBe("true");
  expect(
    screen
      .getByRole("checkbox", { name: "GITHUB_LIST_ISSUES" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});
it("treats an app without authentication methods as unavailable", async () => {
  mount(undefined, { app: { ...connector, authentication_methods: [] } });
  await userEvent.click(await screen.findByRole("button", { name: "Connect" }));
  await screen.findByText("This connector is unavailable from its provider.");
  expect(mocks.POST).not.toHaveBeenCalled();
  expect(mocks.start).not.toHaveBeenCalled();
});
