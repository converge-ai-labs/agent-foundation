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
      canRun
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

it.each([false, true])(
  "keeps model overrides while navigating compact option pages (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
    const user = userEvent.setup();
    mount();
    await user.click(
      screen.getByRole("button", { name: "Agent & Model settings" }),
    );
    await user.click(screen.getByRole("button", { name: "Thinking" }));
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
    await user.click(screen.getByRole("button", { name: "Speed" }));
    await user.click(screen.getByRole("button", { name: "Standard mode" }));
    expect(draft.controls.fast).toBe(false);
    await user.click(screen.getByRole("button", { name: "Speed" }));
    await user.click(screen.getByRole("button", { name: "Use model speed" }));
    expect(draft.controls).toEqual({ thinking: "low", fast: null });
    await user.keyboard("[Escape]");
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Thinking" })).toBeNull(),
    );
    expect(draft.submission.kind).toBe("idle");
  },
);

it.each([
  ["default", "Standard"],
  ["off", "Standard"],
  ["on", "Fast"],
  ["ultrafast", "Ultrafast"],
] as const)(
  "resolves inherited speed %s as %s in the settings summary",
  async (state, label) => {
    const user = userEvent.setup();
    const change = vi.fn();
    render(
      <ThreadRunChoices
        catalog={{
          ...catalog,
          models: [
            {
              ...catalog.models![0],
              fast: { supported: true, state, ultrafast_supported: true },
            },
          ],
        }}
        agentId="writer"
        controls={{}}
        disabled={false}
        onAgentChange={() => {}}
        onModelChange={() => {}}
        onControlsChange={change}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "Agent & Model settings" }),
    );
    expect(screen.getByRole("button", { name: "Speed" }).textContent).toBe(
      `Speed${label}`,
    );
    await user.click(screen.getByRole("button", { name: "Speed" }));
    expect(
      screen.getByRole("button", { name: "Use model speed" }).textContent,
    ).toBe(`Model setting - ${label}`);
    await user.click(screen.getByRole("button", { name: "Standard mode" }));
    expect(change).toHaveBeenLastCalledWith({ fast: false });
  },
);

it.each([false, true])(
  "distinguishes inherited and explicit agents with long names (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
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
  },
);

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
