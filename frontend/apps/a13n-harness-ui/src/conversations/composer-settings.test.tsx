// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Composer, ComposerDrafts, useDraft } from "./composer";
import { ThreadDraft } from "./draft";
import { EditorView } from "@codemirror/view";
import { i18n } from "../i18n";
import { LanguageProvider, LanguageChoice } from "../i18n/language";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

let mobile = false;
const mediaListeners = new Set<() => void>();
let query: QueryClient;
let draft: ThreadDraft;
const catalog: Schema<"ThreadSelectorCatalog"> = {
  agents: [
    {
      agent_id: "writer",
      name: "Writer",
      model_id: "one",
      source_path: "agents/writer.yaml",
    },
  ],
  models: [
    {
      model_id: "one",
      name: "Model One",
      route: "openai-codex:gpt-6-astra",
      fast: { supported: true, state: "off", ultrafast_supported: true },
      reasoning_mode: { supported: true, state: "pro" },
      thinking: {
        status: "supported",
        default_summary: "High",
        default_value: "high",
        options: [
          {
            value: null,
            label: "Default",
            description: "Follow model settings",
          },
          { value: "low", label: "Low", description: "Lower effort" },
          { value: "high", label: "High", description: "Higher effort" },
        ],
      },
    },
    {
      model_id: "two",
      name: "Model Two",
      route: "custom:two",
      fast: { supported: false, state: "default" },
      thinking: { status: "unknown", default_summary: "Custom", options: [] },
    },
  ],
  environments: [
    {
      profile_id: "native",
      name: "Full Control",
      mode: "full-control",
      description: "Host account",
      provider_key: "native",
      release_owned: true,
      canonical_host_paths: true,
    },
    {
      profile_id: "sandbox",
      name: "Sandbox",
      mode: "sandbox",
      description: "Isolated environment",
      provider_key: "sandbox",
      release_owned: true,
      canonical_host_paths: false,
    },
  ],
  harness_plugins: [],
  environment_run_extensions: [],
  mcp_servers: [],
};
const configuration = {
  local_roots: ["/workspace"],
  environment_bindings: [],
  default_environment: "workspace",
  environment_profile_id: "native",
} as unknown as Schema<"ThreadConfiguration">;
const get = vi.fn(async () => ({ data: [] }));
const post = vi.fn();
const transport = { client: { GET: get, POST: post } } as unknown as Transport;

beforeEach(async () => {
  await i18n.changeLanguage("en");
  localStorage.clear();
  get.mockClear();
  post.mockClear();
  mobile = false;
  mediaListeners.clear();
  draft = new ThreadDraft();
  query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() {
      return mobile && query === "(max-width: 639px)";
    },
    addEventListener(_event: string, listener: () => void) {
      if (query === "(max-width: 639px)") mediaListeners.add(listener);
    },
    removeEventListener(_event: string, listener: () => void) {
      mediaListeners.delete(listener);
    },
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      width: 0,
      height: 0,
      bottom: 0,
      right: 0,
    }),
  });
});
afterEach(async () => {
  cleanup();
  query.clear();
  draft.doc.destroy();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  await i18n.changeLanguage("en");
});

