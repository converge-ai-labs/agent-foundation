// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Composer, ComposerDrafts, useDraft } from "./composer";
import { ThreadDraft } from "./draft";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";

let width = 860;
let mobile = false;
const mediaListeners = new Set<() => void>();
let resize: (width: number) => void;
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
      route: "custom:one",
      fast: { supported: true, state: "off" },
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
const transport = { client: { GET: get } } as unknown as Transport;

beforeEach(() => {
  width = 860;
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
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        if (!target.hasAttribute("data-compact")) return;
        resize = (next) =>
          this.callback(
            [{ target, contentRect: { width: next } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
          );
        resize(width);
      }
      unobserve() {}
      disconnect() {}
    },
  );
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
afterEach(() => {
  cleanup();
  query.clear();
  draft.doc.destroy();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function Input() {
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
          catalog={catalog}
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
          catalog={catalog}
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
function mount() {
  return render(
    <QueryClientProvider client={query}>
      <TransportContext value={transport}>
        <MemoryRouter>
          <ComposerDrafts value={new Map([["thread-responsive", draft]])}>
            <Input />
          </ComposerDrafts>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>,
  );
}

it.each([false, true])(
  "separates model settings from Run options without losing authored state (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
    width = 380;
    const user = userEvent.setup();
    mount();
    const editor = screen.getByRole("textbox", { name: "Message" });
    expect(screen.queryByRole("button", { name: "Model settings" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
    expect(
      screen.getByLabelText("Agent: Writer. Model: Model One"),
    ).toBeTruthy();
    act(() => draft.doc.getText("text").insert(0, "Keep this draft"));
    const modelTrigger = screen.getByRole("button", {
      name: "Agent & Model settings",
    });
    await user.click(modelTrigger);
    let panel = await screen.findByRole("dialog", { name: "Agent & Model" });
    expect(
      within(panel).queryByRole("button", { name: "Environments" }),
    ).toBeNull();
    expect(within(panel).queryByRole("button", { name: "Goal" })).toBeNull();
    await user.click(
      within(panel).getByRole("button", { name: "Reasoning mode" }),
    );
    await user.click(within(panel).getByRole("button", { name: "Standard" }));
    expect(draft.controls.reasoning_mode).toBe("standard");
    await user.click(within(panel).getByRole("button", { name: "Thinking" }));
    await user.click(within(panel).getByRole("button", { name: /Low/ }));
    await user.click(within(panel).getByRole("button", { name: "Fast mode" }));
    await user.click(within(panel).getByRole("button", { name: "On" }));
    expect(draft.controls).toEqual({
      thinking: "low",
      fast: true,
      reasoning_mode: "standard",
    });
    // The same panel and selections survive desktop/sheet transitions.
    act(() => {
      mobile = !mobile;
      for (const listener of mediaListeners) listener();
      resize(mobile ? 380 : 860);
    });
    panel = await screen.findByRole("dialog", { name: "Agent & Model" });
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(
      within(panel).getByRole("button", { name: "Thinking" }).textContent,
    ).toBe("ThinkingLow");
    await user.click(within(panel).getByRole("button", { name: "Model" }));
    await user.type(
      screen.getByRole("textbox", { name: "Search models" }),
      "custom:two",
    );
    await user.click(screen.getByRole("button", { name: "Model Two" }));
    expect(draft.modelId).toBe("two");
    expect(draft.controls).toEqual({});
    await user.click(
      screen.getByRole("button", { name: "Close Agent & Model settings" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Agent & Model settings" }),
    );
    act(() => resize(380));
    const trigger = screen.getByRole("button", { name: "Composer options" });
    await user.click(trigger);
    panel = await screen.findByRole("dialog", { name: "Run settings" });
    expect(within(panel).queryByRole("button", { name: "Model" })).toBeNull();
    await user.click(within(panel).getByRole("button", { name: "Goal" }));
    await user.click(
      within(panel).getByRole("button", { name: "Coordinator" }),
    );
    expect(draft.mode).toBe("goal");
    expect(draft.coordinator).toBe(true);
    await user.click(
      within(panel).getByRole("button", { name: "Environments" }),
    );
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(
      screen.getAllByText(/Full Control runs as the server account/).length,
    ).toBeGreaterThan(0);
    await user.click(screen.getByRole("combobox", { name: "Local mode" }));
    await user.click(screen.getByRole("option", { name: "Sandbox" }));
    expect(draft.environment?.environment_profile_id).toBeUndefined();
    await user.click(screen.getByRole("button", { name: "Use for next Run" }));
    expect(draft.environment?.environment_profile_id).toBe("sandbox");
    await user.click(
      screen.getByRole("button", { name: "Close run settings" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(trigger);
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(editor);
    expect(draft.doc.getText("text").toString()).toBe("Keep this draft");
    await user.click(screen.getByRole("button", { name: "Turn off Goal" }));
    await user.click(
      screen.getByRole("button", { name: "Turn off Coordinator" }),
    );
    expect(draft.mode).toBe("normal");
    expect(draft.coordinator).toBe(false);
    act(() => resize(860));
    expect(
      screen.getByLabelText("Agent: Writer. Model: Model Two"),
    ).toBeTruthy();
  },
);

it("edits working folders in-place, cancels without applying, and locks pending selections", async () => {
  mobile = true;
  width = 380;
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "Composer options" }));
  await user.click(screen.getByRole("button", { name: "Environments" }));
  expect(screen.getAllByRole("dialog")).toHaveLength(1);
  const folder = screen.getByDisplayValue("/workspace");
  await user.clear(folder);
  await user.type(folder, "/changed");
  await user.click(
    screen.getByRole("button", { name: "Back to run settings" }),
  );
  expect(draft.environment).toBeUndefined();
  await user.click(screen.getByRole("button", { name: "Environments" }));
  expect(screen.getByDisplayValue("/workspace")).toBeTruthy();
  const restoredFolder = screen.getByDisplayValue("/workspace");
  await user.clear(restoredFolder);
  await user.type(restoredFolder, "/accepted");
  await user.click(screen.getByRole("button", { name: "Use for next Run" }));
  expect(draft.environment?.local_roots).toEqual(["/accepted"]);
  await user.click(screen.getByRole("button", { name: "Close run settings" }));
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
  await user.click(screen.getByRole("button", { name: "Composer options" }));
  expect(
    (
      screen.getByRole("button", {
        name: "Environments",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("button", { name: "Goal" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});

it.each([false, true])(
  "preserves staged environments across the viewport breakpoint (mobile: %s)",
  async (onMobile) => {
    mobile = onMobile;
    width = 380;
    draft.environment = { environment_profile_id: "sandbox" };
    const user = userEvent.setup();
    mount();
    const editor = screen.getByRole("textbox", { name: "Message" });
    await user.click(screen.getByRole("button", { name: "Composer options" }));
    await user.click(screen.getByRole("button", { name: "Environments" }));
    const folder = screen.getByDisplayValue("/workspace");
    await user.clear(folder);
    await user.type(folder, "/staged");
    act(() => {
      mobile = !mobile;
      for (const listener of mediaListeners) listener();
      resize(mobile ? 380 : 860);
    });
    expect(await screen.findByDisplayValue("/staged")).toBeTruthy();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(draft.environment).toEqual({ environment_profile_id: "sandbox" });
    await user.click(screen.getByRole("button", { name: "Use for next Run" }));
    expect(draft.environment?.local_roots).toEqual(["/staged"]);
    expect(draft.environment?.environment_profile_id).toBe("sandbox");
    await user.click(
      screen.getByRole("button", { name: "Close run settings" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("textbox", { name: "Message" })).toBe(editor);
  },
);
