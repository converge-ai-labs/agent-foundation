import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { ApiError } from "../../service-client";
import { useState } from "react";
import { MemoryProviderEditor } from "./editor";
import { MemoryProviders } from "./providers";
import { AgentMemorySelection } from "./selection";
import type { Schema } from "../../shared/api";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", key: "research" },
    basePath: "/workspace/research",
    can: () => true,
  }),
  useAccess: () => ({
    can: (action: string) =>
      ["memory_provider.read", "memory_provider.manage"].includes(action),
    organizationAdmin: false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const scope = { kind: "workspace", id: "ws_test" } as const;
const provider = {
  id: "memprov_test",
  name: "Team memory",
  type: "custom_memory",
  configuration: { base_url: "https://memory.example" },
  enabled: true,
  credential_configured: true,
  workspace_id: "ws_test",
  organization_id: "org_test",
};
const definition = {
  authentication: { mode: "required" as const },
  type: "custom_memory",
  display_name: "Custom memory",
  configuration_schema: {
    type: "object",
    properties: {
      base_url: { type: "string", title: "Server URL", format: "uri" },
    },
    required: ["base_url"],
  },
  credential_schema: {
    type: "object",
    properties: {
      token: { type: "string", title: "Access token", minLength: 1 },
    },
    required: ["token"],
  },
};
const response = (data: unknown, etag = '"v1"') => ({
  data,
  response: new Response(null, { headers: { ETag: etag } }),
});
function setup(child: React.ReactNode) {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(<QueryClientProvider client={cache}>{child}</QueryClientProvider>);
  return cache;
}
beforeEach(() => {
  vi.resetAllMocks();
  http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("memory-provider-types")
        ? { items: [definition] }
        : path.endsWith("{provider_id}")
          ? provider
          : { items: [provider], next_cursor: null },
    ),
  );
  http.POST.mockResolvedValue(response(provider));
  http.PATCH.mockResolvedValue(response(provider));
});
it("creates an installed custom backend from its schemas and clears credentials on dismissal", async () => {
  const user = userEvent.setup(),
    cache = setup(<MemoryProviderEditor scope={scope} />);
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Server URL" }),
    "https://memory.example",
  );
  await user.type(screen.getByLabelText("Access token"), "private-token");
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  expect(
    http.GET.mock.calls.find(
      ([path]) => path === "/api/v1/memory-provider-types",
    )?.[1].headers,
  ).toEqual({ "X-A13N-Workspace-ID": scope.id });
  expect(http.POST.mock.calls[0][1].body).toMatchObject({
    type: "custom_memory",
    configuration: { base_url: "https://memory.example" },
    credential: { token: "private-token" },
  });
  expect(
    JSON.stringify(
      cache
        .getMutationCache()
        .getAll()
        .map((item) => item.state),
    ),
  ).not.toContain("private-token");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  expect(await screen.findByLabelText("Access token")).toHaveProperty(
    "value",
    "",
  );
});
it("keeps the target immutable and preserves credentials on rename with the exact ETag", async () => {
  const user = userEvent.setup();
  setup(<MemoryProviderEditor scope={scope} providerId={provider.id} />);
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    " renamed",
  );
  expect(screen.queryByRole("textbox", { name: "Server URL" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0][1]).toMatchObject({
    params: { header: { "If-Match": '"v1"' } },
    body: { name: "Team memory renamed" },
  });
  expect(http.PATCH.mock.calls[0][1].body).not.toHaveProperty("credential");
  expect(http.PATCH.mock.calls[0][1].body).not.toHaveProperty("configuration");
});
it("retains the draft across a stale ETag and explicitly reloads the version", async () => {
  const user = userEvent.setup();
  setup(<MemoryProviderEditor scope={scope} providerId={provider.id} />);
  http.PATCH.mockRejectedValueOnce(
    new ApiError(412, "precondition_failed", "Changed", {}, "req_test"),
  );
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    " draft",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  const reload = await screen.findByRole("button", {
    name: "Load current version and keep my draft",
  });
  http.GET.mockResolvedValue(response(provider, '"v2"'));
  await user.click(reload);
  expect(screen.getByRole("textbox", { name: "Name" })).toHaveProperty(
    "value",
    "Team memory draft",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(2));
  expect(http.PATCH.mock.calls[1][1].params.header).toEqual({
    "If-Match": '"v2"',
  });
});
it("renders inherited providers read-only without editable secrets", async () => {
  const user = userEvent.setup();
  setup(
    <MemoryProviderEditor scope={scope} providerId={provider.id} readOnly />,
  );
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await screen.findByText("Team memory");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByLabelText("Access token")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
});
it("never repeats an uncertain provider create before explicit reconciliation", async () => {
  const user = userEvent.setup();
  setup(<MemoryProviderEditor scope={scope} />);
  http.POST.mockRejectedValue(new TypeError("Network unavailable"));
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Server URL" }),
    "https://memory.example",
  );
  await user.type(screen.getByLabelText("Access token"), "private-token");
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await screen.findByText(/Creation could not be confirmed/);
  expect(screen.getByRole("button", { name: "Add provider" })).toHaveProperty(
    "disabled",
    true,
  );
  await user.click(
    screen.getByRole("button", { name: "Review existing providers" }),
  );
  await screen.findByRole("button", { name: "Return to providers" });
  expect(http.POST).toHaveBeenCalledOnce();
});
it("preserves all memory options when switching providers and refreshes choices without changing drafts", async () => {
  const change = vi.fn(),
    user = userEvent.setup();
  function Draft() {
    const [value, setValue] = useState<Schema["MemorySelection"] | null>({
      provider_id: provider.id,
      scope: "agent",
      auto_recall: false,
      recall_limit: 7,
      recall_threshold: 0,
      toolset: false,
    });
    return (
      <AgentMemorySelection
        value={value}
        onChange={(next) => {
          change(next);
          setValue(next);
        }}
      />
    );
  }
  setup(<Draft />);
  await waitFor(() =>
    expect(
      http.GET.mock.calls.filter(
        ([path]) => !path.endsWith("memory-provider-types"),
      ),
    ).toHaveLength(1),
  );
  const link = screen.getByRole("link", { name: "Manage memory providers" });
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("href")).toBe(
    "/workspace/research/settings?section=providers&category=memory",
  );
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await waitFor(() =>
    expect(
      http.GET.mock.calls.filter(
        ([path]) => !path.endsWith("memory-provider-types"),
      ),
    ).toHaveLength(2),
  );
  expect(change).not.toHaveBeenCalled();
  focusManager.setFocused(undefined);
  await user.click(screen.getByRole("button", { name: "Recall settings" }));
  expect(
    screen.getByRole("spinbutton", { name: "Similarity threshold" }),
  ).toHaveProperty("value", "0");
  expect(
    screen.getByRole("spinbutton", { name: "Recall limit" }),
  ).toHaveProperty("value", "7");
});