function Input({
  selectors = catalog,
}: {
  selectors?: Schema<"ThreadSelectorCatalog">;
}) {
  const state = useDraft("thread-responsive");
  const disabled =
    state.submission.kind === "pending" || state.submission.kind === "unknown";
  return (
    <Composer
      local
      threadId="thread-responsive"
      activity={{ state: "inactive" }}
      coordinator={{ active: false, available: true }}
      profile={{ display_name: "Tester", color: "#000000" }}
      unauthorized={() => {}}
      reconcile={() => {}}
      leadingControls={
        <RunEnvironments
          catalog={selectors}
          configuration={configuration}
          value={state.environment}
          disabled={disabled}
          onChange={(next) => {
            state.environment = next;
            state.notify();
          }}
        />
      }
      controls={() => (
        <ThreadRunChoices
          catalog={selectors}
          agentId="writer"
          modelId={state.modelId}
          controls={state.controls}
          disabled={disabled}
          onAgentChange={() => {}}
          onModelChange={(next) => {
            state.modelId = next;
            state.controls = {};
            state.notify();
          }}
          onControlsChange={(next) => {
            state.controls = next;
            state.notify();
          }}
        />
      )}
    />
  );
}
function mount(selectors = catalog) {
  return render(
    <QueryClientProvider client={query}>
      <TransportContext value={transport}>
        <MemoryRouter>
          <ComposerDrafts value={new Map([["thread-responsive", draft]])}>
            <LanguageProvider>
              <LanguageChoice />
              <Input selectors={selectors} />
            </LanguageProvider>
          </ComposerDrafts>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}

it.each([false, true])(
  "keeps model/agent searches, editor state and explicit controls through language changes (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
    const user = userEvent.setup();
    draft.doc.getText("text").insert(0, "Keep 用户输入 and source_id");
    draft.modelId = "one";
    draft.controls = { thinking: "low", fast: false, reasoning_mode: "pro" };
    const originalControls = draft.controls;
    const selectors = {
      ...catalog,
      agents: [
        ...catalog.agents,
        ...Array.from({ length: 9 }, (_, index) => ({
          ...catalog.agents[0],
          agent_id: `agent-${index}`,
          name: `Agent ${index}`,
        })),
      ],
    };
    mount(selectors);
    const textbox = screen.getByRole("textbox", { name: "Message" });
    const editor = EditorView.findFromDOM(textbox)!;
    const document = draft.doc;
    const undo = draft.undo;
    act(() => editor.dispatch({ selection: { anchor: 5, head: 9 } }));
    const selection = editor.state.selection.toJSON();

    // Use the real preference control before entering the focus-trapping panel.
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "简体中文" }));
    await user.click(screen.getByRole("button", { name: "智能体与模型设置" }));
    expect(screen.getByRole("button", { name: "思考" }).textContent).toContain(
      "低",
    );
    expect(screen.getByRole("button", { name: "速度" }).textContent).toContain(
      "标准",
    );
    await user.click(screen.getByRole("button", { name: "模型" }));
    const search = screen.getByRole("textbox", {
      name: "搜索模型",
    }) as HTMLInputElement;
    await user.type(search, "two");
    search.setSelectionRange(1, 2);
    const reads = get.mock.calls.length;
    await act(() => i18n.changeLanguage("en"));
    expect(screen.getByRole("textbox", { name: "Search models" })).toBe(search);
    expect(search.value).toBe("two");
    expect([search.selectionStart, search.selectionEnd]).toEqual([1, 2]);
    expect(window.document.activeElement).toBe(search);
    expect(screen.getByRole("button", { name: "Model Two" })).toBeTruthy();
    expect(draft.controls).toEqual(originalControls);
    expect(draft.modelId).toBe("one");
    expect(get).toHaveBeenCalledTimes(reads);
    expect(post).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("button", { name: "Back to Agent & Model settings" }),
    );
    await user.click(screen.getByRole("button", { name: "Agent" }));
    const agents = screen.getByRole("textbox", {
      name: "Search agents",
    }) as HTMLInputElement;
    await user.type(agents, "writer");
    await act(() => i18n.changeLanguage("zh-CN"));
    expect(screen.getByRole("textbox", { name: "搜索智能体" })).toBe(agents);
    expect(agents.value).toBe("writer");
    expect(
      screen
        .getByRole("button", { name: /Writer\s*writer/ })
        .getAttribute("aria-pressed"),
    ).toBe("true");
    expect(draft.controls).toEqual(originalControls);

    await user.click(
      screen.getByRole("button", { name: "返回智能体与模型设置" }),
    );
    await user.click(screen.getByRole("button", { name: "思考" }));
    await user.click(screen.getByRole("button", { name: "高" }));
    expect(draft.controls).toEqual({
      thinking: "high",
      fast: false,
      reasoning_mode: "pro",
    });
    await user.click(
      screen.getByRole("button", { name: "返回智能体与模型设置" }),
    );
    await user.click(screen.getByRole("button", { name: "推理模式" }));
    await user.click(screen.getByRole("button", { name: "默认" }));
    expect(draft.controls).toEqual({
      thinking: "high",
      fast: false,
      reasoning_mode: null,
    });
    expect(
      screen.getByRole("button", { name: "推理模式" }).textContent,
    ).toContain("Pro - 模型默认");
    await user.click(screen.getByRole("button", { name: "速度" }));
    await user.click(screen.getByRole("button", { name: "使用模型速度设置" }));
    expect(draft.controls).toEqual({
      thinking: "high",
      fast: null,
      reasoning_mode: null,
    });
    await user.keyboard("[Escape]");
    expect(draft.doc).toBe(document);
    expect(draft.undo).toBe(undo);
    expect(draft.doc.getText("text").toString()).toBe(
      "Keep 用户输入 and source_id",
    );
    expect(EditorView.findFromDOM(textbox)).toBe(editor);
    expect(editor.state.selection.toJSON()).toEqual(selection);
    expect(draft.submission.kind).toBe("idle");
    expect(post).not.toHaveBeenCalled();
  },
);

