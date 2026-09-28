import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { CreateMCP } from "./create";

const http = vi.hoisted(() => ({ POST: vi.fn(), GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
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

const presets = {
  airtable: {
    key: "airtable",
    name: "Airtable",
    description: "Bases and records",
    url: "https://mcp.airtable.com/mcp",
    auth: "oauth",
    documentation_url: "https://example.com/airtable/setup",
    requirements: "Sign in with an account that can access the requested data.",
  },
  jentic: {
    key: "jentic",
    name: "Jentic",
    description: "Secure API access",
    url: "https://api.jentic.com/mcp",
    auth: "headers",
    header_names: ["x-jentic-api-key", "x-jentic-agent"],
  },
} satisfies Record<string, Schema["McpServer"]>;

it("shows preset authentication as a read-only value with a setup link", () => {
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
  expect(screen.queryByRole("textbox", { name: "Authentication" })).toBeNull();
  expect(screen.getByText(`auth.${preset.auth}`)).toBeTruthy();
  expect(
    (
      screen.getByRole("textbox", {
        name: "Connection name",
      }) as HTMLInputElement
    ).value,
  ).toBe(preset.name);
  expect(screen.getByText(preset.requirements)).toBeTruthy();
  const guide = screen.getByRole("link", { name: "Setup guide" });
  expect(guide.getAttribute("href")).toBe(preset.documentation_url);
  expect(guide.getAttribute("target")).toBe("_blank");
  cache.clear();
});

it("prefills preset header names and sends their values only as the credential", async () => {
  const preset = presets.jentic;
  const created = { id: "conn_test", workspace_id: "ws_test", version: 1 };
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("/test")
      ? { status: "succeeded", message: null, tools: [] }
      : created,
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
  for (const [index, name] of preset.header_names.entries()) {
    expect(
      (screen.getByLabelText(`Header name ${index + 1}`) as HTMLInputElement)
        .value,
    ).toBe(name);
    await user.type(
      screen.getByLabelText(`Header value ${index + 1}`),
      `value-${index}`,
    );
  }
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(created));
  expect(http.POST.mock.calls[0][1].body).toEqual({
    type: "mcp",
    name: preset.name,
    config: { url: preset.url, headers: preset.header_names },
    auth: "headers",
    credential: {
      headers: { "x-jentic-api-key": "value-0", "x-jentic-agent": "value-1" },
    },
  });
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

it("names headers in the configuration and sends their values only as the write-only credential", async () => {
  const created = {
    id: "conn_test",
    workspace_id: "ws_test",
    version: 1,
    credential_configured: true,
    status: "ready",
  };
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("/test")
      ? { status: "succeeded", message: null, tools: [] }
      : created,
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
        endpoint="https://api.jentic.com/mcp"
        onStarted={vi.fn()}
        onSuccess={onSuccess}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Connection name" }),
    "Jentic",
  );
  await user.click(screen.getByRole("combobox", { name: "Authentication" }));
  await user.click(await screen.findByRole("option", { name: "auth.headers" }));
  await user.type(screen.getByLabelText("Header name 1"), "X-Jentic-API-Key");
  await user.type(screen.getByLabelText("Header value 1"), "value-1");
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await waitFor(() => expect(onSuccess).toHaveBeenCalledWith(created));
  expect(http.POST.mock.calls[0]).toEqual([
    "/api/v1/connections",
    {
      body: {
        type: "mcp",
        name: "Jentic",
        config: {
          url: "https://api.jentic.com/mcp",
          headers: ["x-jentic-api-key"],
        },
        auth: "headers",
        credential: { headers: { "x-jentic-api-key": "value-1" } },
      },
    },
  ]);
  expect(http.POST.mock.calls[1]).toEqual([
    "/api/v1/connections/{connection_id}/test",
    {
      params: { path: { connection_id: "conn_test" } },
    },
  ]);
  cache.clear();
});

it("keeps the created connection and reports why its tools could not be listed", async () => {
  http.POST.mockImplementation(async (path: string) => ({
    data: path.endsWith("/test")
      ? {
          status: "failed",
          message: "The server did not answer in time",
          tools: [],
        }
      : { id: "conn_test", workspace_id: "ws_test", version: 1 },
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
        endpoint="https://mcp.example/mcp"
        onStarted={vi.fn()}
        onSuccess={onSuccess}
      />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "Connection name" }),
    "Example",
  );
  await user.click(screen.getByRole("combobox", { name: "Authentication" }));
  await user.click(await screen.findByRole("option", { name: "auth.none" }));
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByText("The server did not answer in time");
  await user.click(screen.getByRole("button", { name: "Continue connection" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledTimes(3));
  expect(
    http.POST.mock.calls.filter(([path]) => path.endsWith("/connections")),
  ).toHaveLength(1);
  expect(onSuccess).not.toHaveBeenCalled();
  cache.clear();
});
