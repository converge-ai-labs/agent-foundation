import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import type { Source } from "./api";
import { DataSourcesPage } from "./sources";
import { DevicesPage } from "./devices";
import { EnvironmentMonitoring, MetricsSourcePicker } from "./environment";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  PUT: vi.fn(),
  DELETE: vi.fn(),
  admin: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) => verb !== "admin" || state.admin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const source: Source = {
  id: "mds_test",
  name: "Test receiver",
  organization_id: "org_test",
  workspace_id: "ws_test",
  config: {
    collection: "native",
    export: { endpoint: "https://receiver.example.com" },
    query: { endpoint: "https://prom.example.com" },
  },
  enabled: true,
  builtin: false,
  version: 1,
  has_export_credentials: true,
  has_query_credentials: true,
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-10-19T00:00:00Z",
  updated_at: "2026-10-19T00:00:00Z",
};
const environment: Schema["EnvironmentView"] = {
  id: "env_test",
  name: "Test environment",
  organization_id: "org_test",
  workspace_id: "ws_test",
  provider_id: "eprov_test",
  template_id: "envtpl_test",
  device_id: null,
  endpoint: null,
  owner_principal_id: null,
  status: "ready",
  operation_id: null,
  operation_started_at: null,
  failure: null,
  last_used_at: null,
  version: 1,
  created_by_id: "usr_test",
  created_at: source.created_at,
  updated_at: source.updated_at,
  metrics_data_source_id: source.id,
  metrics_labels: {},
};
const response = (data: unknown) => ({ data, response: new Response() });
beforeEach(() => {
  vi.clearAllMocks();
  state.admin = true;
  state.GET.mockImplementation(async (path: string) =>
    response(
      path === "/api/v1/metrics-data-sources"
        ? { items: [source], next_cursor: null }
        : path.endsWith("capabilities")
          ? {
              proxy: true,
              collectors: [
                { kind: "alloy", available: false },
                { kind: "vmagent", available: false },
              ],
            }
          : path.endsWith("deployment-preset")
            ? null
            : { items: [], next_cursor: null },
    ),
  );
  state.PATCH.mockResolvedValue(response(source));
  state.PUT.mockResolvedValue(response(environment));
});
function setup(children: ReactNode) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>,
  );
  return { cache, user: userEvent.setup() };
}
it("keeps, replaces and explicitly clears independent source credentials with the opening ETag", async () => {
  const { user } = setup(<DataSourcesPage />);
  await user.click(await screen.findByText(source.name));
  let dialog = await screen.findByRole("dialog");
  await user.click(
    within(dialog).getByRole("button", { name: "Save data source" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(state.PATCH.mock.calls[0][1].body.export_credentials).toBeUndefined();
  expect(state.PATCH.mock.calls[0][1].body.query_credentials).toBeUndefined();
  await user.click(screen.getByText(source.name));
  dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getAllByRole("button", { name: "Clear" })[0]);
  await user.click(
    within(dialog).getAllByRole("button", { name: "Replace" })[0],
  );
  await user.click(within(dialog).getByRole("button", { name: "Add header" }));
  await user.type(
    within(dialog).getByLabelText("Header name 1"),
    "Authorization",
  );
  await user.type(
    within(dialog).getByLabelText("Header value 1"),
    "Bearer test-only",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save data source" }),
  );
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledTimes(2));
  expect(state.PATCH.mock.calls[1][1]).toMatchObject({
    headers: { "If-Match": '"mds_test:1"' },
    body: {
      export_credentials: null,
      query_credentials: { headers: { authorization: "Bearer test-only" } },
    },
  });
});
it("shows saved credential presence to readers without mutation controls", async () => {
  state.admin = false;
  const { user } = setup(<DataSourcesPage />);
  await user.click(await screen.findByText(source.name));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByText(
      /Export credentials.*Saved.*Query credentials.*Saved/,
    ),
  ).toBeTruthy();
  expect(
    within(dialog).queryByRole("button", { name: "Save data source" }),
  ).toBeNull();
  expect(within(dialog).queryByRole("button", { name: "Replace" })).toBeNull();
});
it("keeps missing bindings visible and writes null only after an explicit no-monitoring selection", async () => {
  const { user } = setup(
    <EnvironmentMonitoring
      environment={{ ...environment, metrics_data_source_id: "mds_missing" }}
      etag={'"env_test:1"'}
    />,
  );
  await user.click(screen.getByRole("button", { name: "Configure" }));
  const picker = await screen.findByRole("combobox", {
    name: "Metrics data source",
  });
  expect(picker.textContent).toContain("mds_missing");
  await user.click(picker);
  await user.click(
    await screen.findByRole("option", { name: "No monitoring" }),
  );
  await user.click(screen.getByRole("button", { name: "Save selection" }));
  await waitFor(() => expect(state.PUT).toHaveBeenCalledTimes(1));
  expect(state.PUT.mock.calls[0][1]).toMatchObject({
    headers: { "If-Match": '"env_test:1"' },
    body: { metrics_data_source_id: null, metrics_labels: {} },
  });
});
it("disables unavailable collection routes while retaining and clearing an existing binding", async () => {
  const pull = {
    ...source,
    id: "mds_pull",
    name: "Pull source",
    config: { collection: "pull", collector: "alloy" },
  };
  const proxy = {
    ...source,
    id: "mds_proxy",
    name: "Proxy source",
    config: { collection: "proxy" },
  };
  state.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("capabilities")
        ? {
            proxy: false,
            collectors: [
              { kind: "alloy", available: false },
              { kind: "vmagent", available: true },
            ],
          }
        : { items: [source, pull, proxy], next_cursor: null },
    ),
  );
  const { user, cache } = setup(
    <EnvironmentMonitoring
      environment={{ ...environment, metrics_data_source_id: pull.id }}
      etag={'"env_test:1"'}
    />,
  );
  await screen.findByText(pull.name);
  await user.click(screen.getByRole("button", { name: "Configure" }));
  const picker = screen.getByRole("combobox", { name: "Metrics data source" });
  expect(picker.textContent).toContain(pull.name);
  await user.click(picker);
  expect(
    (await screen.findByRole("option", { name: pull.name })).getAttribute(
      "aria-disabled",
    ),
  ).toBe("true");
  expect(
    screen
      .getByRole("option", { name: proxy.name })
      .getAttribute("aria-disabled"),
  ).toBe("true");
  expect(
    screen
      .getByRole("option", { name: source.name })
      .getAttribute("aria-disabled"),
  ).not.toBe("true");
  cache.setQueryData(["metrics-capabilities", "ws_test"], {
    proxy: true,
    collectors: [{ kind: "alloy", available: true }],
  });
  await waitFor(() =>
    expect(
      screen
        .getByRole("option", { name: pull.name })
        .getAttribute("aria-disabled"),
    ).not.toBe("true"),
  );
  expect(
    screen
      .getByRole("option", { name: proxy.name })
      .getAttribute("aria-disabled"),
  ).not.toBe("true");
  await user.click(screen.getByRole("option", { name: "No monitoring" }));
  await user.click(screen.getByRole("button", { name: "Save selection" }));
  await waitFor(() => expect(state.PUT).toHaveBeenCalledOnce());
  expect(state.PUT.mock.calls[0][1].body.metrics_data_source_id).toBeNull();
});