it("uses Memory management permission and leaves inherited Organization providers read-only", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("memory-provider-types")
        ? { items: [definition] }
        : path.endsWith("{provider_id}")
          ? { ...provider, workspace_id: null }
          : { items: [{ ...provider, workspace_id: null }], next_cursor: null },
    ),
  );
  setup(<MemoryProviders scope={scope} />);
  expect(
    await screen.findByRole("button", { name: "Add provider" }),
  ).toBeTruthy();
  await user.click(await screen.findByText("Team memory"));
  await screen.findByRole("dialog");
  await screen.findByText("https://memory.example");
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull();
});

it("links to the saved Agent provider even when the selection draft changes", async () => {
  setup(
    <AgentMemorySelection
      value={{ provider_id: "memprov_new" }}
      agentId="agt_stable"
      savedProviderId="memprov_old"
      onChange={vi.fn()}
    />,
  );
  const link = screen.getByRole("link", { name: "View agent memories" });
  expect(link.getAttribute("href")).toBe(
    "/workspace/research/memories?scope=agent&provider=memprov_old&subject=agt_stable",
  );
  expect(link.getAttribute("target")).toBe("_blank");
});

const structuredDefinition = {
  ...definition,
  setup_url: "https://docs.example.com/memory-setup",
  setup_label: "Configure custom memory",
  authentication: {
    mode: "required",
    cases: [{ field: "access", equals: "public", mode: "forbidden" }],
  },
  configuration_schema: {
    type: "object",
    properties: {
      access: {
        type: "string",
        title: "Access",
        enum: ["public", "private"],
        default: "public",
      },
    },
  },
  credential_schema: {
    type: "object",
    required: ["authorization", "revision"],
    properties: {
      authorization: {
        type: "object",
        required: ["token"],
        properties: { token: { type: "string", title: "Token", minLength: 1 } },
      },
      revision: { type: "integer", title: "Revision", minimum: 1, default: 1 },
    },
  },
};
it("renders custom help, choice defaults, nested secrets and numeric credentials", async () => {
  const user = userEvent.setup();
  http.GET.mockResolvedValue(response({ items: [structuredDefinition] }));
  setup(<MemoryProviderEditor scope={scope} />);
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  expect(
    (
      await screen.findByRole("link", { name: "Configure custom memory" })
    ).getAttribute("href"),
  ).toBe(structuredDefinition.setup_url);
  expect(screen.queryByLabelText("Token")).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "Access" }));
  await user.click(await screen.findByRole("option", { name: "private" }));
  expect(screen.getByLabelText("Token").getAttribute("type")).toBe("password");
  await user.type(screen.getByLabelText("Token"), "nested-secret");
  await user.clear(screen.getByLabelText("Revision"));
  await user.type(screen.getByLabelText("Revision"), "7");
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  expect(http.POST.mock.calls[0][1].body).toMatchObject({
    configuration: { access: "private" },
    credential: { authorization: { token: "nested-secret" }, revision: 7 },
  });
});
it("allows explicit removal even when the definition requires credentials", async () => {
  const user = userEvent.setup();
  setup(<MemoryProviderEditor scope={scope} providerId={provider.id} />);
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.click(await screen.findByRole("button", { name: "Remove" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0][1].body.credential).toBeNull();
});

it("keeps a forbidden-credential custom provider selectable without a credential", async () => {
  http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("memory-provider-types")
        ? { items: [structuredDefinition] }
        : {
            items: [
              {
                ...provider,
                configuration: { access: "public" },
                credential_configured: false,
              },
            ],
            next_cursor: null,
          },
    ),
  );
  setup(
    <AgentMemorySelection
      value={{ provider_id: provider.id }}
      onChange={vi.fn()}
    />,
  );
  await waitFor(() => expect(http.GET).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(
    screen.getByRole("combobox", { name: "Memory provider" }).textContent,
  ).not.toContain("Unavailable");
});

it("retains saved credentials when renaming an account whose definition is unavailable", async () => {
  const user = userEvent.setup();
  http.GET.mockImplementation(async (path: string) =>
    response(path.endsWith("memory-provider-types") ? { items: [] } : provider),
  );
  setup(<MemoryProviderEditor scope={scope} providerId={provider.id} />);
  await user.click(screen.getByRole("button", { name: "Edit" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Name" }),
    " renamed",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0][1].body).not.toHaveProperty("credential");
});
