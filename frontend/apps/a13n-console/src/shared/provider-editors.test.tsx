// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConnectorProviders } from "../features/connectors/providers";
import { EnvironmentProviders } from "../features/environments/providers";
import { MemoryProviders } from "../features/memories/providers";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
    can: () => true,
  }),
  useAccess: () => ({
    can: () => true,
    organizationCan: () => true,
    organization: { id: "org_test" },
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

const surfaces = ["connector", "environment", "memory"] as const;

function setup(surface: string, overrides: Record<string, unknown> = {}) {
  const connector = surface === "connector";
  const type = { connector: "composio", environment: "e2b" }[surface] ?? "mem0";
  const provider = {
    id: `${surface}_test`,
    name: "Existing provider",
    type,
    workspace_id: "ws_test",
    config: {},
    enabled: true,
    version: 3,
    credential_configured: true,
  };
  const definition = {
    authentication: { mode: "required", cases: [] },
    setup_url: null,
    setup_label: null,
    supports_test: connector,
    type,
    display_name: connector ? "Composio" : type,
    configuration_schema: {
      type: "object",
      properties: {},
      required: [],
    },
    credential_schema: {
      type: "object",
      properties: { api_key: { type: "string" } },
      required: ["api_key"],
    },
    ...overrides,
  };
  const listPath = `/api/v1/${surface}-providers`;
  const detailPath = `${listPath}/{provider_id}`;
  // The Service's strong ETag of the provider's `{id, version}`.
  const response = () =>
    new Response(null, { headers: { ETag: `"${provider.id}:3"` } });
  http.GET.mockImplementation(async (path: string) => {
    if (path === "/api/v1/provider-types/{kind}")
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
        <ConnectorProviders />
      ) : surface === "memory" ? (
        <MemoryProviders />
      ) : (
        <EnvironmentProviders />
      )}
    </QueryClientProvider>,
  );
  return { connector, type, listPath, detailPath, provider };
}

it.each(surfaces)(
  "creates a workspace %s provider after loading the first list page",
  async (surface) => {
    const user = userEvent.setup();
    const { connector, type, listPath, detailPath } = setup(surface);
    await screen.findByText("Existing provider");
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    // Every category starts creation from the provider catalog.
    expect(screen.queryByPlaceholderText("Search providers…")).toBeNull();
    await user.click(
      await screen.findByRole("button", {
        name: new RegExp(connector ? "Composio" : type),
      }),
    );
    const nameField = screen.getByRole("textbox", {
      name: "Name",
    }) as HTMLInputElement;
    expect(nameField.value).toBe(connector ? "Composio" : type);
    await user.clear(nameField);
    await user.type(nameField, "New provider");
    const credentials = {
      api_key: connector ? "test-project" : "test-environment",
    };
    for (const [name, value] of Object.entries(credentials)) {
      const field = screen.getByLabelText(name) as HTMLInputElement;
      expect(field.type).toBe("password");
      await user.type(field, value);
    }
    expect(
      screen.queryByRole("button", { name: "Test connection" }),
    ).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Replace credentials" }),
    ).toBeNull();
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    await waitFor(() =>
      expect(http.POST).toHaveBeenCalledWith(
        listPath,
        expect.objectContaining({
          body: {
            name: "New provider",
            type,
            config: {},
            credential: credentials,
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
    // The catalog comes back, so the previous draft cannot leak forward.
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
    await screen.findByRole("button", {
      name: new RegExp(connector ? "Composio" : type),
    });
  },
);

it.each(surfaces)(
  "edits the exact workspace %s provider after loading the list",
  async (surface) => {
    const user = userEvent.setup();
    const { connector, detailPath, provider } = setup(surface);
    await screen.findByText("Existing provider");
    // Every provider surface carries the shared row overflow menu.
    expect(screen.getByRole("columnheader", { name: "Actions" })).toBeTruthy();
    const row = screen.getByRole("row", { name: /Existing provider/ });
    row.focus();
    await user.keyboard("{Enter}");
    const name = await screen.findByRole("textbox", { name: "Name" });
    expect((name as HTMLInputElement).value).toBe(provider.name);
    // The dialog title names the provider, so its type is the description.
    expect(
      screen.queryByRole("combobox", { name: "Provider type" }),
    ).toBeNull();
    expect(screen.queryByRole("group", { name: "Provider type" })).toBeNull();
    expect(screen.getByRole("dialog").textContent).toContain(
      connector ? "Composio" : provider.type,
    );
    // The saved secret is stated in one row rather than offered as an input.
    expect(screen.queryByLabelText("api_key")).toBeNull();
    expect(screen.getByText("Saved")).toBeTruthy();
    expect(http.GET).toHaveBeenCalledWith(detailPath, expect.anything());
    await user.clear(name);
    await user.type(name, "Renamed provider");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(http.PATCH).toHaveBeenCalledWith(
        detailPath,
        expect.objectContaining({
          params: {
            path: { provider_id: provider.id },
          },
          headers: { "If-Match": `"${provider.id}:3"` },
          body: { name: "Renamed provider", enabled: true },
        }),
      ),
    );
    expect(http.POST).not.toHaveBeenCalled();
  },
);

it("checks a saved environment provider's connection with its read-only probe", async () => {
  const user = userEvent.setup();
  const { detailPath, provider } = setup("environment", {
    supports_test: true,
  });
  await screen.findByText("Existing provider");
  const row = screen.getByRole("row", { name: /Existing provider/ });
  row.focus();
  await user.keyboard("{Enter}");
  const name = await screen.findByRole("textbox", { name: "Name" });
  http.POST.mockResolvedValueOnce({
    data: {
      provider_id: provider.id,
      provider_version: 3,
      status: "failed",
      message: "provider_unavailable",
    },
  });
  await user.click(screen.getByRole("button", { name: "Check connection" }));
  expect(await screen.findByText("provider_unavailable")).toBeTruthy();
  expect(http.POST).toHaveBeenCalledWith(`${detailPath}/test`, {
    params: {
      path: { provider_id: provider.id },
    },
  });
  // An unsaved draft is not what the probe would check.
  await user.type(name, " renamed");
  expect(
    (
      screen.getByRole("button", {
        name: "Check connection",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

it("saves connector name, credentials and enabled state in one atomic update", async () => {
  const user = userEvent.setup();
  const { detailPath, provider } = setup("connector");
  const response = new Response(null);
  http.PATCH.mockResolvedValue({
    data: { ...provider, name: "Renamed", version: 4 },
    response,
  });
  await user.click(await screen.findByText("Existing provider"));
  const name = await screen.findByRole("textbox", { name: "Name" });
  await user.clear(name);
  await user.type(name, "Renamed");
  await user.click(screen.getByRole("button", { name: "Replace" }));
  await user.type(screen.getByLabelText("api_key"), "new-project");
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  expect(http.POST).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(1));
  expect(http.PATCH).toHaveBeenCalledWith(
    detailPath,
    expect.objectContaining({
      body: {
        name: "Renamed",
        enabled: false,
        credential: {
          api_key: "new-project",
        },
      },
    }),
  );
  expect(http.POST).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("preserves structured Connector credentials and setup help", async () => {
  const user = userEvent.setup();
  const { listPath } = setup("connector", {
    setup_url: "https://example.com/keys",
    setup_label: "Create Connector credentials",
    credential_schema: {
      type: "object",
      properties: {
        authorization: {
          type: "object",
          properties: { token: { type: "string", title: "Token" } },
          required: ["token"],
        },
        revision: { type: "integer", title: "Revision" },
        tier: { type: "string", title: "Tier", enum: ["sandbox", "live"] },
      },
      required: ["authorization", "revision", "tier"],
    },
  });
  await screen.findByText("Existing provider");
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.click(await screen.findByRole("button", { name: /Composio/ }));
  expect(
    screen
      .getByRole("link", { name: "Create Connector credentials" })
      .getAttribute("href"),
  ).toBe("https://example.com/keys");
  await user.type(screen.getByLabelText("Token"), "nested-secret");
  await user.type(screen.getByLabelText("Revision"), "7");
  await user.click(screen.getByRole("combobox", { name: "Tier" }));
  await user.click(await screen.findByRole("option", { name: "sandbox" }));
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      listPath,
      expect.objectContaining({
        body: expect.objectContaining({
          credential: {
            authorization: { token: "nested-secret" },
            revision: 7,
            tier: "sandbox",
          },
        }),
      }),
    ),
  );
});

it("allows explicit removal of required Connector credentials", async () => {
  const user = userEvent.setup();
  const { detailPath } = setup("connector");
  await user.click(await screen.findByText("Existing provider"));
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(http.PATCH).toHaveBeenCalledWith(
      detailPath,
      expect.objectContaining({
        body: expect.objectContaining({ credential: null }),
      }),
    ),
  );
});