it("defaults provisioning selection to no monitoring and reports native pending accurately", async () => {
  const change = vi.fn();
  setup(
    <>
      <MetricsSourcePicker value="" onChange={change} />
      <EnvironmentMonitoring environment={environment} etag={'"env_test:1"'} />
    </>,
  );
  expect(
    screen.getByRole("combobox", { name: "Metrics data source" }).textContent,
  ).toContain("No monitoring");
  await screen.findByText(
    "Pending: provision a new target to apply this change.",
  );
  expect(change).not.toHaveBeenCalled();
});
it("reveals a device token once without putting it in React Query caches", async () => {
  state.POST.mockResolvedValue(
    response({
      device: {
        id: "dev_test",
        name: "Lab",
        device_id: "native-lab",
        version: 1,
      },
      token: "test-only-attachment-token",
    }),
  );
  const { user, cache } = setup(<DevicesPage />);
  await user.click(
    await screen.findByRole("button", { name: "Register device" }),
  );
  const dialog = await screen.findByRole("dialog");
  await user.type(within(dialog).getByLabelText("Name"), "Lab");
  await user.type(
    within(dialog).getByLabelText("Native device identity"),
    "native-lab",
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Register device" }),
  );
  await screen.findByText("test-only-attachment-token");
  expect(state.POST.mock.calls[0][1].body).toEqual({
    name: "Lab",
    device_id: "native-lab",
    metrics_data_source_id: null,
  });
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state.data),
    ),
  ).not.toContain("test-only-attachment-token");
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain("test-only-attachment-token");
  await user.click(screen.getByRole("button", { name: "Done" }));
  expect(screen.queryByText("test-only-attachment-token")).toBeNull();
});

