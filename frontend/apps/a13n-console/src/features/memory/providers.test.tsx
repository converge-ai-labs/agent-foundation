import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { ApiError } from "../../service-client";
import { useState } from "react";
import { AddMemoryProvider, MemoryProviderEditor } from "./editor";
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
    workspace: { id: "ws_test", key: "research" },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const scope = { kind: "workspace", id: "ws_test" } as const;
const provider = {
  id: "memprov_test",
  name: "Team memory",
  type: "custom.memory",
  configuration: { base_url: "https://memory.example" },
  enabled: true,
  credential_configured: true,
  workspace_id: "ws_test",
  organization_id: "org_test",
};
const definition = {
  type: "custom.memory",
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
  render(
    <MemoryRouter>
      <QueryClientProvider client={cache}>{child}</QueryClientProvider>
    </MemoryRouter>,
  );
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
    cache = setup(<AddMemoryProvider scope={scope} />);
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.click(
    await screen.findByRole("button", { name: /Custom memory/ }),
  );
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
    type: "custom.memory",
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
  await user.click(
    await screen.findByRole("button", { name: /Custom memory/ }),
  );
  expect(await screen.findByLabelText("Access token")).toHaveProperty(
    "value",
    "",
  );
});
it("keeps the target immutable and preserves credentials on rename with the exact ETag", async () => {
  const user = userEvent.setup();
  setup(
    <MemoryProviderEditor
      scope={scope}
      providerId={provider.id}
      controlledOpen
      onClose={() => {}}
    />,
  );
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
  setup(
    <MemoryProviderEditor
      scope={scope}
      providerId={provider.id}
      controlledOpen
      onClose={() => {}}
    />,
  );
  http.PATCH.mockRejectedValueOnce(
    new ApiError(412, "precondition_failed", "Changed", {}, "req_test"),
  );
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
  setup(
    <MemoryProviderEditor
      scope={scope}
      providerId={provider.id}
      readOnly
      controlledOpen
      onClose={() => {}}
    />,
  );
  await screen.findAllByText("Team memory");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByLabelText("Access token")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
});
it("never repeats an uncertain provider create before explicit reconciliation", async () => {
  const user = userEvent.setup();
  setup(<AddMemoryProvider scope={scope} />);
  http.POST.mockRejectedValue(new TypeError("Network unavailable"));
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await user.click(
    await screen.findByRole("button", { name: /Custom memory/ }),
  );
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
    const [value, setValue] = useState<Schema["MemoryConfiguration"] | null>({
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
  await waitFor(() => expect(http.GET).toHaveBeenCalledOnce());
  const link = screen.getByRole("link", { name: "Manage providers" });
  expect(link.getAttribute("target")).toBeNull();
  expect(link.getAttribute("href")).toBe(
    "/workspace/research/settings?section=providers&category=memory",
  );
  focusManager.setFocused(false);
  focusManager.setFocused(true);
  await waitFor(() => expect(http.GET).toHaveBeenCalledTimes(2));
  expect(change).not.toHaveBeenCalled();
  focusManager.setFocused(undefined);
  await user.click(screen.getByRole("button", { name: "preferences" }));
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
  expect(link.getAttribute("target")).toBeNull();
});

it("adds independent file memory while preserving a legacy Mem0 selection", async () => {
  const change = vi.fn();
  const user = userEvent.setup();
  function Draft() {
    const [value, setValue] = useState<Schema["MemoryConfiguration"] | null>({
      provider_id: provider.id,
      scope: "user",
      recall_limit: 7,
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
  await user.click(screen.getByRole("switch", { name: "Project memory" }));
  const selection = change.mock.lastCall?.[0];
  expect(selection.entries).toHaveLength(2);
  expect(selection.entries[0]).toMatchObject({
    name: "preferences",
    mode: "records",
    scope: "user",
    recall_limit: 7,
    backend: { provider_id: provider.id },
  });
  expect(selection.entries[1]).toMatchObject({
    name: "project",
    mode: "documents",
    backend: { type: "a13n.filesystem" },
  });
  expect(screen.queryByLabelText("Memory directory")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Project memory settings" }),
  );
  expect(
    (screen.getByLabelText("Memory directory") as HTMLInputElement).value,
  ).toBe("/memory");
  await user.click(screen.getByRole("button", { name: "Add custom memory" }));
  expect(
    change.mock.lastCall?.[0].entries.map(
      (item: Schema["MemoryEntrySelection"]) => item.name,
    ),
  ).toEqual(["preferences", "project", "custom"]);
});

it("enables personal and project memory independently and restores a disabled draft", async () => {
  http.GET.mockResolvedValue(
    response({
      items: [{ ...provider, type: "a13n.mem0-platform" }],
      next_cursor: null,
    }),
  );
  const user = userEvent.setup();
  const change = vi.fn();
  function Draft() {
    const [value, setValue] = useState<Schema["MemoryConfiguration"] | null>(
      null,
    );
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
  const personal = screen.getByRole("switch", { name: "Personal preferences" });
  await waitFor(() =>
    expect(personal.hasAttribute("data-disabled")).toBe(false),
  );
  expect(change).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Environment ID")).toBeNull();
  await user.click(personal);
  expect(change.mock.lastCall?.[0].entries).toEqual([
    expect.objectContaining({
      mode: "records",
      scope: "user",
      auto_recall: true,
      backend: { provider_id: provider.id },
    }),
  ]);
  await user.click(screen.getByRole("switch", { name: "Automatic recall" }));
  await user.click(screen.getByRole("switch", { name: "Project memory" }));
  const both = change.mock.lastCall?.[0];
  expect(both.entries).toHaveLength(2);
  expect(both.entries[0].auto_recall).toBe(false);
  expect(both.entries[1]).toMatchObject({
    mode: "documents",
    scope: "thread",
    backend: {
      type: "a13n.filesystem",
      configuration: { storage: { root: "/memory" } },
    },
  });
  await user.click(personal);
  expect(change.mock.lastCall?.[0].entries).toEqual([both.entries[1]]);
  await user.click(personal);
  expect(change.mock.lastCall?.[0].entries).toEqual([
    both.entries[1],
    both.entries[0],
  ]);
});

it("keeps file memory usable without Mem0 and directs personal setup to providers", async () => {
  http.GET.mockResolvedValue(response({ items: [], next_cursor: null }));
  const change = vi.fn();
  const user = userEvent.setup();
  setup(<AgentMemorySelection value={null} onChange={change} />);
  await screen.findByText(
    "Connect Mem0 in memory providers to enable personal preferences.",
  );
  expect(
    screen
      .getByRole("switch", { name: "Personal preferences" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("link", { name: "Manage providers" })
      .getAttribute("target"),
  ).toBeNull();
  await user.click(screen.getByRole("switch", { name: "Project memory" }));
  expect(change.mock.lastCall?.[0].entries).toHaveLength(1);
  expect(change.mock.lastCall?.[0].entries[0].mode).toBe("documents");
});

it("preserves custom purposes even when their names match the presets", async () => {
  http.GET.mockResolvedValue(
    response({
      items: [{ ...provider, type: "a13n.mem0-oss" }],
      next_cursor: null,
    }),
  );
  const entries: Schema["MemoryEntrySelection"][] = [
    {
      name: "preferences",
      description: "Team decisions only",
      mode: "records",
      scope: "agent",
      backend: { provider_id: provider.id },
      recall_limit: 13,
    },
    {
      name: "project",
      description: "Personal travel diary",
      mode: "documents",
      scope: "user",
      backend: {
        type: "a13n.filesystem",
        configuration: {
          storage: { root: "/diary", environment_id: "env_saved" },
        },
      },
    },
  ];
  const change = vi.fn();
  const user = userEvent.setup();
  setup(<AgentMemorySelection value={{ entries }} onChange={change} />);
  const personal = screen.getByRole("switch", { name: "Personal preferences" });
  await waitFor(() =>
    expect(personal.hasAttribute("data-disabled")).toBe(false),
  );
  expect(personal.getAttribute("aria-checked")).toBe("false");
  expect(
    screen
      .getByRole("switch", { name: "Project memory" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(screen.getByText(/Personal travel diary/)).toBeTruthy();
  expect(change).not.toHaveBeenCalled();
  await user.click(personal);
  expect(change.mock.lastCall?.[0].entries.slice(0, 2)).toEqual(entries);
  expect(change.mock.lastCall?.[0].entries[2].name).toBe("preferences_2");
});

it("keeps each preset's detailed settings local and edits only that entry", async () => {
  const change = vi.fn();
  const user = userEvent.setup();
  const personal: Schema["MemoryEntrySelection"] = {
    name: "preferences",
    description: "User preferences and personal facts.",
    mode: "records",
    scope: "user",
    backend: { provider_id: provider.id },
    recall_limit: 7,
    recall_threshold: 0,
  };
  const project: Schema["MemoryEntrySelection"] = {
    name: "project",
    description: "Project requirements, decisions, and procedures.",
    mode: "documents",
    scope: "thread",
    backend: {
      type: "a13n.filesystem",
      configuration: {
        storage: { root: "/memory", environment_id: "env_saved" },
      },
    },
  };
  function Draft() {
    const [value, setValue] = useState<Schema["MemoryConfiguration"] | null>({
      entries: [personal, project],
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
  expect(
    screen.queryByRole("button", { name: /Advanced memory settings/ }),
  ).toBeNull();
  expect(
    screen.queryByRole("combobox", { name: "Memory provider" }),
  ).toBeNull();
  const personalRegion = within(
    screen.getByRole("region", { name: "Personal preferences" }),
  );
  const projectRegion = within(
    screen.getByRole("region", { name: "Project memory" }),
  );
  await user.click(
    personalRegion.getByRole("button", {
      name: "Personal preference settings",
    }),
  );
  expect(
    personalRegion.getByRole("spinbutton", { name: "Recall limit" }),
  ).toHaveProperty("value", "7");
  expect(
    personalRegion.getByRole("spinbutton", { name: "Similarity threshold" }),
  ).toHaveProperty("value", "0");
  expect(personalRegion.queryByLabelText("Memory directory")).toBeNull();
  await user.click(
    projectRegion.getByRole("button", { name: "Project memory settings" }),
  );
  const directory = projectRegion.getByLabelText("Memory directory");
  await user.clear(directory);
  await user.type(directory, "/project-notes");
  expect(change.mock.lastCall?.[0].entries[0]).toEqual(personal);
  expect(
    change.mock.lastCall?.[0].entries[1].backend.configuration.storage,
  ).toEqual({ root: "/project-notes", environment_id: "env_saved" });
  expect(
    projectRegion.queryByRole("spinbutton", { name: "Recall limit" }),
  ).toBeNull();
});

it("adds custom memory directly from the off state without enabling either preset", async () => {
  const change = vi.fn();
  const user = userEvent.setup();
  function Draft() {
    const [value, setValue] = useState<Schema["MemoryConfiguration"] | null>(
      null,
    );
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
  expect(
    screen.queryByRole("combobox", { name: "Memory provider" }),
  ).toBeNull();
  await user.click(screen.getByRole("button", { name: "Add custom memory" }));
  expect(
    screen
      .getByRole("switch", { name: "Project memory" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  await user.type(
    screen.getByRole("textbox", { name: "Purpose" }),
    "Research notes",
  );
  expect(change.mock.lastCall?.[0].entries).toHaveLength(1);
  expect(change.mock.lastCall?.[0].entries[0].description).toBe(
    "Research notes",
  );
  await user.click(screen.getByRole("button", { name: "Remove entry" }));
  expect(change.mock.lastCall?.[0]).toBeNull();
});