it("preserves an unknown submission and locked choices when the language changes", async () => {
  const user = userEvent.setup();
  draft.modelId = "one";
  draft.controls = { fast: false, thinking: "low" };
  mount();
  await user.click(
    screen.getByRole("button", { name: "Agent & Model settings" }),
  );
  await user.click(screen.getByRole("button", { name: "Speed" }));
  act(() => {
    draft.submission = {
      kind: "unknown",
      action: "send",
      receipt: "receipt-original",
      message: "Check delivery first",
    };
    draft.notify();
  });
  const submission = draft.submission;
  const controls = draft.controls;
  const reads = get.mock.calls.length;
  await act(() => i18n.changeLanguage("zh-CN"));
  const standard = screen.getByRole("button", { name: "标准模式" });
  expect(standard).toHaveProperty("disabled", true);
  expect(standard.getAttribute("aria-pressed")).toBe("true");
  await user.click(standard);
  await act(() => i18n.changeLanguage("en"));
  expect(draft.submission).toBe(submission);
  expect(draft.controls).toEqual(controls);
  expect(draft.modelId).toBe("one");
  expect(get).toHaveBeenCalledTimes(reads);
  expect(post).not.toHaveBeenCalled();
});

it("keeps model overrides while navigating compact option pages on mobile", async () => {
  mobile = true;
  const user = userEvent.setup();
  mount();
  const trigger = screen.getByRole("button", {
    name: "Agent & Model settings",
  });
  expect(trigger.textContent).toBe("Model OneHigh");
  await user.click(trigger);
  expect(screen.getByRole("button", { name: "Thinking" }).textContent).toBe(
    "ThinkingHigh (default)",
  );
  await user.click(screen.getByRole("button", { name: "Thinking" }));
  expect(screen.getByText("High (default)")).toBeTruthy();
  expect(draft.controls.thinking).toBeUndefined();
  await user.click(screen.getByRole("button", { name: "High" }));
  expect(screen.queryByText("High (default)")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Use default thinking" }),
  );
  expect(screen.getByText("High (default)")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Low" }));
  expect(draft.controls).toEqual({ thinking: "low" });
  await user.click(
    screen.getByRole("button", { name: "Back to Agent & Model settings" }),
  );
  expect(
    screen.getByRole("button", { name: "Thinking" }).textContent,
  ).toContain("Low");
  await user.click(screen.getByRole("button", { name: "Speed" }));
  await user.click(screen.getByRole("button", { name: "Fast mode" }));
  expect(draft.controls).toEqual({ thinking: "low", fast: true });
  expect(trigger.getAttribute("aria-description")).toBe("Fast");
  await user.click(screen.getByRole("button", { name: "Speed" }));
  await user.click(screen.getByRole("button", { name: "Ultrafast mode" }));
  expect(draft.controls.fast).toBe("ultrafast");
  expect(trigger.getAttribute("aria-description")).toBe("Ultrafast");
  await user.click(screen.getByRole("button", { name: "Speed" }));
  await user.click(screen.getByRole("button", { name: "Standard mode" }));
  expect(draft.controls.fast).toBe(false);
  expect(trigger.getAttribute("aria-description")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Speed" }));
  await user.click(screen.getByRole("button", { name: "Use model speed" }));
  expect(draft.controls).toEqual({ thinking: "low", fast: null });
  await user.keyboard("[Escape]");
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Thinking" })).toBeNull(),
  );
  expect(draft.submission.kind).toBe("idle");
});

it("shows the inherited Ultrafast speed in the summary and picker", async () => {
  const user = userEvent.setup();
  render(
    <ThreadRunChoices
      catalog={{
        ...catalog,
        models: [
          {
            ...catalog.models![0],
            fast: {
              supported: true,
              state: "ultrafast",
              ultrafast_supported: true,
            },
          },
        ],
      }}
      agentId="writer"
      controls={{}}
      disabled={false}
      onAgentChange={() => {}}
      onModelChange={() => {}}
      onControlsChange={() => {}}
    />,
  );
  const trigger = screen.getByRole("button", {
    name: "Agent & Model settings",
  });
  expect(trigger.getAttribute("aria-description")).toBe("Ultrafast");
  await user.click(trigger);
  const speedRow = screen.getByRole("button", { name: "Speed" });
  expect(speedRow.textContent).toContain("Ultrafast");
  await user.click(speedRow);
  expect(
    screen.getByRole("button", { name: "Use model speed" }).textContent,
  ).toContain("Ultrafast");
});

it("distinguishes inherited and explicit agents with long names on mobile", async () => {
  mobile = true;
  const user = userEvent.setup();
  const onAgentChange = vi.fn();
  const name = "OpenAI GPT-6 Astra Coding";
  render(
    <ThreadRunChoices
      catalog={{
        ...catalog,
        agents: [{ ...catalog.agents[0], name }],
      }}
      agentId=""
      defaultAgentId="writer"
      disabled={false}
      controls={{}}
      onAgentChange={onAgentChange}
      onModelChange={() => {}}
      onControlsChange={() => {}}
    />,
  );
  await user.click(
    screen.getByRole("button", { name: "Agent & Model settings" }),
  );
  await user.click(screen.getByRole("button", { name: "Agent" }));
  const inherited = screen.getByRole("button", {
    name: /OpenAI GPT-6 Astra Coding\s*Default/,
  });
  const explicit = screen.getByRole("button", {
    name: /OpenAI GPT-6 Astra Coding\s*writer/,
  });
  expect(inherited.getAttribute("aria-pressed")).toBe("true");
  expect(explicit.getAttribute("aria-pressed")).toBe("false");
  await user.click(explicit);
  expect(onAgentChange).toHaveBeenCalledWith("writer");
  await user.click(screen.getByRole("button", { name: "Agent" }));
  await user.click(
    screen.getByRole("button", {
      name: /OpenAI GPT-6 Astra Coding\s*Default/,
    }),
  );
  expect(onAgentChange).toHaveBeenLastCalledWith("");
});

it("edits working folders in-place, cancels without applying, and locks pending selections", async () => {
  mobile = true;
  const user = userEvent.setup();
  mount();
  if (!screen.queryByRole("button", { name: "Environments" }))
    await user.click(screen.getByRole("button", { name: "Chat options" }));
  await user.click(screen.getByRole("button", { name: "Environments" }));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  await user.click(screen.getByTitle("/workspace"));
  const folder = screen.getByRole("textbox", { name: "Server directory" });
  await user.clear(folder);
  await user.type(folder, "/changed");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(draft.environment).toBeUndefined();
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Environments" })).toBeNull(),
  );
  if (!screen.queryByRole("button", { name: "Environments" }))
    await user.click(screen.getByRole("button", { name: "Chat options" }));
  await user.click(screen.getByRole("button", { name: "Environments" }));
  await user.click(screen.getByTitle("/workspace"));
  const restoredFolder = screen.getByRole("textbox", {
    name: "Server directory",
  });
  await user.clear(restoredFolder);
  await user.type(restoredFolder, "/accepted");
  await user.click(screen.getByRole("button", { name: "Use for next run" }));
  expect(draft.environment?.local_roots).toEqual(["/accepted"]);
  await user.click(
    screen.getByRole("button", { name: "Agent & Model settings" }),
  );
  await user.click(screen.getByRole("button", { name: "Model" }));
  act(() => {
    draft.submission = { kind: "pending", action: "send" };
    draft.notify();
  });
  expect(
    (screen.getByRole("button", { name: "Model Two" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(
    screen.getByRole("button", { name: "Close Agent & Model settings" }),
  );
  await user.click(screen.getByRole("button", { name: "Chat options" }));
  expect(
    (
      screen.getByRole("button", {
        name: "Environments",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    screen.getByRole("switch", { name: "Goal" }).getAttribute("aria-disabled"),
  ).toBe("true");
});

it.each([false, true])(
  "preserves staged environments across the viewport breakpoint (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
    draft.environment = { environment_profile_id: "sandbox" };
    const user = userEvent.setup();
    mount();
    const editor = screen.getByRole("textbox", { name: "Message" });
    if (!screen.queryByRole("button", { name: "Environments" }))
      await user.click(screen.getByRole("button", { name: "Chat options" }));
    await user.click(screen.getByRole("button", { name: "Environments" }));
    await user.click(screen.getByTitle("/workspace"));
    const folder = screen.getByRole("textbox", { name: "Server directory" });
    await user.clear(folder);
    await user.type(folder, "/staged");
    act(() => {
      mobile = !mobile;
      for (const listener of mediaListeners) listener();
    });
    expect(await screen.findByDisplayValue("/staged")).toBeTruthy();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(draft.environment).toEqual({ environment_profile_id: "sandbox" });
    await user.click(screen.getByRole("button", { name: "Use for next run" }));
    expect(draft.environment?.local_roots).toEqual(["/staged"]);
    expect(draft.environment?.environment_profile_id).toBe("sandbox");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Environments" })).toBeNull(),
    );
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(editor);
  },
);

it("keeps saved ACP option mutations mounted across subpage navigation and resolves model ID icons on the trigger", async () => {
  const { AcpOptions, useAcpOptions } = await import("./acp-options");
  let rejectPut!: (reason?: unknown) => void;
  const put = vi.fn(
    () =>
      new Promise<never>((_resolve, reject) => {
        rejectPut = reject;
      }),
  );
  const acpCatalog: Schema<"ThreadSelectorCatalog"> = {
    ...catalog,
    agents: [
      ...catalog.agents,
      {
        kind: "acp_agent",
        agent_id: "acp-agy",
        name: "Antigravity",
        preset: "antigravity",
        source_path: "acp_agents/agy.yaml",
      },
    ],
  };
  const acpTransport = {
    client: {
      GET: vi.fn(async () => ({
        data: [
          {
            id: "model",
            name: "Model",
            category: "model",
            value: "gemini-pro-orchestrator-neon",
            choices: [
              {
                name: "Orchestrator (Neon)",
                value: "gemini-pro-orchestrator-neon",
              },
              { name: "Claude Sonnet 5.5", value: "claude-sonnet-5-5" },
            ],
          },
        ],
      })),
      PUT: put,
      POST: post,
    },
  } as unknown as Transport;

  function Harness() {
    const options = useAcpOptions("thread-acp", true, false, "acp-agy", "c1");
    const modelOpt = options.data?.find((item) => item.id === "model");
    return (
      <ThreadRunChoices
        catalog={acpCatalog}
        agentId="acp-agy"
        controls={{}}
        onControlsChange={() => {}}
        disabled={false}
        onAgentChange={() => {}}
        onModelChange={() => {}}
        externalModel={
          modelOpt?.choices?.find((c) => c.value === modelOpt.value)?.name
        }
        externalModelId={modelOpt ? String(modelOpt.value) : undefined}
        externalOptions={
          <AcpOptions
            threadId="thread-acp"
            active={false}
            agentId="acp-agy"
            preset="antigravity"
            continuation="c1"
            options={options}
          />
        }
      />
    );
  }

  const user = userEvent.setup();
  render(
    <LanguageProvider>
      <MemoryRouter>
        <QueryClientProvider client={query}>
          <TransportContext value={acpTransport}>
            <Harness />
          </TransportContext>
        </QueryClientProvider>
      </MemoryRouter>
    </LanguageProvider>,
  );
  const trigger = await screen.findByRole("button", {
    name: "Agent & Model settings",
  });
  await waitFor(() =>
    expect(trigger.textContent).toContain("Orchestrator (Neon)"),
  );
  expect(trigger.querySelector("img")?.getAttribute("src")).toContain(
    "gemini-color.svg",
  );
  await user.click(trigger);
  await user.click(await screen.findByRole("button", { name: "Model" }));
  await user.click(
    await screen.findByRole("button", { name: "Claude Sonnet 5.5" }),
  );
  // Returned to root while PUT is still pending: Model row remains disabled!
  const modelRow = (await screen.findByRole("button", {
    name: "Model",
  })) as HTMLButtonElement;
  expect(modelRow.disabled).toBe(true);
  await act(async () => {
    rejectPut(new Error("ACP option rejected"));
  });
  expect(await screen.findByText(/ACP option rejected/)).toBeTruthy();
});

it("shows a loading indicator on the ACP trigger while externalLoading is active and isolates option mutations across scopes", async () => {
  const { useAcpOptionChange } = await import("./acp-options");
  let rejectFirst!: (reason?: unknown) => void;
  const put = vi.fn(
    () =>
      new Promise<never>((_resolve, reject) => {
        rejectFirst = reject;
      }),
  );
  const acpCatalog: Schema<"ThreadSelectorCatalog"> = {
    ...catalog,
    agents: [
      ...catalog.agents,
      {
        kind: "acp_agent",
        agent_id: "acp-one",
        name: "Antigravity",
        preset: "antigravity",
        source_path: "acp_agents/agy.yaml",
      },
      {
        kind: "acp_agent",
        agent_id: "acp-two",
        name: "Codex ACP",
        preset: "codex",
        source_path: "acp_agents/codex.yaml",
      },
    ],
  };
  const acpTransport = {
    client: { PUT: put, GET: get, POST: post },
  } as unknown as Transport;

  function ScopedHarness({
    agentId,
    active = false,
    loading = false,
  }: {
    agentId: string;
    active?: boolean;
    loading?: boolean;
  }) {
    const change = useAcpOptionChange("thread-scope", active, agentId, "c1");
    return (
      <>
        <ThreadRunChoices
          catalog={acpCatalog}
          agentId={agentId}
          controls={{}}
          onControlsChange={() => {}}
          disabled={false}
          onAgentChange={() => {}}
          onModelChange={() => {}}
          externalLoading={loading || change.isPending}
        />
        <button
          type="button"
          onClick={() => change.mutate({ id: "model", value: "next" })}
        >
          Trigger change
        </button>
        <output data-testid="state">
          {JSON.stringify({
            pending: change.isPending,
            error: change.error ? String(change.error) : null,
          })}
        </output>
      </>
    );
  }

  const wrap = (node: React.ReactNode) => (
    <LanguageProvider>
      <MemoryRouter>
        <QueryClientProvider client={query}>
          <TransportContext value={acpTransport}>{node}</TransportContext>
        </QueryClientProvider>
      </MemoryRouter>
    </LanguageProvider>
  );

  const user = userEvent.setup();
  const view = render(wrap(<ScopedHarness agentId="acp-one" loading />));
  const trigger = screen.getByRole("button", {
    name: "Agent & Model settings",
  });
  expect(trigger.querySelector("[data-acp-loading]")).toBeTruthy();

  view.rerender(wrap(<ScopedHarness agentId="acp-one" loading={false} />));
  expect(trigger.querySelector("[data-acp-loading]")).toBeNull();

  await user.click(screen.getByRole("button", { name: "Trigger change" }));
  expect(screen.getByTestId("state").textContent).toContain('"pending":true');
  expect(trigger.querySelector("[data-acp-loading]")).toBeTruthy();

  // Switching agent immediately clears pending/error from the old agent scope.
  view.rerender(wrap(<ScopedHarness agentId="acp-two" />));
  expect(screen.getByTestId("state").textContent).toBe(
    '{"pending":false,"error":null}',
  );
  expect(trigger.querySelector("[data-acp-loading]")).toBeNull();

  await act(async () => {
    rejectFirst(new Error("Old agent error"));
  });
  expect(screen.getByTestId("state").textContent).toBe(
    '{"pending":false,"error":null}',
  );
});

it("refreshes draft skill preview with coordinator=true when enabling Coordinate agents", async () => {
  const user = userEvent.setup();
  post.mockImplementation(
    async (
      path: string,
      init?: { params?: { query?: { coordinator?: boolean } } },
    ) => {
      if (path !== "/api/threads/skills-preview") return { data: {} };
      const coordinator = init?.params?.query?.coordinator === true;
      return {
        data: {
          catalog_id: coordinator ? "c".repeat(64) : "a".repeat(64),
          context_kind: "draft",
          items: coordinator
            ? [
                {
                  item_id: "b".repeat(64),
                  name: "coordinate-worktree-pr",
                  description: "Guide worker PR",
                  source_id: "a13n-harness-ui:builtin-coordinator-skills",
                  logical_path:
                    "/environment/builtin-coordinator-skills/coordinate-worktree-pr",
                },
              ]
            : [],
        },
      };
    },
  );
  draft.doc.getText("text").insert(0, "$coordinate-worktree-pr");
  mount();

  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/threads/skills-preview",
      expect.not.objectContaining({ params: expect.anything() }),
    ),
  );
  expect(
    document.querySelector('[data-skill="coordinate-worktree-pr"]'),
  ).toBeNull();

  await user.click(screen.getByRole("button", { name: "Chat options" }));
  await user.click(screen.getByRole("switch", { name: "Coordinate agents" }));

  await waitFor(() =>
    expect(post).toHaveBeenCalledWith(
      "/api/threads/skills-preview",
      expect.objectContaining({ params: { query: { coordinator: true } } }),
    ),
  );
  await waitFor(() =>
    expect(
      document.querySelector('[data-skill="coordinate-worktree-pr"]'),
    ).toBeTruthy(),
  );
});
