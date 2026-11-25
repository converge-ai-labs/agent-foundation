import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { EnvironmentInstances } from "./instances";
import { EnvironmentsPage } from "./page";
import { Page } from "../../shared/page";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  DELETE: vi.fn(),
}));
const access = vi.hoisted(() => ({ can: vi.fn(() => true) }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    organization: { id: "org_test" },
    can: access.can,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

const collections: Record<string, unknown[]> = {
  "/api/v1/metrics-data-sources": [
    {
      id: "mds_local",
      name: "Local Cache",
      enabled: true,
      builtin: true,
      config: { collection: "proxy" },
    },
  ],
  "/api/v1/environment-providers": [
    { id: "eprov_docker", name: "Docker host", type: "docker", enabled: true },
  ],
  "/api/v1/environment-templates": [
    {
      id: "envtpl_python",
      name: "Python",
      description: null,
      enabled: true,
      version: 2,
    },
    {
      id: "envtpl_retired",
      name: "Retired",
      description: null,
      enabled: false,
      version: 1,
    },
  ],
};

beforeEach(() => {
  access.can.mockReturnValue(true);
  http.DELETE.mockReset().mockResolvedValue({ data: {} });
  http.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/metrics-data-sources/capabilities"
        ? { proxy: true, native: true, pull: false, collectors: [] }
        : { items: collections[path] ?? [], next_cursor: null },
  }));
  http.POST.mockReset().mockResolvedValue({ data: { id: "env_test" } });
});

async function openCreate() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Page title="Environment instances">
          <EnvironmentInstances />
        </Page>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByText("No environments yet");
  expect(
    screen.getByRole("link", { name: "Manage providers" }).getAttribute("href"),
  ).toBe("/workspace/ws_test/settings/providers?category=environments");
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  return { cache, user };
}

it("keeps provider navigation beside an empty template collection", async () => {
  http.GET.mockResolvedValue({ data: { items: [], next_cursor: null } });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <EnvironmentsPage section="templates" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("No environment templates");
  const create = screen.getByRole("link", { name: "Create template" });
  expect(create.getAttribute("href")).toBe(
    "/workspace/ws_test/environments/new",
  );
  expect(create.closest("header")).toBeNull();
  const manage = screen.getByRole("link", { name: "Manage providers" });
  expect(manage.closest("header")).not.toBeNull();
  expect(manage.getAttribute("href")).toBe(
    "/workspace/ws_test/settings/providers?category=environments",
  );
  cache.clear();
});

it.each([null, "mds_local"])(
  "allocates a managed environment with monitoring selection %s",
  async (sourceId) => {
    const { cache, user } = await openCreate();
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Scratch");
    await user.click(screen.getByRole("combobox", { name: "Template" }));
    const python = await screen.findByRole("option", { name: /Python/ });
    expect(screen.queryByRole("option", { name: /Retired/ })).toBeNull();
    await user.click(python);
    if (sourceId) {
      await user.click(
        screen.getByRole("combobox", { name: "Metrics data source" }),
      );
      await user.click(
        await screen.findByRole("option", { name: "Local Cache" }),
      );
    }
    await user.click(
      screen.getByRole("button", { name: "Create environment" }),
    );
    await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
    expect(http.POST.mock.calls[0]).toEqual([
      "/api/v1/environments",
      {
        body: {
          template_id: "envtpl_python",
          name: "Scratch",
          metrics_data_source_id: sourceId,
        },
      },
    ]);
    cache.clear();
  },
);

it("submits volume mounts selected for an E2B instance", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/environment-providers/{provider_id}"
        ? { type: "e2b" }
        : {
            items:
              path === "/api/v1/environment-templates"
                ? [
                    {
                      id: "envtpl_e2b",
                      name: "E2B sandbox",
                      enabled: true,
                      provider_id: "eprov_e2b",
                      config: {
                        recipe: {
                          volume_mounts: { "/data": "template-volume" },
                        },
                      },
                    },
                  ]
                : (collections[path] ?? []),
            next_cursor: null,
          },
  }));
  const { cache, user } = await openCreate();
  await user.click(screen.getByRole("combobox", { name: "Template" }));
  await user.click(await screen.findByRole("option", { name: /E2B sandbox/ }));
  const volume = await screen.findByDisplayValue("template-volume");
  await user.clear(volume);
  await user.type(volume, "project-a");
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith("/api/v1/environments", {
      body: {
        template_id: "envtpl_e2b",
        metrics_data_source_id: null,
        recipe_overrides: { volume_mounts: { "/data": "project-a" } },
      },
    }),
  );
  cache.clear();
});

