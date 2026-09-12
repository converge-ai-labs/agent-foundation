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
  start: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { POST: mocks.POST, GET: mocks.GET } }),
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
const connection: Schema["Connection"] = {
  id: "conn_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  source: {
    kind: "connector",
    provider_id: "cnr_test",
    connector_key: "github",
  },
  name: "GitHub",
  status: "pending",
  version: 1,
  authorization_generation: 1,
  credential_configured: false,
  created_by: { principal_type: "user", principal_id: "usr_test" },
  created_at: "2026-09-12T00:00:00Z",
  updated_at: "2026-09-12T00:00:00Z",
};
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
function mount(resource?: Schema["Connection"]) {
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.includes("/connectors/{connector_key}")
      ? connector
      : (resource ?? connection),
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
      <ConnectionSetup connection={resource} connector={connector} />
    </QueryClientProvider>,
  );
}
it("submits fixed setup options through the common browser authorization", async () => {
  mount(connection);
  expect(screen.queryByRole("textbox", { name: "toolkit_version" })).toBeNull();
  await userEvent.click(
    await screen.findByRole("button", { name: "Authorize connection" }),
  );
  await waitFor(() =>
    expect(mocks.start).toHaveBeenCalledWith(
      expect.anything(),
      connection,
      "/workspace/design",
      { auth_config_id: "ac_test", toolkit_version: "20260903_01" },
    ),
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
      ready,
      expect.anything(),
      expect.anything(),
    ),
  );
  expect(mocks.POST).not.toHaveBeenCalled();
});
it("retains a created connection when authorization fails and starts again with its current version", async () => {
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
  expect(mocks.POST).toHaveBeenCalledExactlyOnceWith(
    "/api/v1/workspaces/{workspace}/connections",
    expect.objectContaining({
      body: { name: "GitHub", source: connection.source },
    }),
  );
  expect(mocks.start.mock.calls[1][1].id).toBe(connection.id);
});