it("rejects an empty credential replacement and allows keeping saved credentials", async () => {
  const { user } = setup(<DataSourcesPage />);
  await user.click(await screen.findByText(source.name));
  const dialog = await screen.findByRole("dialog");
  await user.click(
    within(dialog).getAllByRole("button", { name: "Replace" })[0],
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save data source" }),
  );
  await screen.findByText("Add at least one header to replace credentials.");
  expect(state.PATCH).not.toHaveBeenCalled();
  await user.click(
    within(dialog).getByRole("button", { name: "Keep saved credentials" }),
  );
  await user.click(
    within(dialog).getByRole("button", { name: "Save data source" }),
  );
  await waitFor(() => expect(state.PATCH).toHaveBeenCalledTimes(1));
  expect(state.PATCH.mock.calls[0][1].body.export_credentials).toBeUndefined();
});

const deploymentPreset: Schema["DeploymentPreset"] = {
  name: "Deployment metrics",
  config: {
    collection: "pull",
    collector: "vmagent",
    query: { endpoint: "https://metrics.example.com" },
  },
  has_query_credentials: true,
  revision: "a".repeat(64),
};
function offerPreset() {
  const original = state.GET.getMockImplementation()!;
  state.GET.mockImplementation((path: string, ...args: unknown[]) =>
    path.endsWith("deployment-preset")
      ? Promise.resolve(response(deploymentPreset))
      : original(path, ...args),
  );
  state.POST.mockResolvedValue(response(source));
}
it("prefills deployment defaults and only creates after confirmation with explicit credential opt-in", async () => {
  offerPreset();
  const { user } = setup(<DataSourcesPage />);
  await user.click(
    await screen.findByRole("button", { name: "Add data source" }),
  );
  await screen.findByDisplayValue(deploymentPreset.name);
  expect(state.POST).not.toHaveBeenCalled();
  expect(
    screen.getByRole("combobox", { name: "Collection" }).textContent,
  ).toContain("Collector pull");
  await user.click(
    screen.getByRole("combobox", { name: "Use deployment query credentials" }),
  );
  await user.click(screen.getByRole("option", { name: "Yes" }));
  await user.click(screen.getByRole("button", { name: "Save data source" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledOnce());
  expect(state.POST.mock.calls[0][1].body).toMatchObject({
    name: deploymentPreset.name,
    config: deploymentPreset.config,
    deployment_preset_revision: deploymentPreset.revision,
    use_deployment_query_credentials: true,
  });
  expect(state.POST.mock.calls[0][1].body.query_credentials).toBeUndefined();
  expect(state.PUT).not.toHaveBeenCalled();
});
it("clears deployment credential consent when the destination changes and preserves manual edits across refetch", async () => {
  offerPreset();
  const { user, cache } = setup(<DataSourcesPage />);
  await user.click(
    await screen.findByRole("button", { name: "Add data source" }),
  );
  await screen.findByDisplayValue(deploymentPreset.name);
  await user.click(
    screen.getByRole("combobox", { name: "Use deployment query credentials" }),
  );
  await user.click(screen.getByRole("option", { name: "Yes" }));
  const endpoint = screen.getByLabelText("Prometheus query endpoint");
  await user.clear(endpoint);
  await user.type(endpoint, "https://another.example.com");
  cache.setQueryData(["metrics-deployment-preset", "ws_test"], {
    ...deploymentPreset,
    name: "Updated deployment",
  });
  expect(screen.getByDisplayValue("https://another.example.com")).toBeTruthy();
  expect(screen.getByDisplayValue(deploymentPreset.name)).toBeTruthy();
  expect(
    screen.getByRole("combobox", { name: "Use deployment query credentials" })
      .textContent,
  ).toContain("No");
  // Save without touching consent again: manually selecting No hides a broken reset.
  await user.click(screen.getByRole("button", { name: "Save data source" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalledOnce());
  expect(
    state.POST.mock.calls[0][1].body.use_deployment_query_credentials,
  ).toBe(false);
  expect(state.POST.mock.calls[0][1].body.query_credentials).toBeUndefined();
});