it("registers an external target by its endpoint and token, with no provider to pick", async () => {
  const { cache, user } = await openCreate();
  await user.click(screen.getByRole("combobox", { name: "Ownership" }));
  await user.click(
    await screen.findByRole("option", { name: /^External environment/ }),
  );
  expect(screen.queryByRole("combobox", { name: "Provider" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Template" })).toBeNull();
  await user.type(
    screen.getByLabelText("Endpoint URL"),
    " https://laptop.example.com:8443 ",
  );
  const token = screen.getByLabelText("Token") as HTMLInputElement;
  expect(token.type).toBe("password");
  await user.type(token, "daemon-token");
  await user.click(screen.getByRole("button", { name: "Create environment" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  expect(http.POST.mock.calls[0]).toEqual([
    "/api/v1/environments",
    expect.objectContaining({
      body: {
        endpoint: "https://laptop.example.com:8443",
        token: "daemon-token",
        metrics_data_source_id: null,
      },
    }),
  ]);
  cache.clear();
});

it("distinguishes lost and permanently unavailable targets from ready instances", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items:
        path === "/api/v1/environments"
          ? [
              {
                id: "env_lost",
                name: "Lost sandbox",
                status: "ready",
                failure: { code: "environment_lost", permanent: true },
              },
              {
                id: "env_unavailable",
                name: "Unavailable sandbox",
                status: "ready",
                failure: { code: "provider_invalid", permanent: true },
              },
              {
                id: "env_ready",
                name: "Healthy sandbox",
                status: "ready",
                failure: null,
              },
              {
                id: "env_retrying",
                name: "Retrying sandbox",
                status: "ready",
                failure: {
                  code: "provider_credential_changed",
                  permanent: false,
                },
              },
            ]
          : (collections[path] ?? []),
      next_cursor: null,
    },
  }));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Page title="Environment instances">
          <EnvironmentInstances />
        </Page>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("Lost sandbox");
  expect(screen.getByText("Environment lost")).toBeTruthy();
  expect(screen.getByText("Environment unavailable")).toBeTruthy();
  expect(screen.getAllByText("state.ready")).toHaveLength(2);
  cache.clear();
});

const listedEnvironment = {
  id: "env_test",
  workspace_id: "ws_test",
  name: "Scratch sandbox",
  kind: "managed",
  template_id: "envtpl_python",
  provider_id: "eprov_docker",
  status: "ready",
  version: 3,
  failure: null,
  updated_at: "2026-11-17T00:00:00Z",
  desktop_preview: "not_configured",
};

function openInstances({ writable = true, detailFails = false } = {}) {
  access.can.mockImplementation(() => writable);
  const initialRead = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path: string, ...args: unknown[]) => {
    if (path === "/api/v1/environments")
      return { data: { items: [listedEnvironment], next_cursor: null } };
    if (path === "/api/v1/environments/{environment_id}") {
      if (detailFails) throw new Error("Environment read failed");
      return {
        data: listedEnvironment,
        response: new Response(null, { headers: { ETag: '"env_test:3"' } }),
      };
    }
    if (path === "/api/v1/provider-types/{kind}")
      return {
        data: {
          items: [
            { type: "docker", supports_stop: true, supports_destroy: true },
          ],
        },
      };
    return initialRead(path, ...args);
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 15_000 } },
  });
  cache.setQueryData(["environment", listedEnvironment.id], {
    value: { ...listedEnvironment, version: 1 },
    etag: '"env_test:1"',
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <EnvironmentInstances />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { cache, user: userEvent.setup() };
}

it("deletes from the row menu only after confirmation, uses the current ETag and preserves failures", async () => {
  const { cache, user } = openInstances();
  await screen.findByText("Scratch sandbox");
  expect(
    http.GET.mock.calls.some(
      ([path]) => path === "/api/v1/environments/{environment_id}",
    ),
  ).toBe(false);
  await user.click(screen.getByRole("button", { name: "Environment actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Delete environment",
  });
  expect(within(dialog).getByText("Scratch sandbox")).toBeTruthy();
  expect(screen.queryByText("Effective retention policy")).toBeNull();
  expect(http.DELETE).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Delete environment" }),
    ).toBeNull(),
  );
  expect(http.DELETE).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Environment actions" }));
  await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
  http.DELETE.mockRejectedValueOnce(new Error("Environment is in use"));
  await user.click(screen.getByRole("button", { name: "Delete environment" }));
  expect(await screen.findByText("Environment is in use")).toBeTruthy();
  expect(http.DELETE).toHaveBeenCalledWith(
    "/api/v1/environments/{environment_id}",
    {
      params: { path: { environment_id: "env_test" } },
      headers: { "If-Match": '"env_test:3"' },
    },
  );
  http.GET.mockClear();
  await user.click(screen.getByRole("button", { name: "Delete environment" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Delete environment" }),
    ).toBeNull(),
  );
  expect(
    http.GET.mock.calls.some(([path]) => path === "/api/v1/environments"),
  ).toBe(true);
  cache.clear();
});

it.each([
  ["read-only access", { writable: false, detailFails: false }],
  ["a failed current-state read", { writable: true, detailFails: true }],
])(
  "keeps details accessible without lifecycle actions for %s",
  async (_name, options) => {
    const { cache, user } = openInstances(options);
    await user.click(
      await screen.findByRole("button", { name: "Environment actions" }),
    );
    expect(
      await screen.findByRole("menuitem", { name: "Details" }),
    ).toBeTruthy();
    if (options.detailFails) await screen.findByText("Environment read failed");
    expect(screen.queryByRole("menuitem", { name: "Stop" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Delete" })).toBeNull();
    if (options.writable === false) {
      expect(
        http.GET.mock.calls.some(
          ([path]) => path === "/api/v1/environments/{environment_id}",
        ),
      ).toBe(false);
      await user.click(screen.getByRole("menuitem", { name: "Details" }));
      await screen.findByText("Effective retention policy");
    }
    cache.clear();
  },
);
