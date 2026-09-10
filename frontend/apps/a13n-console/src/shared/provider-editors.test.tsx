// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConnectorProviders } from "../features/connectors/providers";
import { EnvironmentProviders } from "../features/environments/providers";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "ws_test" },
    can: () => true,
  }),
  useAccess: () => ({
    can: () => true,
    organizationAdmin: true,
    workspace: { id: "ws_test" },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  // Radix Select uses pointer capture and scroll APIs absent from jsdom.
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const cases = ["workspace", "organization"].flatMap((kind) =>
  ["connector", "environment"].map((surface) => ({
    kind: kind as "workspace" | "organization",
    surface,
  })),
);

function setup(kind: "workspace" | "organization", surface: string) {
  const connector = surface === "connector";
  const scope = { kind, id: kind === "workspace" ? "ws_test" : "org_test" };
  const type = connector ? "openconnector" : "e2b";
  const provider = {
    id: "provider_test",
    name: "Existing provider",
    type,
    organization_id: "org_test",
    workspace_id: kind === "workspace" ? "ws_test" : null,
    configuration: connector ? { enabled_services: ["github"] } : {},
    status: "active",
    enabled: true,
    version: 3,
    credential_configured: true,
  };
  const definition = {
    type,
    display_name: connector ? "OpenConnector" : "e2b",
    configuration_schema: {
      type: "object",
      properties: connector
        ? { enabled_services: { type: "array", items: { type: "string" } } }
        : {},
      required: connector ? ["enabled_services"] : [],
    },
    credential_schema: {
      type: "object",
      properties: connector
        ? {
            project_api_key: { type: "string" },
            catalog_api_key: { type: "string" },
          }
        : { api_key: { type: "string" } },
      required: connector
        ? ["project_api_key", "catalog_api_key"]
        : ["api_key"],
    },
  };
  const listPath = `/api/v1/${kind === "workspace" ? "workspaces/{workspace}" : "organizations/{organization}"}/${surface}-providers`;
  const detailPath = `/api/v1/${surface}-providers/{${connector ? "connector_provider_id" : "resource_id"}}`;
  const response = () => new Response(null, { headers: { ETag: '"v3"' } });
  http.GET.mockImplementation(async (path: string) => {
    if (path === `/api/v1/${surface}-provider-types`)
      return { data: { items: [definition] }, response: response() };
    if (path === listPath)
      return {
        data: { items: [provider], next_cursor: null },
        response: response(),
      };
    if (path === detailPath) return { data: provider, response: response() };
    throw new Error(`Unexpected GET ${path}`);
  });
  http.POST.mockResolvedValue({ data: provider, response: response() });
  http.PATCH.mockResolvedValue({ data: provider, response: response() });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      {connector ? (
        <ConnectorProviders scope={scope} />
      ) : (
        <EnvironmentProviders scope={scope} />
      )}
    </QueryClientProvider>,
  );
  return { connector, type, listPath, detailPath, provider };
}

it.each(cases)(
  "creates a $kind $surface provider after loading the first list page",
  async ({ kind, surface }) => {
    const user = userEvent.setup();
    const { connector, type, listPath, detailPath } = setup(kind, surface);
    await screen.findByText("Existing provider");
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    await user.click(screen.getByRole("combobox", { name: "Provider type" }));
    await user.click(
      await screen.findByRole("option", {
        name: connector ? "OpenConnector" : type,
      }),
    );
    await user.type(
      screen.getByRole("textbox", { name: "Name" }),
      "New provider",
    );
    const credentials = connector
      ? { project_api_key: "test-project", catalog_api_key: "test-catalog" }
      : { api_key: "test-environment" };
    for (const [name, value] of Object.entries(credentials)) {
      const field = screen.getByLabelText(name) as HTMLInputElement;
      expect(field.type).toBe("password");
      await user.type(field, value);
    }
    if (connector) {
      await user.click(
        screen.getByRole("textbox", { name: "enabled_services" }),
      );
      await user.paste('["github"]');
    }
    expect(
      screen.queryByRole("button", { name: "Test connection" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Replace credentials" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(http.POST).toHaveBeenCalledWith(
        listPath,
        expect.objectContaining({
          body: {
            name: "New provider",
            type,
            configuration: connector ? { enabled_services: ["github"] } : {},
            [connector ? "credentials" : "credential"]: credentials,
          },
        }),
      ),
    );
    expect(http.PATCH).not.toHaveBeenCalled();
    expect(http.GET.mock.calls.some(([path]) => path === detailPath)).toBe(
      false,
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    expect(
      (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
    ).toBe("");
    expect(
      screen.getByRole("combobox", { name: "Provider type" }).textContent,
    ).toContain("Search providers…");
  },
);

it.each(cases)(
  "edits the exact $kind $surface provider after loading the list",
  async ({ kind, surface }) => {
    const user = userEvent.setup();
    const { connector, detailPath, provider } = setup(kind, surface);
    await screen.findByText("Existing provider");
    expect(screen.queryByRole("columnheader", { name: "Actions" })).toBeNull();
    const row = screen.getByRole("row", { name: /Existing provider/ });
    row.focus();
    await user.keyboard("{Enter}");
    const name = await screen.findByRole("textbox", { name: "Name" });
    expect((name as HTMLInputElement).value).toBe(provider.name);
    expect(
      screen.queryByRole("combobox", { name: "Provider type" }),
    ).toBeNull();
    expect(http.GET).toHaveBeenCalledWith(detailPath, expect.anything());
    await user.clear(name);
    await user.type(name, "Renamed provider");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(http.PATCH).toHaveBeenCalledWith(
        `/api/v1/${surface}-providers/{${connector ? "connector_provider_id" : "provider_id"}}`,
        expect.objectContaining({
          params: expect.objectContaining({
            path: {
              [connector ? "connector_provider_id" : "provider_id"]:
                provider.id,
            },
            ...(!connector && { header: { "If-Match": '"v3"' } }),
          }),
          body: {
            name: "Renamed provider",
            ...(connector
              ? { expected_version: 3, status: "active" }
              : { enabled: true }),
          },
        }),
      ),
    );
    expect(http.POST).not.toHaveBeenCalled();
  },
);

it("saves connector name, credentials and enabled state in one atomic update", async () => {
  const user = userEvent.setup();
  const { provider } = setup("workspace", "connector");
  const response = new Response(null);
  http.PATCH.mockResolvedValue({
    data: { ...provider, name: "Renamed", version: 4 },
    response,
  });
  await user.click(await screen.findByText("Existing provider"));
  const name = await screen.findByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "Renamed");
  await user.type(screen.getByLabelText("project_api_key"), "new-project");
  await user.type(screen.getByLabelText("catalog_api_key"), "new-catalog");
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(1));
  expect(http.PATCH).toHaveBeenCalledWith(
    "/api/v1/connector-providers/{connector_provider_id}",
    expect.objectContaining({
      body: {
        name: "Renamed",
        expected_version: 3,
        status: "disabled",
        credentials: {
          project_api_key: "new-project",
          catalog_api_key: "new-catalog",
        },
      },
    }),
  );
  expect(http.POST).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
