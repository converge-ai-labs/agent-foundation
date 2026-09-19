import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentForm } from "./form";

vi.mock("./toolsets", () => ({ AgentToolsets: () => null }));
const catalog = vi.hoisted(() => ({
  POST: vi.fn(async () => ({
    data: {
      items: [
        { name: "search", description: "Search" },
        { name: "read", description: "Read" },
      ],
    },
  })),
  GET: vi.fn(async () => ({
    data: {
      items: [
        { key: "profile.read", description: "Read profile" },
        { key: "search", description: "Search" },
        { key: "read", description: "Read" },
        { key: "write", description: "Write" },
      ],
    },
  })),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: catalog }) }));
vi.mock("../memory/presets", () => ({ MemoryPresets: () => null }));
const memoryAvailability = vi.hoisted(() => ({ visible: true }));
const definitionAvailability = vi.hoisted(() => ({ available: true }));
vi.mock("../memory/availability", () => ({
  useMemoryProviders: () => memoryAvailability,
}));
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: {
      models: [
        {
          key: "research",
          name: "Research model",
          upstream_model: "upstream",
          model_api: "openai.responses",
        },
        {
          key: "claude",
          name: "Claude Opus 5",
          upstream_model: "gateway-alias",
          catalog_ref: { provider: "anthropic", model: "claude-opus-5" },
          model_api: "anthropic.messages",
        },
      ],
      skills: [{ key: "sources", name: "Source verification" }],
      connections: [
        {
          id: "mcp_0123456789abcdef",
          name: "Web tools · ready",
          status: "ready",
          version: 1,
          source: { kind: "mcp" },
        },
        {
          id: "conn_0123456789abcdef",
          name: "Repository",
          status: "ready",
          version: 1,
          source: {
            kind: "connector",
            provider_id: "cp_1",
            connector_key: "repo",
          },
        },
        {
          id: "mcp_disabled",
          name: "Disabled MCP · disabled",
          status: "disabled",
          version: 1,
          source: { kind: "mcp" },
        },
      ],
    },
  }),
}));
vi.mock("../models/provider-definitions", () => ({
  useModelProviderDefinitions: () => ({
    data: definitionAvailability.available
      ? {
          items: [
            {
              settings_schemas: Object.fromEntries(
                ["openai.responses", "anthropic.messages"].map((api) => [
                  api,
                  {
                    properties: {
                      thinking: {
                        anyOf: [
                          { type: "boolean" },
                          {
                            enum: ["minimal", "low", "medium", "high", "xhigh"],
                          },
                        ],
                      },
                    },
                  },
                ]),
              ),
            },
          ],
        }
      : undefined,
  }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
afterEach(() => {
  memoryAvailability.visible = true;
  definitionAvailability.available = true;
  cleanup();
  vi.unstubAllGlobals();
});

function editor(
  readonly = false,
  connectorTools: NonNullable<
    ReturnType<typeof initialConfig>["connection_tools"]
  > = [],
  memory?: ReturnType<typeof initialConfig>["memory"],
  settings?: Record<string, string | number | boolean>,
  reviewer?: ReturnType<typeof initialConfig>["reviewer"],
) {
  const submit = vi.fn();
  const initial = {
    ...initialConfig("Research"),
    memory,
    reviewer,
    model: { model_key: "research", ...(settings ? { settings } : {}) },
    instructions: "Check the evidence.",
    connection_tools: connectorTools,
    skills: [{ skill_key: "sources", version: 3 }],
    secret_requirements: [{ key: "research-token", required: true }],
  };
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentForm
          back="/agents"
          name="Research"
          primaryAction={<button type="button">Try agent</button>}
          identityAction={<button type="button">Edit agent details</button>}
          context={<button type="button">More agent actions</button>}
          initial={initial}
          version={7}
          etag='"agent-v1"'
          pending={false}
          error={undefined}
          readonly={readonly}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { submit, initial, view, cache };
}

it("keeps the editor's configuration and ETag through a background refetch", async () => {
  const { submit, initial, view, cache } = editor();
  const user = userEvent.setup();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " Local draft.",
  );
  view.rerender(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentForm
          back="/agents"
          name="Research"
          initial={{ ...initial, instructions: "Remote replacement" }}
          version={8}
          etag='"agent-v2"'
          pending={false}
          error={undefined}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(screen.getByText("v7")).toBeTruthy();
  expect(
    (
      screen.getByRole("textbox", {
        name: "System instructions",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("Check the evidence. Local draft.");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0]?.[3]).toBe('"agent-v1"');
});

it("shows the catalog model logo in the model picker and selected value", async () => {
  const user = userEvent.setup();
  editor();
  await user.click(screen.getByRole("combobox", { name: "Model" }));
  const option = await screen.findByRole("option", { name: /Claude Opus 5/ });
  expect(option.querySelector("img")?.getAttribute("src")).toContain(
    "claude-color.svg",
  );
  await user.click(option);
  expect(
    screen
      .getByRole("combobox", { name: "Model" })
      .querySelector("img")
      ?.getAttribute("src"),
  ).toContain("claude-color.svg");
});

it("defaults thinking on and saves common controls with supplemental JSON", async () => {
  const user = userEvent.setup();
  const { submit } = editor(false, [], undefined, { temperature: 0.4 });
  const thinking = screen.getByRole("combobox", { name: "Thinking effort" });
  expect(thinking.textContent).toContain("On (default effort)");
  await user.click(thinking);
  expect(screen.queryByRole("option", { name: "Model default" })).toBeNull();
  await user.click(await screen.findByRole("option", { name: "High" }));
  await user.type(
    screen.getByRole("spinbutton", { name: "Max output tokens" }),
    "4096",
  );
  await user.click(
    screen.getByRole("button", { name: /Additional model settings/ }),
  );
  const additional = screen.getByRole("textbox", {
    name: "Additional model settings",
  });
  expect((additional as HTMLTextAreaElement).value).toContain(
    '"temperature": 0.4',
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].model.settings).toEqual({
    temperature: 0.4,
    thinking: "high",
    max_tokens: 4096,
  });
});

it("lifts existing thinking and max tokens out of the JSON editor", async () => {
  const user = userEvent.setup();
  editor(false, [], undefined, {
    thinking: false,
    max_tokens: 2000,
    temperature: 0.2,
  });
  expect(
    screen.getByRole("combobox", { name: "Thinking effort" }).textContent,
  ).toContain("Off");
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Max output tokens",
      }) as HTMLInputElement
    ).value,
  ).toBe("2000");
  await user.click(
    screen.getByRole("button", { name: /Additional model settings/ }),
  );
  const json = (
    screen.getByRole("textbox", {
      name: "Additional model settings",
    }) as HTMLTextAreaElement
  ).value;
  expect(json).toContain('"temperature": 0.2');
  expect(json).not.toContain("thinking");
  expect(json).not.toContain("max_tokens");
});

it("keeps agent model choices available when provider definitions cannot load", async () => {
  definitionAvailability.available = false;
  const user = userEvent.setup();
  editor();
  await user.click(screen.getByRole("combobox", { name: "Model" }));
  expect(
    await screen.findByRole("option", { name: /Claude Opus 5/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("combobox", { name: "Thinking effort" }).textContent,
  ).toContain("On (default effort)");
});

it("preserves hidden reviewer configuration verbatim when saving other changes", async () => {
  const user = userEvent.setup();
  const reviewer = {
    model: "mdl_0123456789abcdef",
    risk_threshold: "high" as const,
    model_settings: { temperature: 0 },
  };
  const { submit } = editor(false, [], undefined, undefined, reviewer);
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More context.",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({ reviewer }),
    "Research",
    "",
    '"agent-v1"',
    null,
  );
});

it("edits capabilities inline and preserves pinned versions, hidden configuration and version evidence", async () => {
  const user = userEvent.setup(),
    { submit, initial } = editor();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "Web tools" })).toBeNull();
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Pinned version",
      }) as HTMLInputElement
    ).value,
  ).toBe("3");
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  await user.click(screen.getByRole("button", { name: /Web tools/ }));
  await user.click(screen.getByRole("button", { name: "Web tools" }));
  await screen.findByRole("checkbox", { name: "search" });
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  await user.click(screen.getByRole("button", { name: "Repository" }));
  await user.click(screen.getByRole("button", { name: "Repository" }));
  await screen.findByRole("checkbox", { name: "profile.read" });
  await user.click(screen.getByRole("checkbox", { name: "write" }));
  await user.click(screen.getAllByRole("checkbox", { name: "read" })[1]);
  expect(
    screen
      .getAllByRole("checkbox", { name: "Load tools on demand" })[1]
      .getAttribute("aria-checked"),
  ).toBe("true");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(screen.queryByRole("alert")?.textContent).toBeUndefined();
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: initial.instructions,
      skills: initial.skills,
      secret_requirements: initial.secret_requirements,
      connection_tools: [
        {
          connection_id: "mcp_0123456789abcdef",
          tools: null,
          defer_loading: true,
        },
        {
          connection_id: "conn_0123456789abcdef",
          tools: ["profile.read", "search"],
          defer_loading: true,
        },
      ],
    }),
    "Research",
    "",
    '"agent-v1"',
    null,
  );
});

