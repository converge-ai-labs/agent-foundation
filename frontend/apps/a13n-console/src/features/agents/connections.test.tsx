import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ConnectionGroup } from "./connections";

vi.mock("../../auth/context", () => ({ useClient: () => ({}) }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
        String(values?.[name] ?? name),
      ),
  }),
}));
afterEach(cleanup);

it("removes an unavailable attachment while collapsed without changing tool permissions", () => {
  const onRemove = vi.fn();
  const onChange = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (readOnly: boolean) => (
    <QueryClientProvider client={cache}>
      <ConnectionGroup
        selection={{
          connection_id: "conn_missing",
          tools: ["lookup"],
          permissions: { lookup: "deny" },
        }}
        readOnly={readOnly}
        onChange={onChange}
        onRemove={onRemove}
      />
    </QueryClientProvider>
  );
  const { rerender } = render(view(false));
  expect(screen.queryByRole("checkbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Remove conn_missing" }));
  expect(onRemove).toHaveBeenCalledOnce();
  expect(onChange).not.toHaveBeenCalled();
  expect(screen.queryByText("Default tool permission")).toBeNull();
  rerender(view(true));
  expect(
    screen.queryByRole("button", { name: "Remove conn_missing" }),
  ).toBeNull();
});

it.each([false, true])(
  "names an unavailable connection with authorization_pending=%s in the agent editor",
  (authorization_pending) => {
    const connection: Schema["Connection"] = {
      id: "conn_pending",
      organization_id: "org_test",
      workspace_id: "ws_test",
      name: "Example",
      type: "mcp",
      auth: "oauth",
      config: { url: "https://mcp.example" },
      status: "pending",
      enabled: true,
      version: 1,
      credential_configured: false,
      authorization_pending,
      client_secret_configured: false,
      connector_provider_id: null,
      failure: null,
      last_test: null,
      created_at: "2026-11-20T00:00:00Z",
      updated_at: "2026-11-20T00:00:00Z",
      created_by_id: "usr_test",
      updated_by_id: "usr_test",
    };
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
    cache.setQueryData(
      ["connection-tool-catalog", connection.id, connection.version],
      [],
    );
    cache.setQueryData(["mcp-servers", "catalog"], []);
    render(
      <QueryClientProvider client={cache}>
        <ConnectionGroup
          connection={connection}
          selection={{ connection_id: connection.id, tools: null }}
          readOnly={false}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      </QueryClientProvider>,
    );
    expect(
      screen.getByText(
        authorization_pending ? "Authorizing" : "Not authorized",
      ),
    ).toBeTruthy();
    cache.clear();
  },
);

it("warns on >128 direct connection tools while collapsed and clears once tool discovery covers it", () => {
  const connection: Schema["Connection"] = {
    id: "conn_photon",
    organization_id: "org_test",
    workspace_id: "ws_test",
    name: "Photon",
    type: "mcp",
    auth: "oauth",
    config: { url: "https://mcp.photon.codes/mcp" },
    status: "ready",
    enabled: true,
    version: 1,
    credential_configured: true,
    authorization_pending: false,
    client_secret_configured: false,
    connector_provider_id: null,
    failure: null,
    last_test: null,
    created_at: "2026-11-20T00:00:00Z",
    updated_at: "2026-11-20T00:00:00Z",
    created_by_id: "usr_test",
    updated_by_id: "usr_test",
  };
  const catalog = Array.from({ length: 147 }, (_, index) => ({
    name: `tool_${index}`,
    description: `Tool ${index}`,
  }));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  cache.setQueryData(
    ["connection-tool-catalog", connection.id, connection.version],
    catalog,
  );
  cache.setQueryData(["mcp-servers", "catalog"], []);

  const onEnableDiscovery = vi.fn();
  const renderGroup = (props: {
    discovery?: "proxy" | "native";
    readOnly?: boolean;
    tools?: string[] | null;
  }) => (
    <QueryClientProvider client={cache}>
      <ConnectionGroup
        connection={connection}
        selection={{
          connection_id: connection.id,
          tools: props.tools === undefined ? null : props.tools,
        }}
        discovery={props.discovery}
        onEnableDiscovery={onEnableDiscovery}
        readOnly={props.readOnly ?? false}
        onChange={vi.fn()}
        onRemove={vi.fn()}
      />
    </QueryClientProvider>
  );

  const { rerender } = render(renderGroup({}));
  expect(screen.getByText("More than 128 tools selected")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Enable tool discovery" }),
  );
  expect(onEnableDiscovery).toHaveBeenCalledOnce();

  // Read-only view keeps the notice visible without an editable action button.
  rerender(renderGroup({ readOnly: true }));
  expect(screen.getByText("More than 128 tools selected")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Enable tool discovery" }),
  ).toBeNull();

  // Narrowing to <= 128 tools clears the direct limit notice.
  rerender(
    renderGroup({
      tools: catalog.slice(0, 128).map((tool) => tool.name),
    }),
  );
  expect(screen.queryByText("More than 128 tools selected")).toBeNull();

  // Covering the connection with native tool discovery clears the warning and shows native copy when expanded.
  rerender(renderGroup({ discovery: "native" }));
  expect(screen.queryByText("More than 128 tools selected")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^Photon/ }));
  expect(
    screen.getByText("This connection uses native tool discovery."),
  ).toBeTruthy();
  cache.clear();
});
