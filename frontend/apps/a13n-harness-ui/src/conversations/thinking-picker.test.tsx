// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Schema } from "../transport/client";
import { ThinkingPicker } from "./thinking-picker";
import { ThreadRunChoices } from "./thread-run-choices";

beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const model: Schema<"ModelSummary"> = {
  model_id: "custom-model",
  name: "Custom",
  route: "custom:provider-independent",
  thinking: {
    status: "supported",
    default_summary: "Budget · 8,192 tokens",
    options: [
      { value: null, label: "Model default", description: "Configured budget" },
      { value: false, label: "No thinking", description: "Disable thinking" },
      { value: "low", label: "Quick", description: "Budget: 2,048 tokens" },
      {
        value: "high",
        label: "Deep",
        description: "Budget: 16,384 tokens",
        disabled_reason: "Increase the output limit first",
      },
    ],
  },
};

it("renders backend labels and preserves false separately from default", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  const view = render(<ThinkingPicker model={model} onChange={onChange} />);
  await user.click(screen.getByRole("button", { name: /No thinking/ }));
  expect(onChange).toHaveBeenLastCalledWith(false);
  view.unmount();
  render(<ThinkingPicker model={model} value={false} onChange={onChange} />);
  expect(
    screen
      .getByRole("button", { name: /No thinking/ })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  const blocked = screen.getByRole("button", { name: /Deep/ });
  expect(blocked.hasAttribute("disabled")).toBe(true);
  await user.click(blocked);
  expect(onChange).toHaveBeenCalledTimes(1);
  await user.click(
    screen.getByRole("button", { name: "Use default thinking" }),
  );
  expect(onChange).toHaveBeenLastCalledWith(null);
});

it("resets thinking when a refreshed catalog changes the inherited Model", () => {
  const onThinkingChange = vi.fn();
  const catalog: Schema<"ThreadSelectorCatalog"> = {
    agents: [
      {
        agent_id: "agent",
        name: "Agent",
        model_id: model.model_id,
        source_path: "agents/agent.yaml",
      },
    ],
    models: [model],
    environments: [],
    harness_plugins: [],
    environment_run_extensions: [],
    mcp_servers: [],
  };
  const props = {
    catalog,
    agentId: "agent",
    controls: {
      thinking: "low" as const,
      fast: true,
      reasoning_mode: "pro" as const,
    },
    onControlsChange: onThinkingChange,
    disabled: false,
    onAgentChange: vi.fn(),
    onModelChange: vi.fn(),
  };
  const view = render(<ThreadRunChoices {...props} />);
  expect(onThinkingChange).not.toHaveBeenCalled();
  view.rerender(
    <ThreadRunChoices
      {...props}
      catalog={{
        ...catalog,
        agents: [{ ...catalog.agents[0], model_id: "another-model" }],
      }}
    />,
  );
  expect(onThinkingChange).toHaveBeenCalledWith({});
});

it("renders boolean controls without inventing effort levels", async () => {
  const user = userEvent.setup();
  const onChange = vi.fn();
  render(
    <ThinkingPicker
      model={{
        ...model,
        thinking: {
          status: "supported",
          default_summary: "Off",
          options: [
            {
              value: null,
              label: "Default",
              description: "Follow model settings",
            },
            { value: false, label: "Off", description: "Disable thinking" },
            { value: true, label: "On", description: "Enable thinking" },
          ],
        },
      }}
      onChange={onChange}
    />,
  );
  screen.getByRole("button", { name: /^Off/ }).focus();
  await user.keyboard("[ArrowRight][Space]");
  expect(onChange).toHaveBeenLastCalledWith(true);
  expect(screen.queryByRole("button", { name: "High" })).toBeNull();
});

it("tracks typed inherited levels on catalog refresh without materializing overrides", () => {
  const change = vi.fn();
  const withDefault = (
    default_value: false | "low" | null,
  ): Schema<"ModelSummary"> => ({
    ...model,
    thinking: { ...model.thinking!, default_value },
  });
  const view = render(
    <ThinkingPicker model={withDefault("low")} onChange={change} />,
  );
  const pressed = (name: string) =>
    screen.getByRole("button", { name }).getAttribute("aria-pressed");
  expect(pressed("Quick")).toBe("true");
  expect(pressed("Use default thinking")).toBe("true");
  view.rerender(
    <ThinkingPicker model={withDefault(false)} onChange={change} />,
  );
  expect(pressed("No thinking")).toBe("true");
  expect(pressed("Quick")).toBe("false");
  view.rerender(<ThinkingPicker model={withDefault(null)} onChange={change} />);
  expect(pressed("No thinking")).toBe("false");
  expect(pressed("Quick")).toBe("false");
  view.rerender(
    <ThinkingPicker
      model={withDefault("low")}
      value={false}
      onChange={change}
    />,
  );
  expect(pressed("No thinking")).toBe("true");
  expect(pressed("Quick")).toBe("false");
  expect(pressed("Use default thinking")).toBe("false");
  expect(change).not.toHaveBeenCalled();
});