it("shows configuration as readable values for readers", () => {
  const { submit } = editor(true);
  expect(screen.queryByRole("button", { name: "Add Connections" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Remove Source verification" }),
  ).toBeNull();
  expect(
    screen.queryByRole("textbox", { name: "System instructions" }),
  ).toBeNull();
  expect(
    screen.getByRole("group", { name: "System instructions" }).textContent,
  ).toContain("Check the evidence.");
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

it("requires saving the instruction draft before trial or agent management", async () => {
  const user = userEvent.setup();
  const { submit } = editor();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  expect(
    screen.getByRole("button", { name: "Try agent" }).matches(":disabled"),
  ).toBe(true);
  for (const name of ["Edit agent details", "More agent actions"]) {
    expect(screen.getByRole("button", { name }).matches(":disabled")).toBe(
      true,
    );
  }
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. More detail.",
    }),
    "Research",
    "",
    '"agent-v1"',
    null,
  );
});

it("distinguishes an empty Connector allowlist from all tools", async () => {
  const user = userEvent.setup(),
    { submit } = editor();
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  await user.click(screen.getByRole("button", { name: "Repository" }));
  await user.click(
    screen.getByRole("checkbox", { name: "Enable Repository tools" }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].connection_tools[0].tools).toEqual([]);
  await user.click(
    screen.getByRole("checkbox", { name: "Enable Repository tools" }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[1][0].connection_tools[0].tools).toBeNull();
});

it("removes permission overrides for tools removed from an explicit selection", async () => {
  const user = userEvent.setup();
  const { submit } = editor(false, [
    {
      connection_id: "conn_0123456789abcdef",
      tools: ["read", "write"],
      permissions: { write: "ask" },
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Repository" }));
  await screen.findByRole("checkbox", { name: "write" });
  await user.click(screen.getByRole("checkbox", { name: "write" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].connection_tools[0]).toMatchObject({
    tools: ["read"],
    permissions: {},
  });
});

it("keeps and edits per-tool permissions when all Connection tools are selected", async () => {
  const user = userEvent.setup();
  const { submit } = editor(false, [
    {
      connection_id: "conn_0123456789abcdef",
      tools: null,
      permissions: { write: "ask" },
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Repository" }));
  await screen.findByRole("group", { name: "write permission" });
  await user.click(
    screen
      .getByRole("group", { name: "read permission" })
      .querySelector('button[aria-label="deny"]')!,
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].connection_tools[0]).toMatchObject({
    tools: null,
    permissions: { write: "ask", read: "deny" },
  });
});

it("retains saved tool selections when discovery fails", async () => {
  catalog.POST.mockRejectedValueOnce(new Error("Discovery unavailable"));
  const user = userEvent.setup();
  const { submit } = editor(false, [
    {
      connection_id: "mcp_0123456789abcdef",
      tools: ["legacy_tool"],
      permissions: { legacy_tool: "ask" },
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Web tools" }));
  expect(
    await screen.findByRole("checkbox", { name: "legacy_tool" }),
  ).toBeTruthy();
  expect(await screen.findByText("Discovery unavailable")).toBeTruthy();
  await user.click(
    screen.getByRole("checkbox", { name: "Load tools on demand" }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].connection_tools[0]).toMatchObject({
    tools: ["legacy_tool"],
    permissions: { legacy_tool: "ask" },
  });
});

it("shows discovery progress without a refresh action", async () => {
  let finishDiscovery!: (result: {
    data: { items: { name: string; description: string }[] };
  }) => void;
  catalog.POST.mockImplementationOnce(
    () => new Promise((resolve) => (finishDiscovery = resolve)),
  );
  const user = userEvent.setup();
  editor(false, [{ connection_id: "mcp_0123456789abcdef", tools: null }]);
  await waitFor(() => expect(catalog.POST).toHaveBeenCalledOnce());
  expect(screen.getByRole("status", { name: "Loading…" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Refresh tools/ })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Web tools" }));
  const demand = screen.getByText("Load tools on demand");
  const permission = screen.getByText("Default tool permission");
  expect(
    demand.compareDocumentPosition(permission) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  finishDiscovery({
    data: {
      items: [
        { name: "search", description: "Search" },
        { name: "read", description: "Read" },
      ],
    },
  });
  await screen.findByRole("checkbox", { name: "search" });
  expect(
    permission.compareDocumentPosition(screen.getByText("Search")) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(await screen.findByText("2 of 2 on")).toBeTruthy();
});

it("only offers ready Connections and omits their status from selected names", async () => {
  const user = userEvent.setup();
  catalog.POST.mockClear();
  editor();
  await user.click(screen.getByRole("button", { name: "Add Connections" }));
  expect(
    screen
      .getByRole("button", { name: "Disabled MCP — disabled" })
      .matches(":disabled"),
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Web tools" }));
  await waitFor(() => expect(catalog.POST).toHaveBeenCalledOnce());
  expect(await screen.findByText("2 of 2 on")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Web tools" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Web tools.*ready/ })).toBeNull();
});

it("keeps an existing on-demand preference while showing tools without search", async () => {
  const user = userEvent.setup();
  editor(false, [
    {
      connection_id: "mcp_0123456789abcdef",
      tools: null,
      defer_loading: false,
    },
  ]);
  await user.click(screen.getByRole("button", { name: "Web tools" }));
  await screen.findByRole("checkbox", { name: "search" });
  expect(
    screen
      .getByRole("checkbox", { name: "Load tools on demand" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(screen.queryByRole("searchbox", { name: "Search tools" })).toBeNull();
});

it.each([
  { tools: null, label: "All tools" },
  { tools: [], label: "No tools" },
  { tools: ["profile.read"], label: "profile.read" },
])(
  "preserves read-only Connector tool selection: $label",
  async ({ tools, label }) => {
    const user = userEvent.setup();
    const { submit } = editor(true, [
      {
        connection_id: "conn_0123456789abcdef",
        tools,
        defer_loading: true,
      },
    ]);
    await user.click(screen.getByRole("button", { name: "Repository" }));
    if (tools === null) {
      expect(
        screen
          .getByRole("checkbox", { name: "Enable Repository tools" })
          .getAttribute("aria-checked"),
      ).toBe("true");
    } else if (tools.length === 0) {
      expect(
        screen
          .getByRole("checkbox", { name: "Enable Repository tools" })
          .getAttribute("aria-checked"),
      ).toBe("false");
    } else {
      expect(await screen.findByRole("checkbox", { name: label })).toBeTruthy();
    }
    for (const checkbox of [
      screen.getByRole("checkbox", { name: "Enable Repository tools" }),
      screen.getByRole("checkbox", { name: "Load tools on demand" }),
    ]) {
      expect(checkbox.getAttribute("aria-disabled")).toBe("true");
      const checked = checkbox.getAttribute("aria-checked");
      await user.click(checkbox);
      expect(checkbox.getAttribute("aria-checked")).toBe(checked);
    }
    expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
    expect(submit).not.toHaveBeenCalled();
  },
);

it("offers file memory without a managed provider and preserves an untouched configuration", async () => {
  memoryAvailability.visible = false;
  const user = userEvent.setup();
  const { submit } = editor();
  expect(screen.getByRole("heading", { name: "Memory" })).toBeTruthy();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].memory).toBeUndefined();
});
it("retains a saved memory section and values when its backend is unavailable", async () => {
  memoryAvailability.visible = false;
  const user = userEvent.setup();
  const memory = {
    provider_id: "memprov_0123456789abcdef",
    auto_recall: false,
    recall_threshold: 0,
  };
  const { submit } = editor(false, [], memory);
  expect(screen.getByRole("heading", { name: "Memory" })).toBeTruthy();
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " More detail.",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit.mock.calls[0][0].memory).toEqual(memory);
});
