import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { CreateMCP } from "./create";

const http = vi.hoisted(() => ({ POST: vi.fn(), GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

const presets = {
  airtable: {
    key: "airtable",
    name: "Airtable",
    description: "Bases and records",
    endpoint_url: "https://mcp.airtable.com/mcp",
    auth_mode: "oauth",
  },
  "google-compute-engine": {
    key: "google-compute-engine",
    name: "Google Compute Engine",
    description: "Manage compute infrastructure",
    endpoint_url: "https://compute.googleapis.com/mcp",
    auth_mode: "static_headers",
    static_header_names: ["Authorization", "x-goog-user-project"],
  },
  jentic: {
    key: "jentic",
    name: "Jentic",
    description: "Secure API access",
    endpoint_url: "https://api.jentic.com/mcp",
    auth_mode: "static_headers",
    static_header_names: ["x-jentic-api-key"],
  },
} satisfies Record<string, Schema["MCPServer"]>;

it("shows preset authentication as read-only", () => {
  const preset = presets.airtable;
  const cache = new QueryClient();
  render(
    <QueryClientProvider client={cache}>
      <CreateMCP
        onCancel={vi.fn()}
        preset={preset}
        onStarted={vi.fn()}
        onSuccess={vi.fn()}
      />
    </QueryClientProvider>,
  );

  expect(screen.queryByRole("combobox", { name: "Authentication" })).toBeNull();
  expect(
    screen.getByRole("group", { name: "Authentication" }).textContent,
  ).toContain(`auth.${preset.auth_mode}`);
  cache.clear();
});

it("keeps authentication selectable for custom MCP connections", () => {
  const cache = new QueryClient();
  render(
    <QueryClientProvider client={cache}>
      <CreateMCP onCancel={vi.fn()} onStarted={vi.fn()} onSuccess={vi.fn()} />
    </QueryClientProvider>,
  );

  expect(
    screen.getByRole("combobox", { name: "Authentication" }),
  ).toBeDefined();
  cache.clear();
});

it.each(["google-compute-engine", "jentic"] as const)(
  "%s prefills header names and sends values only through the credential endpoint",
  async (presetId) => {
    const preset = presets[presetId];
    const headers = preset.static_header_names!;
    const initial = {
      id: "mcp_test",
      version: 1,
      credential_configured: false,
    };
    const configured = { ...initial, version: 2, credential_configured: true };
    const ready = { ...configured, version: 3, status: "ready" };
    http.GET.mockResolvedValue({ data: configured, response: new Response() });
    http.POST.mockImplementation(async (path: string) => ({
      data: path.endsWith("/authorizations")
        ? { id: "authz_test", status: "completed" }
        : path.endsWith("/check")
          ? ready
          : initial,
      response: new Response(),
    }));
    const cache = new QueryClient({
      defaultOptions: { mutations: { retry: false } },
    });
    const onSuccess = vi.fn();
    render(
      <QueryClientProvider client={cache}>
        <CreateMCP
          onCancel={vi.fn()}
          preset={preset}
          onStarted={vi.fn()}
          onSuccess={onSuccess}
        />
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    const values: Record<string, string> = {};
    for (const [index, name] of headers.entries()) {
      expect(
        (screen.getByLabelText(`Header name ${index + 1}`) as HTMLInputElement)
          .value,
      ).toBe(name);
      const value =
        name === "Authorization" ? "Bearer test-token" : `value-${index}`;
      values[name.toLowerCase()] = value;
      await user.type(
        screen.getByLabelText(`Header value ${index + 1}`),
        value,
      );
    }
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(ready));
    const create = http.POST.mock.calls.find(([path]) =>
      path.includes("/workspaces/"),
    )!;
    expect(create[1].body).toEqual({
      name: preset.name,
      source: {
        kind: "mcp",
        endpoint_url: preset.endpoint_url,
        auth_mode: "static_headers",
        static_header_names: headers.map((name) => name.toLowerCase()),
      },
    });
    const credentials = http.POST.mock.calls.find(([path]) =>
      path.endsWith("/authorizations"),
    )!;
    expect(credentials[1].body).toEqual({
      expected_version: 1,
      method: "credentials",
      credentials: values,
    });
    cache.clear();
  },
);
